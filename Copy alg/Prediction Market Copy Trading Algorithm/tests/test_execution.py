from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from kalshi_algo.config import load_config
from kalshi_algo.engine import StrategyEngine
from kalshi_algo.execution import VirtualExecutionEngine
from kalshi_algo.models import SignalCandidate
from kalshi_algo.persistence import EventStore
from pathlib import Path
from tempfile import TemporaryDirectory


class VirtualExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config("config/default.toml")
        self.engine = VirtualExecutionEngine(self.config)

    def test_enter_creates_position(self) -> None:
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="strong",
            confidence_level="tradable",
            price=0.50,
            size_ratio=0.05,
            cluster_count=4,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )
        decision = self.engine.enter(candidate)
        self.assertTrue(decision.accepted)
        assert decision.position is not None
        self.assertGreater(decision.position.take_profit_price, decision.position.entry_price)
        self.assertLess(decision.position.stop_price, decision.position.entry_price)

    def test_kelly_size_uses_bankroll_without_fixed_two_share_cap(self) -> None:
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="strong",
            confidence_level="tradable",
            price=0.10,
            size_ratio=0.05,
            cluster_count=4,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )

        size = self.engine.determine_position_size(candidate, bankroll=20.0)

        self.assertGreater(size, 2)

    def test_stop_trails_upward_only_when_enabled(self) -> None:
        self.config.execution.enable_stop_losses = True
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="moderate",
            confidence_level="tradable",
            price=0.40,
            size_ratio=0.04,
            cluster_count=3,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )
        decision = self.engine.enter(candidate)
        position = decision.position
        assert position is not None
        old_stop = position.stop_price
        self.engine.update_position(position, 0.50)
        self.assertGreater(position.stop_price, old_stop)
        trailed = position.stop_price
        self.engine.update_position(position, 0.45)
        self.assertEqual(position.stop_price, trailed)

    def test_stop_losses_disabled_by_config(self) -> None:
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="moderate",
            confidence_level="tradable",
            price=0.40,
            size_ratio=0.04,
            cluster_count=3,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )
        decision = self.engine.enter(candidate)
        position = decision.position
        assert position is not None
        old_stop = position.stop_price

        self.engine.update_position(position, 0.50)

        self.assertEqual(position.stop_price, old_stop)
        self.assertIsNone(self.engine.should_exit(position, old_stop))
        self.assertEqual(self.engine.should_exit(position, position.take_profit_price), "take_profit")

    def test_no_position_uses_side_specific_prices(self) -> None:
        self.config.execution.enable_stop_losses = True
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="NO",
            signal_strength="moderate",
            confidence_level="tradable",
            price=0.40,
            size_ratio=0.04,
            cluster_count=2,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )
        decision = self.engine.enter(candidate)
        position = decision.position
        assert position is not None
        self.assertGreater(position.take_profit_price, position.entry_price)
        self.assertLess(position.stop_price, position.entry_price)
        self.assertEqual(self.engine.should_exit(position, position.take_profit_price), "take_profit")
        self.assertEqual(self.engine.should_exit(position, position.stop_price), "stop_loss")

    def test_liquidate_all_closes_open_positions_at_latest_price(self) -> None:
        candidate = SignalCandidate(
            market_ticker="FIN-TEST",
            side="YES",
            signal_strength="moderate",
            confidence_level="tradable",
            price=0.40,
            size_ratio=0.04,
            cluster_count=3,
            timestamp=datetime.now(UTC),
            reason="qualified_signal",
        )
        with TemporaryDirectory() as tmp:
            with EventStore(Path(tmp) / "session.db") as store:
                strategy = StrategyEngine(self.config, store)
                decision = strategy.execution_engine.enter(candidate)
                assert decision.position is not None
                strategy.state.positions[decision.position.position_id] = decision.position
                strategy.latest_prices[(candidate.market_ticker, candidate.side)] = 0.44

                events = strategy.liquidate_all("test_shutdown")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "EXIT")
        self.assertEqual(events[0].price, 0.44)
        self.assertEqual(events[0].reason, "test_shutdown")
        self.assertEqual(strategy.state.positions, {})

    def test_drain_mode_rejects_new_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            with EventStore(Path(tmp) / "session.db") as store:
                strategy = StrategyEngine(self.config, store)
                strategy.accepting_new_entries = False
                base = datetime.now(UTC)
                payload = {
                    "channel": "trade",
                    "market_ticker": "FIN-TEST",
                    "side": "YES",
                    "price": 0.10,
                    "size": 200000,
                    "timestamp": base.isoformat(),
                    "market_volume": 5_000_000,
                    "category": "financial",
                    "close_time": (base + timedelta(hours=1)).isoformat(),
                }

                events = strategy.process_payload(payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "REJECT")
        self.assertEqual(events[0].reason, "shutdown_draining")

    def test_connection_status_payload_becomes_info_event(self) -> None:
        with TemporaryDirectory() as tmp:
            with EventStore(Path(tmp) / "session.db") as store:
                strategy = StrategyEngine(self.config, store)
                events = strategy.process_payload(
                    {
                        "type": "connection_status",
                        "msg": {"status": "reconnecting", "delay_seconds": 5},
                    }
                )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "INFO")
        self.assertEqual(events[0].reason, "websocket_reconnecting")


if __name__ == "__main__":
    unittest.main()
