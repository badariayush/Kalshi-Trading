from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections.abc import Callable
from typing import Any
import asyncio
import json

from .client import KalshiClient
from .config import AppConfig
from .execution import VirtualExecutionEngine
from .models import ActionEvent, TradeEvent, utc_now
from .persistence import EventStore
from .reporting import generate_session_report
from .risk import RiskEngine
from .signals import SignalDecision, SignalEngine
from .state import MarketState


class StrategyEngine:
    def __init__(self, config: AppConfig, store: EventStore):
        self.config = config
        self.store = store
        self.state = MarketState()
        self.signal_engine = SignalEngine(config)
        from .models import SessionStats

        self.risk_engine = RiskEngine(config, SessionStats(starting_cash=config.portfolio.starting_cash))
        self.execution_engine = VirtualExecutionEngine(config)
        self.live_ticker_cache: dict[str, dict[str, Any]] = {}
        self.latest_prices: dict[tuple[str, str], float] = {}
        self.accepting_new_entries = True

    async def run_live(
        self,
        client: KalshiClient,
        max_seconds: float | None = None,
        drain_on_timeout: bool = False,
        drain_requested: Callable[[], bool] | None = None,
        force_liquidation_requested: Callable[[], bool] | None = None,
    ) -> None:
        deadline = datetime.now(UTC) + timedelta(seconds=max_seconds) if max_seconds is not None else None
        drain_started = False
        async for payload in client.stream_events(
            reconnect=True,
            initial_delay_seconds=self.config.environment.reconnect_initial_delay_seconds,
            max_delay_seconds=self.config.environment.reconnect_max_delay_seconds,
        ):
            if force_liquidation_requested is not None and force_liquidation_requested():
                for event in self.liquidate_all("keyboard_interrupt_liquidation"):
                    self.emit(event)
                break
            if drain_requested is not None and drain_requested():
                drain_started = True
                self.accepting_new_entries = False
                if not self.state.positions:
                    break
            if deadline is not None and datetime.now(UTC) >= deadline:
                if drain_on_timeout:
                    drain_started = True
                    self.accepting_new_entries = False
                    if not self.state.positions:
                        break
                else:
                    for event in self.liquidate_all("max_seconds_liquidation"):
                        self.emit(event)
                    break
            for event in self.process_payload(payload):
                self.emit(event)
            if drain_started and not self.state.positions:
                break

    async def run_replay_file(self, input_path: str | Path) -> None:
        path = Path(input_path)
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            for event in self.process_payload(payload):
                self.emit(event)
            await asyncio.sleep(0)

    def process_payload(self, payload: dict[str, Any]) -> list[ActionEvent]:
        channel = payload.get("channel") or payload.get("type")
        body = payload.get("msg") if isinstance(payload.get("msg"), dict) else payload
        if channel in {"orderbook_snapshot", "orderbook_delta"}:
            self._handle_orderbook(body, payload)
            return []
        if channel in {"ticker", "price_tick"}:
            return self._handle_price_tick(body, payload)
        if channel == "trade":
            return self._handle_trade(body, payload)
        if channel in {"subscribed", "ok"}:
            return []
        if channel == "connection_status":
            msg = payload.get("msg") if isinstance(payload.get("msg"), dict) else {}
            return [
                ActionEvent(
                    event_type="INFO",
                    timestamp=utc_now(),
                    market_ticker=None,
                    side=None,
                    price=None,
                    size=None,
                    signal_strength=None,
                    confidence_level=None,
                    reason=f"websocket_{msg.get('status', 'status')}",
                    metadata={"payload": payload},
                )
            ]
        if channel == "error":
            msg = payload.get("msg") if isinstance(payload.get("msg"), dict) else {}
            return [
                ActionEvent(
                    event_type="INFO",
                    timestamp=utc_now(),
                    market_ticker=None,
                    side=None,
                    price=None,
                    size=None,
                    signal_strength=None,
                    confidence_level=None,
                    reason=f"websocket_error:{msg.get('code', 'unknown')}",
                    metadata={"payload": payload},
                )
            ]
        if channel == "disconnect":
            self.risk_engine.halt("websocket_disconnect")
            return [self.risk_engine.halt_event("websocket_disconnect")]
        return []

    def _handle_orderbook(self, payload: dict[str, Any], original: dict[str, Any] | None = None) -> None:
        ticker = payload.get("market_ticker")
        if ticker is None:
            return
        book = self.state.get_orderbook(ticker)
        if "yes_bids" in payload or "yes_asks" in payload:
            book.yes_bids.clear()
            book.yes_asks.clear()
            for price, qty in payload.get("yes_bids", []):
                book.yes_bids[float(price)] = max(1, int(float(qty)))
            for price, qty in payload.get("yes_asks", []):
                book.yes_asks[float(price)] = max(1, int(float(qty)))
        elif "yes_dollars_fp" in payload:
            book.yes_bids.clear()
            book.yes_asks.clear()
            for price, qty in payload.get("yes_dollars_fp", []):
                book.yes_bids[float(price)] = max(1, int(float(qty)))
            for price, qty in payload.get("no_dollars_fp", []):
                yes_ask = 1 - float(price)
                book.yes_asks[yes_ask] = max(1, int(float(qty)))
        elif "price_dollars" in payload and "delta_fp" in payload:
            side = str(payload.get("side", "yes")).lower()
            yes_price = float(payload["price_dollars"])
            quantity = max(1, int(abs(float(payload["delta_fp"]))))
            target = book.yes_bids if side == "yes" else book.yes_asks
            if float(payload["delta_fp"]) <= 0:
                target.pop(yes_price, None)
            else:
                target[yes_price] = quantity
        source = original or payload
        book.updated_at = self._parse_ts(
            payload.get("time") or payload.get("ts") or source.get("timestamp") or source.get("received_at")
        )

    def _handle_trade(self, payload: dict[str, Any], original: dict[str, Any] | None = None) -> list[ActionEvent]:
        # Replay fixtures use a flat payload shape; live Kalshi WS uses type/msg envelopes.
        if {"market_ticker", "side", "price", "size", "market_volume", "category", "close_time"}.issubset(payload):
            trade = TradeEvent(
                market_ticker=str(payload["market_ticker"]),
                side=str(payload["side"]),
                price=float(payload["price"]),
                size=int(payload["size"]),
                timestamp=self._parse_ts(payload["timestamp"]),
                market_volume=float(payload["market_volume"]),
                category=str(payload["category"]),
                close_time=self._parse_ts(payload["close_time"]),
            )
        else:
            ticker = payload.get("market_ticker")
            if ticker is None:
                return [
                    ActionEvent(
                        event_type="INFO",
                        timestamp=utc_now(),
                        market_ticker=None,
                        side=None,
                        price=None,
                        size=None,
                        signal_strength=None,
                        confidence_level=None,
                        reason="trade_missing_market_ticker",
                        metadata={"payload": original or payload},
                    )
                ]

            cached = self.live_ticker_cache.get(ticker)
            if cached is None:
                return [
                    ActionEvent(
                        event_type="INFO",
                        timestamp=utc_now(),
                        market_ticker=ticker,
                        side=None,
                        price=None,
                        size=None,
                        signal_strength=None,
                        confidence_level=None,
                        reason="trade_waiting_for_ticker_metadata",
                        metadata={"payload": original or payload},
                    )
                ]

            taker_side = str(payload.get("taker_side", "yes")).lower()
            side = "YES" if taker_side == "yes" else "NO"
            if side == "YES":
                price = float(payload.get("yes_price_dollars") or 0)
            else:
                price = float(payload.get("no_price_dollars") or 0)
            size = max(1, int(float(payload.get("count_fp") or 0)))
            trade = TradeEvent(
                market_ticker=ticker,
                side=side,
                price=price,
                size=size,
                timestamp=self._parse_ts(payload.get("created_time") or payload.get("time") or payload.get("ts")),
                market_volume=float(cached["market_volume"]),
                category=str(cached["category"]),
                close_time=self._parse_ts(cached["close_time"]),
            )
        self.latest_prices[(trade.market_ticker, trade.side)] = trade.price
        if not self.accepting_new_entries:
            return [
                ActionEvent(
                    event_type="REJECT",
                    timestamp=utc_now(),
                    market_ticker=trade.market_ticker,
                    side=trade.side,
                    price=trade.price,
                    size=trade.size,
                    signal_strength=None,
                    confidence_level=None,
                    reason="shutdown_draining",
                )
            ]
        orderbook = self.state.get_orderbook(trade.market_ticker)
        decision: SignalDecision = self.signal_engine.process_trade(trade, orderbook, self.state)
        if decision.candidate is None:
            return [
                ActionEvent(
                    event_type="REJECT",
                    timestamp=utc_now(),
                    market_ticker=trade.market_ticker,
                    side=trade.side,
                    price=trade.price,
                    size=trade.size,
                    signal_strength=None,
                    confidence_level=None,
                    reason=decision.rejection_reason or "rejected",
                )
            ]
        proposed_size = self.execution_engine.determine_position_size(
            decision.candidate,
            bankroll=self.risk_engine.stats.cash,
        )
        if proposed_size <= 0:
            return [self.execution_engine.enter(decision.candidate, size=proposed_size).event]
        expected_exit_price = orderbook.expected_exit_price(trade.side, proposed_size)
        best_bid = orderbook.best_bid()
        best_ask = orderbook.best_ask()
        entry_spread = (
            best_ask - best_bid
            if best_bid is not None and best_ask is not None
            else None
        )
        risk = self.risk_engine.evaluate_entry(
            decision.candidate,
            self.state,
            expected_exit_price,
            entry_spread=entry_spread,
            proposed_size=proposed_size,
        )
        if not risk.allowed:
            return [
                ActionEvent(
                    event_type="REJECT",
                    timestamp=utc_now(),
                    market_ticker=trade.market_ticker,
                    side=trade.side,
                    price=trade.price,
                    size=trade.size,
                    signal_strength=decision.candidate.signal_strength,
                    confidence_level=decision.candidate.confidence_level,
                    reason=risk.reason,
                )
            ]
        entry = self.execution_engine.enter(decision.candidate, size=proposed_size)
        if entry.accepted and entry.position is not None:
            entry.event.metadata["orderbook"] = self._orderbook_snapshot(orderbook, trade, expected_exit_price)
            self.state.positions[entry.position.position_id] = entry.position
            self.state.record_entry(decision.candidate.market_ticker, decision.candidate.timestamp)
        return [entry.event]

    def _handle_price_tick(self, payload: dict[str, Any], original: dict[str, Any] | None = None) -> list[ActionEvent]:
        market_ticker = payload.get("market_ticker")
        if market_ticker is None:
            return []
        yes_price = self._extract_yes_price(payload)
        self.latest_prices[(market_ticker, "YES")] = yes_price
        self.latest_prices[(market_ticker, "NO")] = self._price_for_side(payload, "NO", yes_price)
        self.live_ticker_cache[market_ticker] = {
            "market_volume": float(payload.get("dollar_volume") or payload.get("volume") or 0),
            # Live WS ticker updates do not include category/close_time, so v1 uses safe defaults
            # until REST metadata hydration is added.
            "category": "financial",
            "close_time": "2099-12-31T23:59:59+00:00",
        }
        book = self.state.get_orderbook(market_ticker)
        yes_bid = payload.get("yes_bid_dollars")
        yes_ask = payload.get("yes_ask_dollars")
        if yes_bid is not None:
            bid_size = max(1, int(float(payload.get("yes_bid_size_fp") or 1)))
            book.yes_bids = {float(yes_bid): bid_size}
        if yes_ask is not None:
            ask_size = max(1, int(float(payload.get("yes_ask_size_fp") or 1)))
            book.yes_asks = {float(yes_ask): ask_size}
        source = original or payload
        book.updated_at = self._parse_ts(
            payload.get("time") or payload.get("ts") or source.get("timestamp") or source.get("received_at")
        )
        events: list[ActionEvent] = []
        for position_id, position in list(self.state.positions.items()):
            if position.market_ticker != market_ticker:
                continue
            price = self._price_for_side(payload, position.side, yes_price)
            self.latest_prices[(market_ticker, position.side)] = price
            self.execution_engine.update_position(position, price)
            counter_count = self.state.cluster_count(market_ticker, "NO" if position.side == "YES" else "YES")
            self.execution_engine.apply_counter_signal(position, counter_count)
            exit_reason = self.execution_engine.should_exit(position, price)
            if exit_reason is None:
                continue
            exit_event = self.execution_engine.exit(position, price, exit_reason)
            events.append(exit_event)
            self._record_market_exit(position)
            del self.state.positions[position_id]
            events.extend(self.risk_engine.update_after_exit(position))
        return events

    def emit(self, event: ActionEvent) -> None:
        print(self.format_event(event), flush=True)
        self.store.append(event)

    def _record_market_exit(self, position: Any) -> None:
        if position.exit_time is None:
            return
        self.state.record_market_exit(
            position.market_ticker,
            position.exit_time,
            position.pnl(),
            self.config.portfolio.loss_count_threshold,
            self.config.portfolio.market_loss_cooldown_count,
            self.config.portfolio.market_loss_cooldown_seconds,
        )

    def liquidate_all(self, reason: str) -> list[ActionEvent]:
        events: list[ActionEvent] = []
        for position_id, position in list(self.state.positions.items()):
            price = self.latest_prices.get((position.market_ticker, position.side), position.entry_price)
            exit_event = self.execution_engine.exit(position, price, reason)
            exit_event.metadata["liquidation"] = True
            events.append(exit_event)
            self._record_market_exit(position)
            del self.state.positions[position_id]
            events.extend(self.risk_engine.update_after_exit(position))
        return events

    @staticmethod
    def _extract_yes_price(payload: dict[str, Any]) -> float:
        return float(
            payload.get("yes_price")
            or payload.get("yes_price_dollars")
            or payload.get("price")
            or payload.get("price_dollars")
            or payload.get("yes_bid_dollars")
            or payload.get("yes_ask_dollars")
            or 0
        )

    @staticmethod
    def _price_for_side(payload: dict[str, Any], side: str, yes_price: float) -> float:
        if side == "YES":
            return yes_price
        no_price = payload.get("no_price") or payload.get("no_price_dollars")
        if no_price is not None:
            return float(no_price)
        return 1 - yes_price

    @staticmethod
    def _orderbook_snapshot(orderbook: Any, trade: TradeEvent, expected_exit_price: float | None) -> dict[str, Any]:
        best_bid = orderbook.best_bid()
        best_ask = orderbook.best_ask()
        age_seconds = None
        if orderbook.updated_at is not None:
            age_seconds = max(0.0, (trade.timestamp - orderbook.updated_at).total_seconds())
        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": (best_ask - best_bid) if best_bid is not None and best_ask is not None else None,
            "expected_exit_price": expected_exit_price,
            "age_seconds": age_seconds,
        }

    @staticmethod
    def format_event(event: ActionEvent) -> str:
        market = event.market_ticker or "-"
        side = event.side or "-"
        price = f"{event.price:.4f}" if event.price is not None else "-"
        size = str(event.size) if event.size is not None else "-"
        return (
            f"{event.timestamp.isoformat()} "
            f"{event.event_type} market={market} side={side} price={price} "
            f"size={size} reason={event.reason}"
        )

    @staticmethod
    def _parse_ts(value: Any) -> datetime:
        if value is None:
            return datetime.now(UTC)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), UTC)
        if isinstance(value, str) and value.isdigit():
            return datetime.fromtimestamp(float(value), UTC)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.now(UTC)


def run_report(config: AppConfig, db_path: str | Path) -> str:
    del config
    with EventStore(db_path) as store:
        return generate_session_report(store).render()
