from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import json
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.cli import main
from kalshi_crypto.storage import SQLiteAuditStore


class DataOnlyCliTests(unittest.TestCase):
    def test_data_only_requires_local_replay_file_until_live_ingestion_exists(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(["data-only", "--config", "configs/paper.example.toml"])

        self.assertEqual(exit_code, 2)
        self.assertIn("data-only requires a local replay file", stderr.getvalue())

    def test_data_only_replays_events_to_sqlite_without_network_or_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_file = Path(tmpdir) / "replay.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            replay_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_id": "evt-1",
                                "event_type": "OrderBookSnapshotNormalized",
                                "worker": "market_monitor",
                                "timestamp_ms": 1_000,
                                "causality_id": "root-1",
                                "payload": {
                                    "source": "kalshi_orderbook",
                                    "source_timestamp_ms": 900,
                                    "received_timestamp_ms": 1_000,
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "event_id": "evt-2",
                                "event_type": "OrderBookSnapshotNormalized",
                                "worker": "market_monitor",
                                "timestamp_ms": 5_000,
                                "causality_id": "root-1",
                                "payload": {
                                    "source": "kalshi_orderbook",
                                    "source_timestamp_ms": 1_000,
                                    "received_timestamp_ms": 5_000,
                                    "api_key_id": "must-redact",
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "data-only",
                        "--config",
                        "configs/paper.example.toml",
                        "--replay-file",
                        str(replay_file),
                        "--audit-db",
                        str(audit_db),
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("mode=paper_simulated", stdout.getvalue())
        self.assertIn("network=not_attempted", stdout.getvalue())
        self.assertIn("execution=not_attempted", stdout.getvalue())
        self.assertIn("replay_events=2", stdout.getvalue())
        self.assertIn("stale_events=1", stdout.getvalue())
        self.assertEqual([record["event_id"] for record in records[:2]], ["evt-1", "evt-2"])
        self.assertEqual(records[1]["payload"]["api_key_id"], "[REDACTED]")
        self.assertEqual(records[-1]["event_type"], "DataOnlyRunCompleted")

    def test_data_only_normalizes_raw_kalshi_orderbook_replay_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_file = Path(tmpdir) / "raw-orderbook.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            replay_file.write_text(
                json.dumps(
                    {
                        "record_type": "kalshi_orderbook",
                        "market_ticker": "KXBTCD-TEST",
                        "source_timestamp_ms": 1_000,
                        "received_timestamp_ms": 1_100,
                        "yes_bids": [{"price": "0.42", "quantity": 10}],
                        "no_bids": [{"price": "0.37", "quantity": 20}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "data-only",
                        "--config",
                        "configs/paper.example.toml",
                        "--replay-file",
                        str(replay_file),
                        "--audit-db",
                        str(audit_db),
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("replay_events=1", stdout.getvalue())
        self.assertIn("normalized_orderbooks=1", stdout.getvalue())
        self.assertEqual(records[0]["event_type"], "OrderBookSnapshotNormalized")
        self.assertEqual(records[0]["payload"]["yes_ask_price"], "0.63")
        self.assertEqual(records[0]["payload"]["yes_ask_depth"], 20)
        self.assertEqual(records[0]["payload"]["no_ask_price"], "0.58")
        self.assertEqual(records[0]["payload"]["no_ask_depth"], 10)
        self.assertEqual(records[1]["event_type"], "FeedHealthEvaluated")
        self.assertEqual(records[1]["payload"]["healthy"], True)
        self.assertEqual(records[-1]["event_type"], "DataOnlyRunCompleted")

    def test_data_only_replays_raw_kalshi_market_lifecycle_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_file = Path(tmpdir) / "raw-market.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            replay_file.write_text(
                json.dumps(
                    {
                        "record_type": "kalshi_market",
                        "market_ticker": "KXBTCD-TEST",
                        "series_ticker": "KXBTC15M",
                        "underlying": "BTC",
                        "strike": "102500",
                        "lifecycle_status": "closing",
                        "open_timestamp_ms": 1_000,
                        "close_timestamp_ms": 901_000,
                        "received_timestamp_ms": 890_000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "data-only",
                        "--config",
                        "configs/paper.example.toml",
                        "--replay-file",
                        str(replay_file),
                        "--audit-db",
                        str(audit_db),
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("market_events=2", stdout.getvalue())
        self.assertEqual(
            [record["event_type"] for record in records[:2]],
            ["WindowDiscovered", "WindowClosingSoon"],
        )
        self.assertEqual(records[1]["payload"]["time_to_close_ms"], 11_000)

    def test_data_only_projects_current_and_next_market_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_file = Path(tmpdir) / "market-windows.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            replay_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "record_type": "kalshi_market",
                                "market_ticker": "KXBTCD-CURRENT",
                                "series_ticker": "KXBTC15M",
                                "underlying": "BTC",
                                "strike": "102500",
                                "lifecycle_status": "open",
                                "open_timestamp_ms": 1_000,
                                "close_timestamp_ms": 901_000,
                                "received_timestamp_ms": 2_000,
                            }
                        ),
                        json.dumps(
                            {
                                "record_type": "kalshi_market",
                                "market_ticker": "KXBTCD-NEXT",
                                "series_ticker": "KXBTC15M",
                                "underlying": "BTC",
                                "strike": "103000",
                                "lifecycle_status": "upcoming",
                                "open_timestamp_ms": 901_000,
                                "close_timestamp_ms": 1_801_000,
                                "received_timestamp_ms": 2_000,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "data-only",
                        "--config",
                        "configs/paper.example.toml",
                        "--replay-file",
                        str(replay_file),
                        "--audit-db",
                        str(audit_db),
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("current_windows=KXBTCD-CURRENT", stdout.getvalue())
        self.assertIn("next_windows=KXBTCD-NEXT", stdout.getvalue())
        self.assertEqual(records[-1]["payload"]["current_window_tickers"], ["KXBTCD-CURRENT"])
        self.assertEqual(records[-1]["payload"]["next_window_tickers"], ["KXBTCD-NEXT"])

    def test_data_only_replays_cf_benchmark_ticks_into_candles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            replay_file = Path(tmpdir) / "cf-ticks.jsonl"
            audit_db = Path(tmpdir) / "audit.sqlite3"
            replay_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "record_type": "cf_benchmark_tick",
                                "index_ticker": "BRTI",
                                "price": "100000",
                                "source_timestamp_ms": 1_000,
                                "received_timestamp_ms": 1_050,
                            }
                        ),
                        json.dumps(
                            {
                                "record_type": "cf_benchmark_tick",
                                "index_ticker": "BRTI",
                                "price": "100500",
                                "source_timestamp_ms": 30_000,
                                "received_timestamp_ms": 30_050,
                            }
                        ),
                        json.dumps(
                            {
                                "record_type": "cf_benchmark_tick",
                                "index_ticker": "BRTI",
                                "price": "101000",
                                "source_timestamp_ms": 61_000,
                                "received_timestamp_ms": 61_050,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "data-only",
                        "--config",
                        "configs/paper.example.toml",
                        "--replay-file",
                        str(replay_file),
                        "--audit-db",
                        str(audit_db),
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("cf_benchmark_ticks=3", stdout.getvalue())
        self.assertIn("cf_candles=2", stdout.getvalue())
        self.assertEqual(
            [record["event_type"] for record in records[:5]],
            [
                "CFBenchmarkTickIngested",
                "FeedHealthEvaluated",
                "CFBenchmarkTickIngested",
                "FeedHealthEvaluated",
                "CFBenchmarkTickIngested",
            ],
        )
        candle_events = [
            record for record in records if record["event_type"] == "CFBenchmarkCandleClosed"
        ]
        self.assertEqual(len(candle_events), 2)
        self.assertEqual(candle_events[0]["payload"]["close_price"], "100500")
        self.assertEqual(candle_events[0]["payload"]["tick_count"], 2)


if __name__ == "__main__":
    unittest.main()
