from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import asyncio
import os
from typing import Any

from arb_bot.config import AppConfig
from arb_bot.matching.pair_scorer import auto_pair_markets, top_pair_candidates
from arb_bot.models import MarketPair, OrderBook, VenueMarket
from arb_bot.risk.limits import PortfolioState, RiskLimits, check_risk
from arb_bot.storage.db import ArbDatabase
from arb_bot.strategy.arbitrage import ArbSettings, find_opportunities
from arb_bot.venues.kalshi import KalshiConnector
from arb_bot.venues.polymarket import PolymarketConnector
from arb_bot.runtime.websockets import monitor_kalshi_market_ws, monitor_polymarket_market_ws


@dataclass(frozen=True, slots=True)
class PaperRunnerResult:
    db_path: Path
    markets_discovered: int
    pairs_discovered: int
    opportunities: int
    paper_positions: int
    websocket_messages: int = 0


class LiveDataPaperRunner:
    def __init__(self, config: AppConfig, db_path: str | Path) -> None:
        self.config = config
        self.db = ArbDatabase(db_path)
        self.db_path = Path(db_path)
        self.polymarket = PolymarketConnector()
        self.kalshi = KalshiConnector()
        self.portfolio = PortfolioState()
        self.stop = asyncio.Event()
        self.markets_discovered = 0
        self.pairs_discovered = 0
        self.opportunities = 0
        self.paper_positions = 0
        self.websocket_messages = 0
        self._known_pair_ids: set[str] = set()
        self._paired: list[tuple[MarketPair, VenueMarket, VenueMarket]] = []

    async def run(self, max_seconds: float | None = None, asset_query: str = "crypto") -> PaperRunnerResult:
        self.db.record_event("runner", "info", "live-data paper runner starting", {"asset_query": asset_query})
        poly_markets, kalshi_markets = await self._discover_and_pair(asset_query)

        ws_tasks = self._start_websocket_tasks(self._paired, poly_markets, kalshi_markets)
        poll_task = asyncio.create_task(self._poll_pairs(), name="paper-poll-pairs")
        rediscovery_task = asyncio.create_task(self._rediscover_loop(asset_query), name="paper-rediscovery")
        try:
            if max_seconds is None:
                await poll_task
            else:
                await asyncio.wait_for(poll_task, timeout=max_seconds)
        except TimeoutError:
            self.db.record_event("runner", "info", "max seconds reached; stopping paper runner", {"max_seconds": max_seconds})
        finally:
            self.stop.set()
            poll_task.cancel()
            rediscovery_task.cancel()
            for task in ws_tasks:
                task.cancel()
            await asyncio.gather(poll_task, rediscovery_task, *ws_tasks, return_exceptions=True)
            self.db.record_event("runner", "info", "live-data paper runner stopped", None)
            self.db.close()

        return PaperRunnerResult(
            db_path=self.db_path,
            markets_discovered=self.markets_discovered,
            pairs_discovered=self.pairs_discovered,
            opportunities=self.opportunities,
            paper_positions=self.paper_positions,
            websocket_messages=self.websocket_messages,
        )

    async def _discover_and_pair(self, asset_query: str) -> tuple[list[VenueMarket], list[VenueMarket]]:
        discovery_results = await asyncio.gather(
            asyncio.to_thread(self.polymarket.discover_markets, self.config.runtime.discovery_limit, asset_query),
            asyncio.to_thread(self.kalshi.discover_markets, self.config.runtime.discovery_limit, asset_query),
            return_exceptions=True,
        )
        poly_markets = self._markets_or_log("polymarket", discovery_results[0])
        kalshi_markets = self._markets_or_log("kalshi", discovery_results[1])
        for market in [*poly_markets, *kalshi_markets]:
            self.db.record_market(market)
        self.markets_discovered = max(self.markets_discovered, len(poly_markets) + len(kalshi_markets))

        paired = auto_pair_markets(
            poly_markets,
            kalshi_markets,
            min_confidence=self.config.runtime.min_pair_confidence,
            max_pairs=self.config.runtime.max_pairs,
        )
        new_pairs = 0
        for pair, poly_market, kalshi_market in paired:
            self.db.record_pair(pair)
            if pair.pair_id not in self._known_pair_ids:
                self._known_pair_ids.add(pair.pair_id)
                self._paired.append((pair, poly_market, kalshi_market))
                new_pairs += 1
        for confidence, poly_market, kalshi_market in top_pair_candidates(poly_markets, kalshi_markets, limit=25):
            self.db.record_pair_candidate(
                poly_market,
                kalshi_market,
                str(confidence),
                confidence >= self.config.runtime.min_pair_confidence,
            )
        self.pairs_discovered = len(self._known_pair_ids)
        self.db.record_event(
            "pairing",
            "info",
            "market discovery and pairing completed",
            {
                "query": asset_query,
                "polymarket": len(poly_markets),
                "kalshi": len(kalshi_markets),
                "pairs": len(paired),
                "new_pairs": new_pairs,
            },
        )
        return poly_markets, kalshi_markets

    async def _rediscover_loop(self, asset_query: str) -> None:
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.config.runtime.rediscovery_interval_seconds)
                return
            except TimeoutError:
                await self._discover_and_pair(asset_query)

    async def _poll_pairs(self) -> None:
        if not self._paired:
            self.db.record_event("pairing", "warning", "no market pairs found; runner idling", None)
        settings = ArbSettings(
            min_net_edge=self.config.strategy.min_net_edge,
            slippage_buffer=self.config.strategy.slippage_buffer,
            min_trade_size=self.config.strategy.min_trade_size,
            max_book_age_seconds=self.config.runtime.book_freshness_seconds,
            min_pair_confidence=self.config.runtime.min_pair_confidence,
        )
        limits = RiskLimits(
            max_notional_per_leg=self.config.risk.max_notional_per_leg,
            max_total_exposure=self.config.risk.max_total_exposure,
            max_pair_exposure=self.config.risk.max_pair_exposure,
            max_venue_exposure=self.config.risk.max_venue_exposure,
            daily_loss_limit=self.config.risk.daily_loss_limit,
            max_consecutive_losses=self.config.risk.max_consecutive_losses,
            kill_switch_file=self.config.risk.kill_switch_file,
        )

        while not self.stop.is_set():
            for pair, poly_market, kalshi_market in list(self._paired):
                await self._scan_pair(pair, poly_market, kalshi_market, settings, limits)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.config.runtime.poll_interval_seconds)
            except TimeoutError:
                pass

    async def _scan_pair(
        self,
        pair: MarketPair,
        poly_market: VenueMarket,
        kalshi_market: VenueMarket,
        settings: ArbSettings,
        limits: RiskLimits,
    ) -> None:
        try:
            poly_book, kalshi_book = await asyncio.gather(
                asyncio.to_thread(self.polymarket.fetch_order_book, poly_market),
                asyncio.to_thread(self.kalshi.fetch_order_book, kalshi_market),
            )
            self._record_books_seen(pair, poly_book, kalshi_book)
            opportunities = find_opportunities(pair, poly_book, kalshi_book, settings)
            for opp in opportunities:
                self.opportunities += 1
                opportunity_id = self.db.record_opportunity(opp)
                decision = check_risk(opp, self.portfolio, limits)
                if not decision.allowed:
                    self.db.record_event(
                        "risk_reject",
                        "info",
                        f"opportunity rejected: {decision.reason}",
                        {"pair_id": pair.pair_id, "direction": opp.direction},
                    )
                    continue
                position_id = self.db.record_open_paper_position(opp, opportunity_id)
                self.paper_positions += 1
                self.db.record_event(
                    "paper_entry",
                    "info",
                    "paper position opened from live market data",
                    {
                        "position_id": position_id,
                        "pair_id": pair.pair_id,
                        "direction": opp.direction,
                        "size": str(opp.size),
                        "net_edge": str(opp.net_edge),
                        "expected_profit": str(opp.expected_profit),
                    },
                )
        except Exception as exc:
            self.db.record_event(
                "pair_scan_error",
                "warning",
                f"pair scan failed: {exc.__class__.__name__}",
                {"pair_id": pair.pair_id, "error": str(exc)[:500]},
            )

    def _start_websocket_tasks(
        self,
        paired: list[tuple[MarketPair, VenueMarket, VenueMarket]],
        poly_markets: list[VenueMarket],
        kalshi_markets: list[VenueMarket],
    ) -> list[asyncio.Task[None]]:
        poly_asset_ids: list[str] = []
        kalshi_tickers: list[str] = []
        for _, poly_market, kalshi_market in paired:
            if poly_market.yes_token_id:
                poly_asset_ids.append(poly_market.yes_token_id)
            if poly_market.no_token_id:
                poly_asset_ids.append(poly_market.no_token_id)
            kalshi_tickers.append(kalshi_market.market_id)
        if not paired:
            for poly_market in poly_markets[: self.config.runtime.max_pairs]:
                if poly_market.yes_token_id:
                    poly_asset_ids.append(poly_market.yes_token_id)
                if poly_market.no_token_id:
                    poly_asset_ids.append(poly_market.no_token_id)
            kalshi_tickers = [market.market_id for market in kalshi_markets[: self.config.runtime.max_pairs]]
        tasks = [
            asyncio.create_task(
                monitor_polymarket_market_ws(poly_asset_ids, self._record_event, self.stop),
                name="polymarket-websocket-monitor",
            ),
            asyncio.create_task(
                monitor_kalshi_market_ws(kalshi_tickers, self._record_event, self.stop, self._kalshi_headers_factory()),
                name="kalshi-websocket-monitor",
            ),
        ]
        return tasks

    def _kalshi_headers_factory(self):
        key_id = os.getenv("KALSHI_API_KEY_ID")
        private_key_path = _resolve_path(os.getenv("KALSHI_PRIVATE_KEY_PATH") or self.config.kalshi_key_file)
        if not key_id or not private_key_path.exists():
            return None

        def headers() -> dict[str, str]:
            timestamp_ms = str(int(datetime.now(UTC).timestamp() * 1000))
            path = "/trade-api/ws/v2"
            message = timestamp_ms + "GET" + path
            signature = _sign_pss_text(private_key_path, message)
            return {
                "KALSHI-ACCESS-KEY": key_id,
                "KALSHI-ACCESS-SIGNATURE": signature,
                "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            }

        return headers

    def _markets_or_log(self, venue: str, result: object) -> list[VenueMarket]:
        if isinstance(result, Exception):
            self.db.record_event(
                "discovery_error",
                "warning",
                f"{venue} discovery failed: {result.__class__.__name__}",
                {"error": str(result)[:500]},
            )
            return []
        return result if isinstance(result, list) else []

    def _record_event(self, event_type: str, severity: str, message: str, payload: dict[str, object] | None = None) -> None:
        if event_type == "websocket_message":
            self.websocket_messages += 1
        self.db.record_event(event_type, severity, message, payload)

    def _record_books_seen(self, pair: MarketPair, poly_book: OrderBook, kalshi_book: OrderBook) -> None:
        self.db.record_event(
            "books_seen",
            "debug",
            "order book snapshots fetched",
            {
                "pair_id": pair.pair_id,
                "polymarket_yes_asks": len(poly_book.yes_asks),
                "polymarket_no_asks": len(poly_book.no_asks),
                "kalshi_yes_asks": len(kalshi_book.yes_asks),
                "kalshi_no_asks": len(kalshi_book.no_asks),
            },
        )


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _sign_pss_text(private_key_path: Path, text: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return b64encode(signature).decode("utf-8")
