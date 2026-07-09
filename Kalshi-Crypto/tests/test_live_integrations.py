from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import asyncio
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kalshi_crypto.cli import main
from kalshi_crypto.config import load_app_config
from kalshi_crypto.live_config import LiveDataConfig, OrderApiConfig
from kalshi_crypto.live_feeds import (
    CoinbaseWebSocketSubscription,
    KalshiWebSocketSubscription,
)
from kalshi_crypto.kalshi_auth import KalshiAuthConfig
from kalshi_crypto.kalshi_auth import build_kalshi_auth_headers, kalshi_signature_message
from kalshi_crypto.kalshi_orders import KalshiOrderRequest
from kalshi_crypto.live_collectors import (
    LiveMessageRecord,
    _collect_websocket_messages,
    _kalshi_key_id_from_env,
    _kalshi_private_key_pem_from_env,
    _record_feed_is_healthy,
    events_from_coinbase_ws_message,
    events_from_kalshi_ws_message,
    run_live_data_audit,
)
from kalshi_crypto.feed_health import FeedHealthMonitor
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
        config = KalshiAuthConfig()

        self.assertEqual(
            config.required_env_vars(),
            ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH"),
        )

    def test_kalshi_auth_accepts_previous_algorithm_environment_names(self) -> None:
        with patch.dict(os.environ, {"KALSHI_API_KEY_ID": "legacy-key-id"}, clear=True):
            self.assertEqual(_kalshi_key_id_from_env(), "legacy-key-id")

    def test_kalshi_auth_prefers_previous_algorithm_key_id_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "KALSHI_API_KEY_ID": "file-path-style-key-id",
                "KALSHI_KEY_ID": "pem-style-key-id",
            },
            clear=True,
        ):
            self.assertEqual(_kalshi_key_id_from_env(), "file-path-style-key-id")

    def test_kalshi_auth_reads_previous_algorithm_private_key_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "kalshi_private_key.pem"
            key_path.write_text(
                "-----BEGIN PRIVATE KEY-----\nbody\n-----END PRIVATE KEY-----\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"KALSHI_PRIVATE_KEY_PATH": str(key_path)},
                clear=True,
            ):
                self.assertEqual(
                    _kalshi_private_key_pem_from_env(),
                    "-----BEGIN PRIVATE KEY-----\nbody\n-----END PRIVATE KEY-----\n",
                )

    def test_kalshi_auth_prefers_private_key_path_over_raw_pem_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "kalshi_private_key.pem"
            key_path.write_text(
                "-----BEGIN PRIVATE KEY-----\npath\n-----END PRIVATE KEY-----\n",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {
                    "KALSHI_PRIVATE_KEY_PATH": str(key_path),
                    "KALSHI_PRIVATE_KEY_PEM": (
                        "-----BEGIN PRIVATE KEY-----\n"
                        "env\n"
                        "-----END PRIVATE KEY-----\n"
                    ),
                },
                clear=True,
            ):
                self.assertEqual(
                    _kalshi_private_key_pem_from_env(),
                    "-----BEGIN PRIVATE KEY-----\npath\n-----END PRIVATE KEY-----\n",
                )

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

    def test_realtime_strategy_gate_rejects_stale_and_clock_skewed_records(self) -> None:
        monitor = FeedHealthMonitor(max_stale_ms=2_500)
        healthy = LiveMessageRecord(
            source="kalshi",
            received_timestamp_ms=1_700_000_000_500,
            message={
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "source_timestamp_ms": 1_700_000_000_000,
                },
            },
        )
        stale = LiveMessageRecord(
            source="kalshi",
            received_timestamp_ms=1_700_000_003_000,
            message={
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "source_timestamp_ms": 1_700_000_000_000,
                },
            },
        )
        skewed = LiveMessageRecord(
            source="kalshi",
            received_timestamp_ms=1_700_000_000_000,
            message={
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "source_timestamp_ms": 1_700_000_001_001,
                },
            },
        )

        self.assertTrue(_record_feed_is_healthy(healthy, monitor))
        self.assertFalse(_record_feed_is_healthy(stale, monitor))
        self.assertFalse(_record_feed_is_healthy(skewed, monitor))

    def test_realtime_strategy_gate_tolerates_configured_provider_clock_jitter(self) -> None:
        monitor = FeedHealthMonitor(max_stale_ms=2_500)
        observed_kalshi_jitter = LiveMessageRecord(
            source="kalshi",
            received_timestamp_ms=1_782_617_403_538,
            message={
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "source_timestamp_ms": 1_782_617_404_644,
                },
            },
        )
        genuinely_skewed = LiveMessageRecord(
            source="kalshi",
            received_timestamp_ms=1_782_617_403_538,
            message={
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "source_timestamp_ms": 1_782_617_408_539,
                },
            },
        )

        self.assertTrue(
            _record_feed_is_healthy(
                observed_kalshi_jitter,
                monitor,
                future_clock_skew_tolerance_ms=1_500,
            )
        )
        self.assertFalse(
            _record_feed_is_healthy(
                genuinely_skewed,
                monitor,
                future_clock_skew_tolerance_ms=1_500,
            )
        )

    def test_kalshi_ticker_numeric_seconds_timestamp_is_normalized_to_milliseconds(self) -> None:
        event = events_from_kalshi_ws_message(
            {
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "ts": 1_782_646_202,
                },
            },
            received_timestamp_ms=1_782_646_202_649,
        )[0]
        monitor = FeedHealthMonitor(max_stale_ms=2_500)
        record = LiveMessageRecord(
            source="kalshi",
            received_timestamp_ms=1_782_646_202_649,
            message={
                "type": "ticker",
                "msg": {
                    "market_ticker": "KXBTCD-TEST",
                    "ts": 1_782_646_202,
                },
            },
        )

        self.assertEqual(event.payload["source_timestamp_ms"], 1_782_646_202_000)
        self.assertTrue(_record_feed_is_healthy(record, monitor))

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
                ["doctor-live-data", "--config", "configs/live.example.toml"]
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
mode = "live_data"
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

    def test_live_data_cli_requires_actual_kalshi_market_ticker(self) -> None:
        stderr = StringIO()

        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "live-data",
                    "--config",
                    "configs/live.example.toml",
                    "--audit-db",
                    "/private/tmp/unused.sqlite3",
                    "--max-seconds",
                    "10",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("--kalshi-market-ticker", stderr.getvalue())

    def test_internal_live_data_audit_parser_never_executes_orders(self) -> None:
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
            summary = run_live_data_audit(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=10,
                kalshi_market_tickers=("KXBTCD-TEST",),
                input_file=live_file,
            )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(summary.network, "not_attempted")
        self.assertEqual(summary.raw_messages, 2)
        self.assertEqual(summary.feed_unhealthy_events, 0)
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

    def test_live_data_audit_prints_simulated_order_only_from_live_feed_prices(self) -> None:
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
                                        "yes_ask_dollars": "0.48",
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
                                    "channel": "ticker",
                                    "timestamp_ms": 1_700_000_000_000,
                                    "events": [
                                        {
                                            "type": "update",
                                            "tickers": [
                                                {
                                                    "product_id": "BTC-USD",
                                                    "price": "100000.00",
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
            summary = run_live_data_audit(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=10,
                kalshi_market_tickers=("KXBTCD-TEST",),
                input_file=live_file,
                stdout=stdout,
            )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(summary.simulated_orders, 0)
        self.assertNotIn("simulated_order_placed=", stdout.getvalue())
        self.assertEqual(records[-1]["event_type"], "LiveDataAuditCompleted")
        self.assertEqual(records[-1]["payload"]["simulated_orders"], 0)

    def test_live_data_audit_prints_simulated_order_when_prior_feed_events_are_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            live_file = Path(tmpdir) / "live-messages.jsonl"
            audit_db = Path(tmpdir) / "live.sqlite3"
            live_file.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "source": "coinbase",
                                "received_timestamp_ms": 1_700_000_010_000,
                                "message": {
                                    "channel": "ticker",
                                    "timestamp_ms": 1_700_000_000_000,
                                    "events": [
                                        {
                                            "type": "update",
                                            "tickers": [
                                                {
                                                    "product_id": "BTC-USD",
                                                    "price": "100000.00",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "source": "kalshi",
                                "received_timestamp_ms": 1_700_000_010_050,
                                "message": {
                                    "type": "ticker",
                                    "seq": 1,
                                    "msg": {
                                        "market_ticker": "KXBTCD-TEST",
                                        "yes_ask_dollars": "0.48",
                                        "source_timestamp_ms": 1_700_000_000_000,
                                    },
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout = StringIO()
            summary = run_live_data_audit(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=10,
                kalshi_market_tickers=("KXBTCD-TEST",),
                input_file=live_file,
                stdout=stdout,
            )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertGreater(summary.feed_unhealthy_events, 0)
        self.assertEqual(summary.simulated_orders, 0)
        self.assertNotIn("simulated_order_placed=", stdout.getvalue())
        self.assertEqual(records[-1]["event_type"], "LiveDataAuditCompleted")
        self.assertEqual(records[-1]["payload"]["simulated_orders"], 0)

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
            summary = run_live_data_audit(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=10,
                kalshi_market_tickers=("KXBTCD-TEST",),
                input_file=live_file,
            )

            records = SQLiteAuditStore(audit_db).read_all()

        self.assertEqual(summary.feed_unhealthy_events, 0)
        self.assertEqual(records[1]["event_type"], "FeedHealthEvaluated")
        self.assertEqual(records[1]["payload"]["healthy"], True)

    def test_live_data_audit_can_write_provider_message_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "captured.jsonl"
            audit_db = Path(tmpdir) / "live.sqlite3"
            live_file = Path(tmpdir) / "live-messages.jsonl"
            live_file.write_text(
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
                )
                + "\n",
                encoding="utf-8",
            )
            summary = run_live_data_audit(
                config=load_app_config("configs/live.example.toml"),
                audit_db=audit_db,
                max_seconds=10,
                kalshi_market_tickers=("KXBTCD-TEST",),
                input_file=live_file,
                output_file=output_file,
            )

            records = [
                json.loads(line)
                for line in output_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary.raw_messages, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source"], "kalshi")
        self.assertIn("message", records[0])

    def test_websocket_collector_delivers_each_record_to_realtime_handler(self) -> None:
        handled = []

        class FakeWebSocket:
            def __init__(self) -> None:
                self.received = 0

            async def __aenter__(self) -> "FakeWebSocket":
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def send(self, _message: str) -> None:
                return None

            async def recv(self) -> str:
                if self.received:
                    raise TimeoutError
                self.received += 1
                return json.dumps({"channel": "ticker", "events": []})

        class FakeWebSockets:
            @staticmethod
            def connect(*_args: object, **_kwargs: object) -> FakeWebSocket:
                return FakeWebSocket()

        records = asyncio.run(
            _collect_websocket_messages(
                websockets_module=FakeWebSockets,
                url="wss://example.test",
                source="coinbase",
                subscription_messages=({"type": "subscribe"},),
                max_seconds=1,
                headers=None,
                on_record=handled.append,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(handled, records)

    def test_live_data_audit_rejects_real_order_submission_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unsafe.toml"
            path.write_text(
                """
[runtime]
mode = "live_data"
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
                        "--kalshi-market-ticker",
                        "KXBTCD-TEST",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertIn("order submission enabled", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
