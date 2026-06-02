from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from kalshi_algo.config import load_config
from kalshi_algo.engine import StrategyEngine
from kalshi_algo.persistence import EventStore


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_emits_entry_and_exit(self) -> None:
        config = load_config("config/default.toml")
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "session.db"
            input_path = Path(tmp) / "events.jsonl"
            base = "2026-05-01T12:00:00+00:00"
            events = [
                {
                    "channel": "orderbook_snapshot",
                    "market_ticker": "FIN-TEST",
                    "timestamp": "2026-05-01T12:00:02+00:00",
                    "yes_bids": [[0.49, 10], [0.48, 10]],
                    "yes_asks": [[0.51, 10], [0.52, 10]],
                },
                {
                    "channel": "trade",
                    "market_ticker": "FIN-TEST",
                    "side": "YES",
                    "price": 0.50,
                    "size": 200000,
                    "timestamp": "2026-05-01T12:00:01+00:00",
                    "market_volume": 5000000,
                    "category": "financial",
                    "close_time": "2026-05-01T13:00:00+00:00",
                },
                {
                    "channel": "trade",
                    "market_ticker": "FIN-TEST",
                    "side": "YES",
                    "price": 0.50,
                    "size": 200000,
                    "timestamp": "2026-05-01T12:00:02+00:00",
                    "market_volume": 5000000,
                    "category": "financial",
                    "close_time": "2026-05-01T13:00:00+00:00",
                },
                {
                    "channel": "trade",
                    "market_ticker": "FIN-TEST",
                    "side": "YES",
                    "price": 0.50,
                    "size": 200000,
                    "timestamp": "2026-05-01T12:00:02.500000+00:00",
                    "market_volume": 5000000,
                    "category": "financial",
                    "close_time": "2026-05-01T13:00:00+00:00",
                },
                {
                    "channel": "ticker",
                    "market_ticker": "FIN-TEST",
                    "price": 0.59,
                    "timestamp": "2026-05-01T12:00:04+00:00",
                },
            ]
            input_path.write_text("\n".join(json.dumps(item) for item in events))
            with EventStore(db_path) as store:
                engine = StrategyEngine(config, store)
                await engine.run_replay_file(input_path)
                persisted = store.load_events()
            self.assertTrue(any(row["event_type"] == "ENTER" for row in persisted))
            self.assertTrue(any(row["event_type"] == "EXIT" for row in persisted))


if __name__ == "__main__":
    unittest.main()
