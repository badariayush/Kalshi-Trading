from __future__ import annotations

from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.cli import main
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.report import build_report
from kalshi_crypto.storage import SQLiteAuditStore


def _event(event_type: str, payload: dict[str, object], timestamp_ms: int) -> AuditEvent:
    return AuditEvent.create(
        event_type=event_type,
        worker="test",
        payload=payload,
        causality_id="test-run",
        timestamp_ms=timestamp_ms,
    )


class ReportTests(unittest.TestCase):
    def test_build_report_summarizes_closed_positions_and_health(self) -> None:
        events = (
            _event(
                "FeedHealthEvaluated",
                {"healthy": True, "source": "kalshi_orderbook"},
                1_000,
            ),
            _event(
                "FeedHealthEvaluated",
                {"healthy": False, "source": "coinbase:ticker"},
                2_000,
            ),
            _event(
                "PositionClosed",
                {"realized_pnl": "1.35", "outcome": "profit"},
                3_000,
            ),
            _event(
                "PositionClosed",
                {"realized_pnl": "-0.40", "outcome": "loss"},
                4_000,
            ),
            _event(
                "ExecutionFailed",
                {"reason": "paper failure fixture"},
                5_000,
            ),
        )

        report = build_report(tuple(event.to_dict() for event in events))

        self.assertEqual(report.total_events, 5)
        self.assertEqual(report.feed_unhealthy_events, 1)
        self.assertEqual(report.closed_positions, 2)
        self.assertEqual(report.profitable_positions, 1)
        self.assertEqual(report.losing_positions, 1)
        self.assertEqual(report.total_realized_pnl, Decimal("0.95"))
        self.assertEqual(report.execution_failures, 1)
        self.assertEqual(report.status, "error")

    def test_report_cli_reads_sqlite_audit_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "audit.sqlite3"
            store = SQLiteAuditStore(audit_db)
            store.append(
                _event(
                    "PositionClosed",
                    {"realized_pnl": "2.00", "outcome": "profit"},
                    1_000,
                )
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["report", "--audit-db", str(audit_db)])

        self.assertEqual(exit_code, 0)
        self.assertIn("status=profit", stdout.getvalue())
        self.assertIn("closed_positions=1", stdout.getvalue())
        self.assertIn("total_realized_pnl=2.00", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
