from __future__ import annotations

import unittest

from kalshi_crypto.events import AuditEvent
from kalshi_crypto.market_lifecycle import RawKalshiMarketMessage, lifecycle_events_from_market
from kalshi_crypto.market_state import MarketWindowRegistry


def _market_events(
    *,
    market_ticker: str,
    lifecycle_status: str,
    open_timestamp_ms: int,
    close_timestamp_ms: int,
    received_timestamp_ms: int,
    underlying: str = "BTC",
    strike: str = "102500",
) -> tuple[AuditEvent, ...]:
    return lifecycle_events_from_market(
        RawKalshiMarketMessage(
            market_ticker=market_ticker,
            series_ticker=f"KX{underlying}15M",
            underlying=underlying,
            strike=strike,
            lifecycle_status=lifecycle_status,
            open_timestamp_ms=open_timestamp_ms,
            close_timestamp_ms=close_timestamp_ms,
            received_timestamp_ms=received_timestamp_ms,
        )
    )


class MarketWindowRegistryTests(unittest.TestCase):
    def test_projects_current_and_next_window_for_underlying(self) -> None:
        registry = MarketWindowRegistry.empty()
        current_events = _market_events(
            market_ticker="KXBTCD-CURRENT",
            lifecycle_status="open",
            open_timestamp_ms=1_000,
            close_timestamp_ms=901_000,
            received_timestamp_ms=2_000,
        )
        next_events = _market_events(
            market_ticker="KXBTCD-NEXT",
            lifecycle_status="upcoming",
            open_timestamp_ms=901_000,
            close_timestamp_ms=1_801_000,
            received_timestamp_ms=2_000,
            strike="103000",
        )

        updated = registry.apply_all((*current_events, *next_events))

        self.assertEqual(
            [window.market_ticker for window in updated.current_windows("BTC", 10_000)],
            ["KXBTCD-CURRENT"],
        )
        next_window = updated.next_window("BTC", 10_000)
        self.assertIsNotNone(next_window)
        self.assertEqual(next_window.market_ticker, "KXBTCD-NEXT")

    def test_apply_returns_new_registry_without_mutating_original(self) -> None:
        registry = MarketWindowRegistry.empty()
        events = _market_events(
            market_ticker="KXBTCD-CURRENT",
            lifecycle_status="open",
            open_timestamp_ms=1_000,
            close_timestamp_ms=901_000,
            received_timestamp_ms=2_000,
        )

        updated = registry.apply_all(events)

        self.assertIsNone(registry.window("KXBTCD-CURRENT"))
        self.assertIsNotNone(updated.window("KXBTCD-CURRENT"))

    def test_closing_and_closed_events_update_status(self) -> None:
        registry = MarketWindowRegistry.empty().apply_all(
            _market_events(
                market_ticker="KXBTCD-CURRENT",
                lifecycle_status="closing",
                open_timestamp_ms=1_000,
                close_timestamp_ms=901_000,
                received_timestamp_ms=890_000,
            )
        )

        closing_window = registry.window("KXBTCD-CURRENT")
        self.assertIsNotNone(closing_window)
        self.assertEqual(closing_window.status, "closing")
        self.assertEqual(
            [window.market_ticker for window in registry.current_windows("BTC", 890_000)],
            ["KXBTCD-CURRENT"],
        )

        closed = registry.apply_all(
            _market_events(
                market_ticker="KXBTCD-CURRENT",
                lifecycle_status="closed",
                open_timestamp_ms=1_000,
                close_timestamp_ms=901_000,
                received_timestamp_ms=902_000,
            )
        )

        closed_window = closed.window("KXBTCD-CURRENT")
        self.assertIsNotNone(closed_window)
        self.assertEqual(closed_window.status, "closed")
        self.assertEqual(closed.current_windows("BTC", 902_000), ())

    def test_rejects_lifecycle_event_missing_required_payload(self) -> None:
        event = AuditEvent.create(
            event_type="WindowDiscovered",
            worker="market_monitor",
            payload={
                "market_ticker": "KXBTCD-BAD",
                "underlying": "BTC",
                "open_timestamp_ms": 1_000,
                "close_timestamp_ms": 901_000,
            },
            causality_id="test",
            timestamp_ms=1_000,
        )

        with self.assertRaisesRegex(ValueError, "series_ticker"):
            MarketWindowRegistry.empty().apply(event)


if __name__ == "__main__":
    unittest.main()
