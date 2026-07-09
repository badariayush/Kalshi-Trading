from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
                {"reason": "execution failure"},
                5_000,
            ),
            _event(
                "SimulatedOrderPlaced",
                {
                    "market_ticker": "KXBTCD-TEST",
                    "execution": "simulated_print_only",
                    "order_submission": "disabled",
                },
                6_000,
            ),
        )

        report = build_report(tuple(event.to_dict() for event in events))

        self.assertEqual(report.total_events, 6)
        self.assertEqual(report.feed_unhealthy_events, 1)
        self.assertEqual(report.simulated_orders, 1)
        self.assertEqual(report.closed_positions, 2)
        self.assertEqual(report.profitable_positions, 1)
        self.assertEqual(report.losing_positions, 1)
        self.assertEqual(report.total_realized_pnl, Decimal("0.95"))
        self.assertEqual(report.execution_failures, 1)
        self.assertEqual(report.status, "error")
        self.assertEqual(report.open_positions, 1)
        self.assertEqual(report.win_rate_pct, Decimal("50.00"))
        self.assertEqual(report.average_realized_pnl, Decimal("0.48"))

    def test_report_counts_open_paper_positions_and_total_fees(self) -> None:
        events = (
            _event(
                "SimulatedOrderPlaced",
                {
                    "market_ticker": "OPEN",
                    "leg_index": 1,
                    "fee": "0.02",
                },
                1_000,
            ),
            _event(
                "SimulatedOrderPlaced",
                {
                    "market_ticker": "CLOSED",
                    "leg_index": 1,
                    "fee": "0.02",
                },
                2_000,
            ),
            _event(
                "PositionClosed",
                {
                    "market_ticker": "CLOSED",
                    "realized_pnl": "0.10",
                    "total_fees": "0.04",
                },
                3_000,
            ),
        )

        report = build_report(tuple(event.to_dict() for event in events))

        self.assertEqual(report.open_positions, 1)
        self.assertEqual(report.total_fees, Decimal("0.06"))
        self.assertEqual(report.win_rate_pct, Decimal("100.00"))

    def test_report_cli_reads_sqlite_audit_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "audit.sqlite3"
            store = SQLiteAuditStore(audit_db)
            store.append(
                _event(
                    "LiveDataAuditCompleted",
                    {
                        "raw_messages": 10,
                        "kalshi_messages": 5,
                        "coinbase_messages": 5,
                        "feed_unhealthy_events": 0,
                        "network": "attempted",
                        "execution": "not_attempted",
                        "order_submission": "disabled",
                    },
                    1_000,
                )
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(["report", "--audit-db", str(audit_db)])

        self.assertEqual(exit_code, 0)
        self.assertIn("status=no_trades", stdout.getvalue())
        self.assertIn("simulated_orders=0", stdout.getvalue())
        self.assertIn("closed_positions=0", stdout.getvalue())
        self.assertIn("total_realized_pnl=0.00", stdout.getvalue())

    def test_report_cli_does_not_materialize_the_full_raw_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "audit.sqlite3"
            store = SQLiteAuditStore(audit_db)
            store.append(
                _event(
                    "LiveDataAuditCompleted",
                    {
                        "feed_unhealthy_events": 2,
                        "network": "attempted",
                        "order_submission": "disabled",
                    },
                    1_000,
                )
            )
            stdout = StringIO()

            with patch.object(
                SQLiteAuditStore,
                "read_all",
                side_effect=AssertionError("report must not load raw feed rows"),
            ):
                with redirect_stdout(stdout):
                    exit_code = main(["report", "--audit-db", str(audit_db)])

        self.assertEqual(exit_code, 0)
        self.assertIn("feed_unhealthy_events=2", stdout.getvalue())

    def test_report_cli_rejects_non_live_audit_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "audit.sqlite3"
            store = SQLiteAuditStore(audit_db)
            store.append(
                _event(
                    "NonLiveRunCompleted",
                    {"network": "non_live", "order_submission": "disabled"},
                    1_000,
                )
            )
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main(["report", "--audit-db", str(audit_db)])

        self.assertEqual(exit_code, 2)
        self.assertIn("only accepts live-data audit databases", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
