from __future__ import annotations

from datetime import UTC, datetime
import unittest

from kalshi_algo.config import load_config
from kalshi_algo.models import SessionStats, SignalCandidate, VirtualPosition
from kalshi_algo.risk import RiskEngine
from kalshi_algo.state import MarketState


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config("config/default.toml")
        self.stats = SessionStats(starting_cash=self.config.portfolio.starting_cash)
        self.engine = RiskEngine(self.config, self.stats)
        self.state = MarketState()

    def test_risk_blocks_when_liquidity_missing(self) -> None:
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="moderate",
            confidence_level="tradable",
            price=0.50,
            size_ratio=0.04,
            cluster_count=3,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )
        decision = self.engine.evaluate_entry(candidate, self.state, expected_exit_price=None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "insufficient_liquidity")

    def test_consecutive_losses_halt(self) -> None:
        for _ in range(self.config.portfolio.consecutive_loss_halt_count):
            position = VirtualPosition.create(
                market_ticker="FIN-TEST",
                side="YES",
                entry_price=0.50,
                size=1,
                signal_strength="moderate",
                confidence_level="tradable",
                entry_time=datetime.now(UTC),
                stop_price=0.40,
                take_profit_price=0.60,
                reason="qualified_signal",
            )
            position.exit_price = 0.40
            position.exit_reason = "stop_loss"
            position.exit_time = datetime.now(UTC)
            events = self.engine.update_after_exit(position)
        self.assertTrue(self.engine.halted)
        self.assertTrue(any(event.reason == "consecutive_loss_limit" for event in events))

    def test_tiny_loss_does_not_count_toward_loss_frequency(self) -> None:
        position = VirtualPosition.create(
            market_ticker="FIN-TEST",
            side="YES",
            entry_price=0.50,
            size=1,
            signal_strength="moderate",
            confidence_level="tradable",
            entry_time=datetime.now(UTC),
            stop_price=0.40,
            take_profit_price=0.60,
            reason="qualified_signal",
        )
        position.exit_price = 0.49
        position.exit_reason = "stop_loss"
        position.exit_time = datetime.now(UTC)

        events = self.engine.update_after_exit(position)

        self.assertEqual(events, [])
        self.assertEqual(self.stats.daily_losses, 0)
        self.assertEqual(self.stats.consecutive_losses, 0)
        self.assertAlmostEqual(self.stats.realized_pnl, -0.01)

    def test_loss_frequency_halt_requires_negative_realized_pnl(self) -> None:
        self.config.portfolio.consecutive_loss_halt_count = 999
        self.stats.realized_pnl = 1.0
        self.stats.cash = self.stats.starting_cash + self.stats.realized_pnl
        for _ in range(self.config.portfolio.daily_loss_frequency_limit):
            position = VirtualPosition.create(
                market_ticker="FIN-TEST",
                side="YES",
                entry_price=0.50,
                size=1,
                signal_strength="moderate",
                confidence_level="tradable",
                entry_time=datetime.now(UTC),
                stop_price=0.40,
                take_profit_price=0.60,
                reason="qualified_signal",
            )
            position.exit_price = 0.46
            position.exit_reason = "stop_loss"
            position.exit_time = datetime.now(UTC)
            events = self.engine.update_after_exit(position)

        self.assertFalse(self.engine.halted)
        self.assertFalse(any(event.reason == "daily_loss_frequency_limit" for event in events))

    def test_blocks_wide_entry_spread(self) -> None:
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="strong",
            confidence_level="tradable",
            price=0.31,
            size_ratio=0.04,
            cluster_count=4,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )

        decision = self.engine.evaluate_entry(
            candidate,
            self.state,
            expected_exit_price=0.24,
            entry_spread=0.07,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "spread_too_wide")

    def test_blocks_extra_btc_related_open_positions(self) -> None:
        for index, ticker in enumerate(("KXBTC-TEST", "KXBTC15M-TEST", "KXBTCD-TEST")):
            position = VirtualPosition.create(
                market_ticker=ticker,
                side="YES",
                entry_price=0.25,
                size=1,
                signal_strength="strong",
                confidence_level="tradable",
                entry_time=datetime.now(UTC),
                stop_price=0.20,
                take_profit_price=0.30,
                reason="qualified_signal",
            )
            self.state.positions[str(index)] = position

        candidate = SignalCandidate(
            market_ticker="KXBTC-NEW",
            side="YES",
            signal_strength="strong",
            confidence_level="tradable",
            price=0.25,
            size_ratio=0.04,
            cluster_count=4,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )

        decision = self.engine.evaluate_entry(
            candidate,
            self.state,
            expected_exit_price=0.24,
            entry_spread=0.01,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "btc_exposure_limit")


    def test_blocks_market_after_repeated_losses(self) -> None:
        now = datetime.now(UTC)
        for _ in range(self.config.portfolio.market_loss_cooldown_count):
            self.state.record_market_exit(
                "FIN-TEST",
                now,
                pnl=-self.config.portfolio.loss_count_threshold,
                loss_threshold=self.config.portfolio.loss_count_threshold,
                cooldown_loss_count=self.config.portfolio.market_loss_cooldown_count,
                cooldown_seconds=self.config.portfolio.market_loss_cooldown_seconds,
            )

        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="strong",
            confidence_level="tradable",
            price=0.25,
            size_ratio=0.04,
            cluster_count=4,
            timestamp=now,
            reason="qualified_signal",
        )

        decision = self.engine.evaluate_entry(
            candidate,
            self.state,
            expected_exit_price=0.24,
            entry_spread=0.01,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "market_loss_cooldown")

    def test_portfolio_exposure_limit_scales_with_current_cash(self) -> None:
        self.stats.cash = 30.0
        position = VirtualPosition.create(
            market_ticker="FIN-OPEN",
            side="YES",
            entry_price=0.50,
            size=24,
            signal_strength="strong",
            confidence_level="tradable",
            entry_time=datetime.now(UTC),
            stop_price=0.45,
            take_profit_price=0.55,
            reason="qualified_signal",
        )
        self.state.positions[position.position_id] = position
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="strong",
            confidence_level="tradable",
            price=0.50,
            size_ratio=0.04,
            cluster_count=4,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )

        decision = self.engine.evaluate_entry(
            candidate,
            self.state,
            expected_exit_price=0.49,
            entry_spread=0.01,
            proposed_size=10,
        )

        self.assertTrue(decision.allowed)



if __name__ == "__main__":
    unittest.main()
