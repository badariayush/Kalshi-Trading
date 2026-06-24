from __future__ import annotations

import unittest

from kalshi_crypto.market_lifecycle import (
    RawKalshiMarketMessage,
    lifecycle_events_from_market,
)


class MarketLifecycleTests(unittest.TestCase):
    def test_open_market_emits_discovered_and_opened_events(self) -> None:
        market = RawKalshiMarketMessage(
            market_ticker="KXBTCD-TEST",
            series_ticker="KXBTC15M",
            underlying="BTC",
            strike="102500",
            lifecycle_status="open",
            open_timestamp_ms=1_000,
            close_timestamp_ms=901_000,
            received_timestamp_ms=2_000,
        )

        events = lifecycle_events_from_market(market)

        self.assertEqual(
            [event.event_type for event in events],
            ["WindowDiscovered", "WindowOpened"],
        )
        self.assertEqual(events[0].payload["market_ticker"], "KXBTCD-TEST")
        self.assertEqual(events[0].payload["close_timestamp_ms"], 901_000)
        self.assertEqual(events[1].causality_id, events[0].event_id)

    def test_closing_market_emits_closing_soon_event(self) -> None:
        market = RawKalshiMarketMessage(
            market_ticker="KXBTCD-TEST",
            series_ticker="KXBTC15M",
            underlying="BTC",
            strike="102500",
            lifecycle_status="closing",
            open_timestamp_ms=1_000,
            close_timestamp_ms=901_000,
            received_timestamp_ms=890_000,
        )

        events = lifecycle_events_from_market(market)

        self.assertEqual(
            [event.event_type for event in events],
            ["WindowDiscovered", "WindowClosingSoon"],
        )
        self.assertEqual(events[1].payload["time_to_close_ms"], 11_000)

    def test_closed_market_emits_window_closed_event(self) -> None:
        market = RawKalshiMarketMessage(
            market_ticker="KXBTCD-TEST",
            series_ticker="KXBTC15M",
            underlying="BTC",
            strike="102500",
            lifecycle_status="closed",
            open_timestamp_ms=1_000,
            close_timestamp_ms=901_000,
            received_timestamp_ms=902_000,
        )

        events = lifecycle_events_from_market(market)

        self.assertEqual(
            [event.event_type for event in events],
            ["WindowDiscovered", "WindowClosed"],
        )

    def test_rejects_invalid_market_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "close_timestamp_ms"):
            RawKalshiMarketMessage(
                market_ticker="KXBTCD-TEST",
                series_ticker="KXBTC15M",
                underlying="BTC",
                strike="102500",
                lifecycle_status="open",
                open_timestamp_ms=1_000,
                close_timestamp_ms=1_000,
                received_timestamp_ms=1_000,
            )


if __name__ == "__main__":
    unittest.main()
