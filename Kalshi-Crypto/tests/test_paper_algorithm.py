from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.cli import main
from kalshi_crypto.report import load_report
from kalshi_crypto.storage import SQLiteAuditStore


class PaperAlgorithmTests(unittest.TestCase):
    def test_paper_run_uses_workers_and_produces_profitable_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "paper.sqlite3"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "paper",
                        "--config",
                        "configs/paper.example.toml",
                        "--audit-db",
                        str(audit_db),
                        "--max-seconds",
                        "600",
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()
            report = load_report(audit_db)

        self.assertEqual(exit_code, 0)
        self.assertIn("order placed for", stdout.getvalue())
        self.assertIn("order sold for", stdout.getvalue())
        self.assertEqual(report.status, "profit")
        self.assertGreater(report.total_realized_pnl, Decimal("0"))
        self.assertEqual(
            [
                "WindowDiscovered",
                "WindowOpened",
                "OrderBookSnapshotNormalized",
                "CFBenchmarkCandleClosed",
                "SignalReady",
                "EntryAuthorized",
                "OrderSubmitted",
                "FillRecorded",
                "ExitAuthorized",
                "OrderSubmitted",
                "FillRecorded",
                "PositionClosed",
                "PaperRunCompleted",
            ],
            [record["event_type"] for record in records],
        )
        self.assertEqual(records[5]["payload"]["side"], "yes")
        self.assertEqual(records[-1]["payload"]["order_submission"], "disabled")

    def test_paper_run_can_use_live_provider_message_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_db = Path(tmpdir) / "paper-live.sqlite3"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "paper",
                        "--config",
                        "configs/paper.example.toml",
                        "--audit-db",
                        str(audit_db),
                        "--max-seconds",
                        "600",
                        "--live-input-file",
                        "configs/live.paper.example.jsonl",
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()
            report = load_report(audit_db)

        self.assertEqual(exit_code, 0)
        self.assertIn("order placed for KXBTCD-LIVEPAPER", stdout.getvalue())
        self.assertEqual(report.status, "profit")
        self.assertGreater(report.total_realized_pnl, Decimal("0"))
        self.assertIn("CoinbaseCandleClosed", [record["event_type"] for record in records])
        self.assertEqual(records[0]["payload"]["market_ticker"], "KXBTCD-LIVEPAPER")
        self.assertEqual(records[2]["payload"]["source"], "kalshi_websocket")
        self.assertEqual(records[-1]["payload"]["network"], "live_message_file")
        self.assertEqual(records[-1]["payload"]["raw_messages"], 5)
        self.assertEqual(records[-1]["payload"]["order_submission"], "disabled")

    def test_paper_run_rejects_real_order_submission_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "unsafe.toml"
            audit_db = Path(tmpdir) / "paper.sqlite3"
            config_path.write_text(
                """
[runtime]
mode = "paper_simulated"
confirm_live = false
allow_trade_mcp = false

[order_api]
enable_order_api = true
allow_order_submission = true
""".strip(),
                encoding="utf-8",
            )
            stdout = StringIO()
            stderr = StringIO()

            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "paper",
                        "--config",
                        str(config_path),
                        "--audit-db",
                        str(audit_db),
                    ]
                )

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
