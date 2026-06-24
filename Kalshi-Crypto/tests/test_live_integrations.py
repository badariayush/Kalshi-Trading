from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from kalshi_crypto.cli import main
from kalshi_crypto.live_config import LiveDataConfig, OrderApiConfig
from kalshi_crypto.live_feeds import (
    CoinbaseWebSocketSubscription,
    KalshiWebSocketSubscription,
)
from kalshi_crypto.kalshi_auth import KalshiAuthConfig
from kalshi_crypto.kalshi_auth import build_kalshi_auth_headers, kalshi_signature_message
from kalshi_crypto.kalshi_orders import KalshiOrderRequest
from kalshi_crypto.live_collectors import (
    events_from_coinbase_ws_message,
    events_from_kalshi_ws_message,
)
from kalshi_crypto.storage import SQLiteAuditStore


class LiveIntegrationConfigTests(unittest.TestCase):
    def test_live_data_config_defaults_to_network_ready(self) -> None:
        config = LiveDataConfig.from_mapping({})

        self.assertTrue(config.enable_live_network)
        self.assertEqual(
            config.kalshi_ws_url,
            "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
        )
        self.assertEqual(config.coinbase_ws_url, "wss://advanced-trade-ws.coinbase.com")

    def test_order_api_config_enables_request_building_but_blocks_submission_by_default(self) -> None:
        config = OrderApiConfig.from_mapping({})

        self.assertTrue(config.enable_order_api)
        self.assertFalse(config.allow_order_submission)
        self.assertEqual(config.kalshi_rest_url, "https://api.elections.kalshi.com/trade-api/v2")

    def test_auth_config_resolves_environment_names_without_reading_secret_files(self) -> None:
        config = KalshiAuthConfig(
            key_id_env="KALSHI_KEY_ID",
            private_key_pem_env="KALSHI_PRIVATE_KEY_PEM",
        )

        self.assertEqual(config.required_env_vars(), ("KALSHI_KEY_ID", "KALSHI_PRIVATE_KEY_PEM"))

    def test_kalshi_auth_header_builder_signs_timestamp_method_and_path(self) -> None:
        signed_messages: list[str] = []

        def signer(message: str) -> str:
            signed_messages.append(message)
            return "signed-message"

        headers = build_kalshi_auth_headers(
            key_id="key-123",
            timestamp_ms=1_700_000_000_000,
            method="get",
            path="/portfolio/orders",
            signer=signer,
        )

        self.assertEqual(signed_messages, ["1700000000000GET/portfolio/orders"])
        self.assertEqual(
            headers.as_mapping(),
            {
                "KALSHI-ACCESS-KEY": "key-123",
                "KALSHI-ACCESS-TIMESTAMP": "1700000000000",
                "KALSHI-ACCESS-SIGNATURE": "signed-message",
            },
        )

    def test_kalshi_signature_message_rejects_queryless_path_mistakes(self) -> None:
        with self.assertRaisesRegex(ValueError, "path must start"):
            kalshi_signature_message(
                timestamp_ms=1,
                method="GET",
                path="portfolio/orders",
            )


class LiveSubscriptionBuilderTests(unittest.TestCase):
    def test_builds_kalshi_websocket_subscription_messages(self) -> None:
        subscription = KalshiWebSocketSubscription(
            market_tickers=("KXBTCD-TEST", "KXETHD-TEST"),
            channels=("orderbook_delta", "ticker", "market_lifecycle_v2"),
        )

        messages = subscription.messages()

        self.assertEqual(
            messages,
            (
                {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta", "ticker", "market_lifecycle_v2"],
                        "market_tickers": ["KXBTCD-TEST", "KXETHD-TEST"],
                    },
                },
            ),
        )

    def test_builds_coinbase_public_market_data_subscription_messages(self) -> None:
        subscription = CoinbaseWebSocketSubscription(
            product_ids=("BTC-USD", "ETH-USD"),
            channels=("ticker", "level2"),
        )

        messages = subscription.messages()

        self.assertEqual(
            messages,
            (
                {
                    "type": "subscribe",
                    "product_ids": ["BTC-USD", "ETH-USD"],
                    "channel": "ticker",
                },
                {
                    "type": "subscribe",
                    "product_ids": ["BTC-USD", "ETH-USD"],
                    "channel": "level2",
                },
            ),
        )


class LiveWebSocketParserTests(unittest.TestCase):
    def test_parses_kalshi_ticker_message_into_audit_event(self) -> None:
        events = events_from_kalshi_ws_message(
            {
                "type": "ticker",
                "sid": 7,
                "seq": 42,
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "yes_bid_dollars": "0.47",
                    "yes_ask_dollars": "0.48",
                    "source_timestamp_ms": 1_700_000_000_000,
                },
            },
            received_timestamp_ms=1_700_000_000_050,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, "KalshiTickerReceived")
        self.assertEqual(event.worker, "market_monitor")
        self.assertEqual(event.payload["source"], "kalshi_websocket")
        self.assertEqual(event.payload["market_ticker"], "KXBTCD-TEST")
        self.assertEqual(event.payload["sequence"], 42)
        self.assertEqual(event.payload["source_timestamp_ms"], 1_700_000_000_000)
        self.assertEqual(event.payload["received_timestamp_ms"], 1_700_000_000_050)

    def test_parses_coinbase_level2_message_into_audit_event(self) -> None:
        events = events_from_coinbase_ws_message(
            {
                "channel": "l2_data",
                "sequence_num": 11,
                "timestamp": "2026-06-22T14:00:00Z",
                "events": [
                    {
                        "type": "snapshot",
                        "product_id": "BTC-USD",
                        "updates": [
                            {
                                "side": "bid",
                                "price_level": "100000.00",
                                "new_quantity": "1.5",
                                "event_time": "2026-06-22T14:00:00Z",
                            }
                        ],
                    }
                ],
            },
            received_timestamp_ms=1_782_136_800_100,
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, "CoinbaseLevel2Received")
        self.assertEqual(event.payload["source"], "coinbase_websocket")
        self.assertEqual(event.payload["channel"], "l2_data")
        self.assertEqual(event.payload["product_id"], "BTC-USD")
        self.assertEqual(event.payload["update_count"], 1)
        self.assertEqual(event.payload["sequence"], 11)
        self.assertEqual(event.payload["source_timestamp_ms"], 1_782_136_800_000)


class OrderApiGuardTests(unittest.TestCase):
    def test_order_request_builds_v2_payload_with_idempotency_and_cost_guard(self) -> None:
        request = KalshiOrderRequest(
            market_ticker="KXBTCD-TEST",
            client_order_id="strategy-001",
            action="buy",
            side="yes",
            order_type="limit",
            count=1,
            yes_price_cents=47,
            buy_max_cost_cents=47,
            post_only=True,
            reduce_only=False,
        )

        self.assertEqual(request.path(), "/portfolio/orders")
        self.assertEqual(
            request.payload(),
            {
                "ticker": "KXBTCD-TEST",
                "client_order_id": "strategy-001",
                "action": "buy",
                "side": "yes",
                "type": "limit",
                "count": 1,
                "yes_price": 47,
                "buy_max_cost": 47,
                "post_only": True,
                "reduce_only": False,
            },
        )

    def test_order_request_rejects_buy_without_max_cost(self) -> None:
        with self.assertRaisesRegex(ValueError, "buy_max_cost_cents"):
            KalshiOrderRequest(
                market_ticker="KXBTCD-TEST",
                client_order_id="strategy-001",
                action="buy",
                side="yes",
                order_type="limit",
                count=1,
                yes_price_cents=47,
            )


class LiveReadinessCliTests(unittest.TestCase):
    def test_doctor_live_data_reports_ready_network_and_disabled_submission(self) -> None:
        stdout = StringIO()

        with redirect_stdout(stdout):
            exit_code = main(
                ["doctor-live-data", "--config", "configs/paper.example.toml"]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("status=ok", stdout.getvalue())
        self.assertIn("network=not_attempted", stdout.getvalue())
        self.assertIn("live_network=enabled", stdout.getvalue())
        self.assertIn("order_api=enabled", stdout.getvalue())
        self.assertIn("order_submission=disabled", stdout.getvalue())
        self.assertIn(
            "kalshi_ws_url=wss://external-api-ws.kalshi.com/trade-api/ws/v2",
            stdout.getvalue(),
        )

    def test_doctor_live_data_rejects_order_api_without_explicit_network_enablement(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unsafe.toml"
            path.write_text(
                """
[runtime]
mode = "paper_simulated"
confirm_live = false
allow_trade_mcp = false

[live_data]
enable_live_network = false

[order_api]
enable_order_api = true
allow_order_submission = false
""".strip(),
                encoding="utf-8",
            )
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main(["doctor-live-data", "--config", str(path)])

        self.assertEqual(exit_code, 2)
        self.assertIn("order API requires live network readiness", stderr.getvalue())

    def test_live_data_audit_reads_provider_messages_and_never_executes_orders(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            live_file = Path(tmpdir) / "live-messages.jsonl"
            audit_db = Path(tmpdir) / "live.sqlite3"
            live_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "source": "kalshi",
                                "received_timestamp_ms": 1_700_000_000_050,
                                "message": {
                                    "type": "ticker",
                                    "seq": 1,
                                    "msg": {
                                        "market_ticker": "KXBTCD-TEST",
                                        "source_timestamp_ms": 1_700_000_000_000,
                                    },
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "source": "coinbase",
                                "received_timestamp_ms": 1_700_000_000_060,
                                "message": {
                                    "channel": "l2_data",
                                    "timestamp": "2023-11-14T22:13:20Z",
                                    "sequence_num": 2,
                                    "events": [
                                        {
                                            "type": "snapshot",
                                            "product_id": "BTC-USD",
                                            "updates": [
                                                {
                                                    "side": "bid",
                                                    "price_level": "100000.00",
                                                    "new_quantity": "1.0",
                                                }
                                            ],
                                        }
                                    ],
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
                        "live-data",
                        "--config",
                        "configs/paper.example.toml",
                        "--input-file",
                        str(live_file),
                        "--audit-db",
                        str(audit_db),
                        "--max-seconds",
                        "10",
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("status=ok", stdout.getvalue())
        self.assertIn("network=not_attempted", stdout.getvalue())
        self.assertIn("execution=not_attempted", stdout.getvalue())
        self.assertIn("order_submission=disabled", stdout.getvalue())
        self.assertIn("raw_messages=2", stdout.getvalue())
        self.assertIn("feed_unhealthy_events=0", stdout.getvalue())
        self.assertEqual(
            [record["event_type"] for record in records],
            [
                "KalshiTickerReceived",
                "FeedHealthEvaluated",
                "CoinbaseLevel2Received",
                "FeedHealthEvaluated",
                "LiveDataAuditCompleted",
            ],
        )
        self.assertEqual(records[-1]["payload"]["order_submission"], "disabled")

    def test_live_data_audit_tolerates_small_provider_clock_skew(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            live_file = Path(tmpdir) / "live-messages.jsonl"
            audit_db = Path(tmpdir) / "live.sqlite3"
            live_file.write_text(
                json.dumps(
                    {
                        "source": "coinbase",
                        "received_timestamp_ms": 1_700_000_000_000,
                        "message": {
                            "channel": "ticker",
                            "timestamp_ms": 1_700_000_000_050,
                            "events": [{"type": "update", "product_id": "BTC-USD"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "live-data",
                        "--config",
                        "configs/paper.example.toml",
                        "--input-file",
                        str(live_file),
                        "--audit-db",
                        str(audit_db),
                        "--max-seconds",
                        "10",
                    ]
                )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(exit_code, 0)
        self.assertIn("feed_unhealthy_events=0", stdout.getvalue())
        self.assertEqual(records[1]["event_type"], "FeedHealthEvaluated")
        self.assertEqual(records[1]["payload"]["healthy"], True)

    def test_live_data_audit_can_write_provider_message_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "captured.jsonl"
            audit_db = Path(tmpdir) / "live.sqlite3"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "live-data",
                        "--config",
                        "configs/paper.example.toml",
                        "--input-file",
                        "configs/live.messages.example.jsonl",
                        "--output-file",
                        str(output_file),
                        "--audit-db",
                        str(audit_db),
                        "--max-seconds",
                        "10",
                    ]
                )

            records = [
                json.loads(line)
                for line in output_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(exit_code, 0)
        self.assertIn("raw_messages=2", stdout.getvalue())
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["source"], "kalshi")
        self.assertIn("message", records[0])

    def test_live_data_audit_rejects_real_order_submission_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unsafe.toml"
            path.write_text(
                """
[runtime]
mode = "paper_simulated"
confirm_live = false
allow_trade_mcp = false

[live_data]
enable_live_network = true

[order_api]
enable_order_api = true
allow_order_submission = true
""".strip(),
                encoding="utf-8",
            )
            stderr = StringIO()

            with redirect_stderr(stderr):
                exit_code = main(
                    [
                        "live-data",
                        "--config",
                        str(path),
                        "--audit-db",
                        str(Path(tmpdir) / "live.sqlite3"),
                        "--input-file",
                        str(path),
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("order submission enabled", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
