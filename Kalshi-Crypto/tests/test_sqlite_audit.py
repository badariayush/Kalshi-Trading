from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.events import AuditEvent
from kalshi_crypto.storage import SQLiteAuditStore


class SQLiteAuditStoreTests(unittest.TestCase):
    def test_appends_and_reads_redacted_events_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "audit.sqlite3"
            store = SQLiteAuditStore(db_path)
            first = AuditEvent.create(
                event_type="WindowDiscovered",
                worker="market_monitor",
                payload={"market_ticker": "KXBTCD-TEST", "api_key_id": "abc123"},
                causality_id="root-1",
                timestamp_ms=1_000,
                event_id="evt-1",
            )
            second = AuditEvent.create(
                event_type="FeedHealthEvaluated",
                worker="market_monitor",
                payload={"source": "kalshi_orderbook", "healthy": True},
                causality_id="root-1",
                timestamp_ms=1_001,
                event_id="evt-2",
            )

            store.append(first)
            store.append(second)
            records = store.read_all()

        self.assertEqual([record["event_id"] for record in records], ["evt-1", "evt-2"])
        self.assertEqual(records[0]["payload"]["api_key_id"], "[REDACTED]")
        self.assertEqual(records[1]["payload"]["healthy"], True)


if __name__ == "__main__":
    unittest.main()
