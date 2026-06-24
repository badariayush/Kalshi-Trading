from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.audit import JsonlAuditLog
from kalshi_crypto.events import AuditEvent, EventBus


class EventAndAuditTests(unittest.TestCase):
    def test_audit_event_payload_is_immutable(self) -> None:
        event = AuditEvent.create(
            event_type="WindowDiscovered",
            worker="market_monitor",
            payload={"market_ticker": "KXBTCD-TEST"},
            causality_id="root-1",
            timestamp_ms=1_000,
            event_id="evt-1",
        )

        with self.assertRaises(TypeError):
            event.payload["market_ticker"] = "MUTATED"  # type: ignore[index]

    def test_event_bus_dispatches_to_subscribers_without_mutating_event(self) -> None:
        bus = EventBus()
        seen: list[AuditEvent] = []
        bus.subscribe("WindowDiscovered", seen.append)
        event = AuditEvent.create(
            event_type="WindowDiscovered",
            worker="market_monitor",
            payload={"market_ticker": "KXBTCD-TEST"},
            causality_id="root-1",
            timestamp_ms=1_000,
            event_id="evt-1",
        )

        published = bus.publish(event)

        self.assertEqual(published, 1)
        self.assertEqual(seen, [event])

    def test_jsonl_audit_log_appends_redacted_reviewable_events(self) -> None:
        event = AuditEvent.create(
            event_type="ConfigChecked",
            worker="logging",
            payload={
                "market_ticker": "KXBTCD-TEST",
                "api_key_id": "abc123",
                "nested": {"private_key_path": "/tmp/key.pem"},
            },
            causality_id="root-1",
            timestamp_ms=1_000,
            event_id="evt-1",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "audit.jsonl"
            log = JsonlAuditLog(path)

            log.append(event)
            loaded = log.read_all()

        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["event_id"], "evt-1")
        self.assertEqual(loaded[0]["payload"]["market_ticker"], "KXBTCD-TEST")
        self.assertEqual(loaded[0]["payload"]["api_key_id"], "[REDACTED]")
        self.assertEqual(loaded[0]["payload"]["nested"]["private_key_path"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
