from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kalshi_algo.cli import _db_has_events
from kalshi_algo.models import ActionEvent, utc_now
from kalshi_algo.persistence import EventStore


class CliTests(unittest.TestCase):
    def test_db_has_events_is_false_for_missing_or_empty_db(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.db"
            empty = Path(tmp) / "empty.db"
            with EventStore(empty):
                pass

            self.assertFalse(_db_has_events(missing))
            self.assertFalse(_db_has_events(empty))

    def test_db_has_events_detects_existing_session_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "session.db"
            with EventStore(db_path) as store:
                store.append(
                    ActionEvent(
                        event_type="INFO",
                        timestamp=utc_now(),
                        market_ticker=None,
                        side=None,
                        price=None,
                        size=None,
                        signal_strength=None,
                        confidence_level=None,
                        reason="test",
                    )
                )

            self.assertTrue(_db_has_events(db_path))


if __name__ == "__main__":
    unittest.main()
