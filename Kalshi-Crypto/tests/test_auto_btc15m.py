from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.auto_btc15m import run_auto_btc_15m_live_data
from kalshi_crypto.config import load_app_config
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.live_collectors import LiveDataSummary
from kalshi_crypto.market_discovery import discover_next_btc_15m_market
from kalshi_crypto.storage import SQLiteAuditStore


CURRENT_MARKET = {
    "event_ticker": "KXBTC15M-26JUN250030",
    "ticker": "KXBTC15M-26JUN250030-30",
    "status": "active",
    "open_time": "2026-06-25T04:15:00Z",
    "close_time": "2026-06-25T04:30:00Z",
    "floor_strike": 100000,
}
NEXT_MARKET = {
    "event_ticker": "KXBTC15M-26JUN250045",
    "ticker": "KXBTC15M-26JUN250045-45",
    "status": "initialized",
    "open_time": "2026-06-25T04:30:00Z",
    "close_time": "2026-06-25T04:45:00Z",
    "floor_strike": 100000,
}
FOLLOWING_MARKET = {
    "event_ticker": "KXBTC15M-26JUN250100",
    "ticker": "KXBTC15M-26JUN250100-00",
    "status": "initialized",
    "open_time": "2026-06-25T04:45:00Z",
    "close_time": "2026-06-25T05:00:00Z",
    "floor_strike": 100000,
}


class AutoBtc15mTests(unittest.TestCase):
    def test_discovers_next_btc_15m_market_without_using_current_market(self) -> None:
        discovery = discover_next_btc_15m_market(
            now_ms=_ms("2026-06-25T04:20:00Z"),
            fetch_json=_fetcher_for_static_markets,
        )

        self.assertEqual(discovery.current_market.ticker, CURRENT_MARKET["ticker"])
        self.assertEqual(discovery.next_market.ticker, NEXT_MARKET["ticker"])
        self.assertEqual(str(discovery.next_market.strike), "100000")

    def test_discovers_nested_custom_strike_for_future_paper_market(self) -> None:
        nested_next = {
            key: value for key, value in NEXT_MARKET.items() if key != "floor_strike"
        }
        nested_next["custom_strike"] = {"floor_strike": "100000.25"}

        def fetch_json(url: str) -> dict[str, object]:
            if "status=open" in url:
                return {"events": [{"markets": [CURRENT_MARKET]}]}
            if "KXBTC15M-26JUN250045" in url:
                return {"event": {"markets": [nested_next]}}
            return {"events": [{"markets": [nested_next, FOLLOWING_MARKET]}]}

        discovery = discover_next_btc_15m_market(
            now_ms=_ms("2026-06-25T04:20:00Z"),
            fetch_json=fetch_json,
        )

        self.assertEqual(discovery.next_market.ticker, NEXT_MARKET["ticker"])
        self.assertEqual(discovery.next_market.strike, Decimal("100000.25"))

    def test_auto_runner_skips_current_market_and_runs_next_once(self) -> None:
        now_ms = _ms("2026-06-25T04:20:00Z")
        run_calls: list[tuple[str, int, Path | None]] = []

        def clock() -> int:
            return now_ms

        def sleeper(seconds: float) -> None:
            nonlocal now_ms
            now_ms += int(seconds * 1000)

        def fetch_json(url: str) -> dict[str, object]:
            if "status=open" in url:
                market = CURRENT_MARKET if now_ms < _ms("2026-06-25T04:30:00Z") else NEXT_MARKET
                return {"events": [{"markets": [market]}]}
            if "KXBTC15M-26JUN250045" in url:
                market = (
                    {**NEXT_MARKET, "result": "yes"}
                    if now_ms >= _ms("2026-06-25T04:45:00Z")
                    else NEXT_MARKET
                )
                return {"event": {"markets": [market]}}
            if "KXBTC15M-26JUN250100" in url:
                return {"event": {"markets": [FOLLOWING_MARKET]}}
            return {"events": [{"markets": [NEXT_MARKET, FOLLOWING_MARKET]}]}

        def market_runner(**kwargs: object) -> LiveDataSummary:
            nonlocal now_ms
            ticker = tuple(kwargs["kalshi_market_tickers"])[0]
            max_seconds = int(kwargs["max_seconds"])
            output_file = kwargs["output_file"]
            audit_db = Path(kwargs["audit_db"])
            SQLiteAuditStore(audit_db).append(
                AuditEvent.create(
                    event_type="SimulatedOrderPlaced",
                    worker="execution",
                    payload={
                        "market_ticker": ticker,
                        "side": "yes",
                        "price": "0.48",
                        "quantity": 1,
                        "execution": "simulated_print_only",
                        "order_submission": "disabled",
                    },
                    causality_id="test",
                    timestamp_ms=now_ms,
                )
            )
            SQLiteAuditStore(audit_db).append(
                AuditEvent.create(
                    event_type="SimulatedOrderPlaced",
                    worker="execution",
                    payload={
                        "market_ticker": ticker,
                        "side": "no",
                        "price": "0.40",
                        "quantity": 1,
                        "reason": "partial_hedge",
                        "leg_index": 2,
                        "execution": "simulated_print_only",
                        "order_submission": "disabled",
                    },
                    causality_id="test",
                    timestamp_ms=now_ms + 1,
                )
            )
            run_calls.append((ticker, max_seconds, output_file))
            now_ms += max_seconds * 1000
            return LiveDataSummary(
                raw_messages=2,
                kalshi_messages=1,
                coinbase_messages=1,
                feed_unhealthy_events=0,
                simulated_orders=2,
                audit_events=6,
                network="attempted",
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = StringIO()
            summary = run_auto_btc_15m_live_data(
                config=load_app_config("configs/live.example.toml"),
                audit_db=Path(tmpdir) / "live.sqlite3",
                max_seconds=1_800,
                market_capture_seconds=60,
                max_markets=1,
                output_file=Path(tmpdir) / "capture.jsonl",
                stdout=stdout,
                clock=clock,
                sleeper=sleeper,
                fetch_json=fetch_json,
                market_runner=market_runner,
            )

        self.assertEqual(run_calls[0][0], NEXT_MARKET["ticker"])
        self.assertEqual(run_calls[0][1], 60)
        self.assertIn(NEXT_MARKET["ticker"], str(run_calls[0][2]))
        self.assertEqual(summary.markets_run, 1)
        self.assertEqual(summary.simulated_orders, 2)
        self.assertEqual(summary.simulated_positions_closed, 1)
        self.assertEqual(str(summary.simulated_total_pnl), "0.12")
        self.assertEqual(summary.last_market_ticker, NEXT_MARKET["ticker"])
        self.assertEqual(summary.next_market_ticker, FOLLOWING_MARKET["ticker"])
        self.assertIn(
            f"auto_skipped_current_market_ticker={CURRENT_MARKET['ticker']}",
            stdout.getvalue(),
        )
        self.assertIn(
            f"auto_prepared_following_market_ticker={FOLLOWING_MARKET['ticker']}",
            stdout.getvalue(),
        )
        self.assertIn("simulated_position_closed=", stdout.getvalue())

    def test_auto_runner_recovers_and_settles_unresolved_position(self) -> None:
        now_ms = _ms("2026-06-25T04:20:00Z")

        def fetch_json(url: str) -> dict[str, object]:
            if "status=open" in url:
                return {"events": [{"markets": [CURRENT_MARKET]}]}
            if "KXBTC15M-26JUN250045" in url:
                return {"event": {"markets": [NEXT_MARKET]}}
            if "KXBTC15M-26JUN250015" in url:
                recovered = {
                    "event_ticker": "KXBTC15M-26JUN250015",
                    "ticker": "KXBTC15M-26JUN250015-15",
                    "status": "settled",
                    "result": "yes",
                    "open_time": "2026-06-25T04:00:00Z",
                    "close_time": "2026-06-25T04:15:00Z",
                    "floor_strike": 100000,
                }
                return {"event": {"markets": [recovered]}}
            return {"events": [{"markets": [NEXT_MARKET]}]}

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "live.sqlite3"
            SQLiteAuditStore(audit_db).append(
                AuditEvent.create(
                    event_type="SimulatedOrderPlaced",
                    worker="execution",
                    payload={
                        "market_ticker": "KXBTC15M-26JUN250015-15",
                        "event_ticker": "KXBTC15M-26JUN250015",
                        "market_open_time_ms": _ms("2026-06-25T04:00:00Z"),
                        "market_close_time_ms": _ms("2026-06-25T04:15:00Z"),
                        "market_strike": "100000",
                        "side": "yes",
                        "price": "0.40",
                        "quantity": 1,
                        "fee": "0.02",
                        "leg_index": 1,
                    },
                    causality_id="recovered",
                    timestamp_ms=_ms("2026-06-25T04:01:00Z"),
                )
            )

            summary = run_auto_btc_15m_live_data(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=60,
                fetch_json=fetch_json,
                clock=lambda: now_ms,
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(summary.simulated_positions_closed, 1)
        self.assertEqual(summary.simulated_total_pnl, Decimal("0.58"))

    def test_auto_runner_does_not_resettle_take_profit_closed_position(self) -> None:
        now_ms = _ms("2026-06-25T04:20:00Z")

        def clock() -> int:
            return now_ms

        def sleeper(seconds: float) -> None:
            nonlocal now_ms
            now_ms += int(seconds * 1000)

        def market_runner(**kwargs: object) -> LiveDataSummary:
            nonlocal now_ms
            ticker = tuple(kwargs["kalshi_market_tickers"])[0]
            audit_db = Path(kwargs["audit_db"])
            store = SQLiteAuditStore(audit_db)
            store.append(
                AuditEvent.create(
                    event_type="SimulatedOrderPlaced",
                    worker="execution",
                    payload={
                        "market_ticker": ticker,
                        "side": "yes",
                        "price": "0.40",
                        "quantity": 1,
                        "fee": "0.02",
                        "leg_index": 1,
                    },
                    causality_id=ticker,
                    timestamp_ms=now_ms,
                )
            )
            store.append(
                AuditEvent.create(
                    event_type="PositionClosed",
                    worker="execution",
                    payload={
                        "market_ticker": ticker,
                        "realized_pnl": "0.10",
                        "total_fees": "0.04",
                        "exit_reason": "take_profit",
                    },
                    causality_id=ticker,
                    timestamp_ms=now_ms + 1,
                )
            )
            now_ms += int(kwargs["max_seconds"]) * 1000
            return LiveDataSummary(
                raw_messages=2,
                kalshi_messages=1,
                coinbase_messages=1,
                feed_unhealthy_events=0,
                simulated_orders=1,
                audit_events=6,
                network="attempted",
                simulated_positions_closed=1,
                simulated_realized_pnl=Decimal("0.10"),
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "live.sqlite3"
            summary = run_auto_btc_15m_live_data(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=1_800,
                market_capture_seconds=60,
                max_markets=1,
                clock=clock,
                sleeper=sleeper,
                fetch_json=_fetcher_for_static_markets,
                market_runner=market_runner,
            )
            closed_events = [
                record
                for record in SQLiteAuditStore(audit_db).read_all()
                if record["event_type"] == "PositionClosed"
            ]

        self.assertEqual(summary.simulated_positions_closed, 1)
        self.assertEqual(summary.simulated_total_pnl, Decimal("0.10"))
        self.assertEqual(len(closed_events), 1)


def _fetcher_for_static_markets(url: str) -> dict[str, object]:
    if "status=open" in url:
        return {"events": [{"markets": [CURRENT_MARKET]}]}
    if "KXBTC15M-26JUN250045" in url:
        return {"event": {"markets": [NEXT_MARKET]}}
    return {"events": [{"markets": [NEXT_MARKET, FOLLOWING_MARKET]}]}


def _ms(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)


if __name__ == "__main__":
    unittest.main()
