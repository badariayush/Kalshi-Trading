from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from kalshi_algo.config import load_config
from kalshi_algo.models import TradeEvent
from kalshi_algo.signals import SignalEngine
from kalshi_algo.state import MarketState


class SignalEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config("config/default.toml")
        self.engine = SignalEngine(self.config)
        self.state = MarketState()
        self.book = self.state.get_orderbook("FIN-TEST")
        self.book.yes_bids = {0.49: 20, 0.48: 30}
        self.book.yes_asks = {0.51: 20, 0.52: 30}
        self.book.updated_at = datetime.now(UTC)

    def test_signal_requires_cluster(self) -> None:
        base = datetime.now(UTC)
        self.book.updated_at = base
        for idx in range(1):
            trade = TradeEvent(
                market_ticker="FIN-TEST",
                side="YES",
                price=0.50,
                size=200000,
                timestamp=base + timedelta(seconds=idx),
                market_volume=5_000_000,
                category="financial",
                close_time=base + timedelta(hours=1),
            )
            decision = self.engine.process_trade(trade, self.book, self.state)
            self.assertIsNone(decision.candidate)
            self.assertEqual(decision.rejection_reason, "cluster_below_threshold")

    def test_signal_accepts_on_second_trade(self) -> None:
        base = datetime.now(UTC)
        self.book.updated_at = base + timedelta(seconds=2)
        result = None
        for idx in range(2):
            trade = TradeEvent(
                market_ticker="FIN-TEST",
                side="YES",
                price=0.50,
                size=200000,
                timestamp=base + timedelta(seconds=idx),
                market_volume=5_000_000,
                category="financial",
                close_time=base + timedelta(hours=1),
            )
            result = self.engine.process_trade(trade, self.book, self.state)
        assert result is not None
        self.assertIsNotNone(result.candidate)
        self.assertEqual(result.candidate.signal_strength, "strong")

    def test_blocks_moderate_mid_price_entries(self) -> None:
        base = datetime.now(UTC)
        self.book.updated_at = base + timedelta(seconds=2)
        result = None
        for idx in range(2):
            trade = TradeEvent(
                market_ticker="FIN-TEST",
                side="YES",
                price=0.40,
                size=200000,
                timestamp=base + timedelta(seconds=idx),
                market_volume=100_000_000,
                category="financial",
                close_time=base + timedelta(hours=1),
            )
            result = self.engine.process_trade(trade, self.book, self.state)
        assert result is not None
        self.assertIsNone(result.candidate)
        self.assertEqual(result.rejection_reason, "moderate_mid_price_block")


if __name__ == "__main__":
    unittest.main()
