from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any

from kalshi_crypto.config import AppConfig
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.feed_health import FeedHealthMonitor
from kalshi_crypto.kalshi_auth import build_kalshi_auth_headers
from kalshi_crypto.live_feeds import (
    CoinbaseWebSocketSubscription,
    KalshiWebSocketSubscription,
)
from kalshi_crypto.storage import SQLiteAuditStore

KALSHI_WS_PATH = "/trade-api/ws/v2"
WEBSOCKET_MAX_MESSAGE_BYTES = 8 * 1024 * 1024
CLOCK_SKEW_TOLERANCE_MS = 1_000


@dataclass(frozen=True, slots=True)
class LiveDataSummary:
    raw_messages: int
    kalshi_messages: int
    coinbase_messages: int
    feed_unhealthy_events: int
    audit_events: int
    network: str


def events_from_kalshi_ws_message(
    raw_message: Mapping[str, Any],
    *,
    received_timestamp_ms: int,
) -> tuple[AuditEvent, ...]:
    message_type = str(raw_message.get("type", "unknown"))
    message_payload = _mapping_value(raw_message.get("msg"))
    source_timestamp_ms = _source_timestamp_ms(message_payload, received_timestamp_ms)
    market_ticker = _optional_str(
        message_payload.get("market_ticker")
        or raw_message.get("market_ticker")
        or message_payload.get("ticker")
    )
    payload = {
        "source": "kalshi_websocket",
        "message_type": message_type,
        "market_ticker": market_ticker,
        "sequence": _optional_int(raw_message.get("seq") or raw_message.get("sequence")),
        "sid": _optional_int(raw_message.get("sid")),
        "source_timestamp_ms": source_timestamp_ms,
        "received_timestamp_ms": received_timestamp_ms,
        "raw_message": raw_message,
    }
    return (
        AuditEvent.create(
            event_type=_kalshi_event_type(message_type),
            worker="market_monitor",
            payload=payload,
            causality_id="live-data",
            timestamp_ms=received_timestamp_ms,
        ),
    )


def events_from_coinbase_ws_message(
    raw_message: Mapping[str, Any],
    *,
    received_timestamp_ms: int,
) -> tuple[AuditEvent, ...]:
    channel = str(raw_message.get("channel", raw_message.get("type", "unknown")))
    source_timestamp_ms = _source_timestamp_ms(raw_message, received_timestamp_ms)
    events = raw_message.get("events")
    if isinstance(events, list) and events:
        return tuple(
            _coinbase_event_from_payload(
                channel=channel,
                event_payload=_mapping_value(event_payload),
                raw_message=raw_message,
                received_timestamp_ms=received_timestamp_ms,
                source_timestamp_ms=source_timestamp_ms,
            )
            for event_payload in events
        )

    return (
        _coinbase_event_from_payload(
            channel=channel,
            event_payload=_mapping_value(raw_message),
            raw_message=raw_message,
            received_timestamp_ms=received_timestamp_ms,
            source_timestamp_ms=source_timestamp_ms,
        ),
    )


def run_live_data_audit(
    *,
    config: AppConfig,
    audit_db: str | Path,
    max_seconds: int,
    kalshi_market_tickers: tuple[str, ...] = (),
    input_file: str | Path | None = None,
    output_file: str | Path | None = None,
) -> LiveDataSummary:
    _validate_live_data_config(config=config, max_seconds=max_seconds)
    store = SQLiteAuditStore(audit_db)
    monitor = FeedHealthMonitor(max_stale_ms=config.circuit_breakers.data_feed_stale_ms)

    if input_file is not None:
        messages = tuple(load_live_message_records(input_file))
        if output_file is not None:
            write_live_message_records(output_file, messages)
        return _append_live_message_records(
            messages=messages,
            store=store,
            monitor=monitor,
            network="not_attempted",
            timestamp_ms=_last_received_timestamp(messages),
        )

    messages = tuple(
        asyncio.run(
            _collect_network_messages(
                config=config,
                max_seconds=max_seconds,
                kalshi_market_tickers=kalshi_market_tickers,
            )
        )
    )
    if output_file is not None:
        write_live_message_records(output_file, messages)
    return _append_live_message_records(
        messages=messages,
        store=store,
        monitor=monitor,
        network="attempted",
        timestamp_ms=_last_received_timestamp(messages),
    )


@dataclass(frozen=True, slots=True)
class LiveMessageRecord:
    source: str
    message: Mapping[str, Any]
    received_timestamp_ms: int


def _validate_live_data_config(*, config: AppConfig, max_seconds: int) -> None:
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if not config.live_data.enable_live_network:
        raise SafetyError("live data requires live_data.enable_live_network = true")
    if config.order_api.allow_order_submission:
        raise SafetyError("live data audit refuses configs with order submission enabled")


def _append_live_message_records(
    *,
    messages: tuple[LiveMessageRecord, ...],
    store: SQLiteAuditStore,
    monitor: FeedHealthMonitor,
    network: str,
    timestamp_ms: int,
) -> LiveDataSummary:
    kalshi_messages = 0
    coinbase_messages = 0
    feed_unhealthy_events = 0
    audit_events = 0

    for record in messages:
        events = _events_from_live_message_record(record)
        for event in events:
            store.append(event)
            audit_events += 1
            health_event = _health_event(event, monitor)
            store.append(health_event)
            audit_events += 1
            if health_event.payload.get("healthy") is False:
                feed_unhealthy_events += 1
        if record.source == "kalshi":
            kalshi_messages += 1
        if record.source == "coinbase":
            coinbase_messages += 1

    summary_event = AuditEvent.create(
        event_type="LiveDataAuditCompleted",
        worker="market_monitor",
        payload={
            "raw_messages": len(messages),
            "kalshi_messages": kalshi_messages,
            "coinbase_messages": coinbase_messages,
            "feed_unhealthy_events": feed_unhealthy_events,
            "network": network,
            "execution": "not_attempted",
            "order_submission": "disabled",
        },
        causality_id="live-data",
        timestamp_ms=timestamp_ms,
    )
    store.append(summary_event)
    return LiveDataSummary(
        raw_messages=len(messages),
        kalshi_messages=kalshi_messages,
        coinbase_messages=coinbase_messages,
        feed_unhealthy_events=feed_unhealthy_events,
        audit_events=audit_events + 1,
        network=network,
    )


def _events_from_live_message_record(
    record: LiveMessageRecord,
) -> tuple[AuditEvent, ...]:
    if record.source == "kalshi":
        return events_from_kalshi_ws_message(
            record.message,
            received_timestamp_ms=record.received_timestamp_ms,
        )
    if record.source == "coinbase":
        return events_from_coinbase_ws_message(
            record.message,
            received_timestamp_ms=record.received_timestamp_ms,
        )
    raise ValueError(f"unsupported live message source: {record.source}")


def _health_event(event: AuditEvent, monitor: FeedHealthMonitor) -> AuditEvent:
    payload = event.payload
    source_timestamp_ms = int(payload["source_timestamp_ms"])
    received_timestamp_ms = int(payload["received_timestamp_ms"])
    if source_timestamp_ms > received_timestamp_ms:
        skew_ms = source_timestamp_ms - received_timestamp_ms
        if skew_ms > CLOCK_SKEW_TOLERANCE_MS:
            return AuditEvent.create(
                event_type="FeedHealthEvaluated",
                worker="market_monitor",
                payload={
                    "source": str(payload["source"]),
                    "healthy": False,
                    "reason": "clock_skew",
                    "age_ms": skew_ms,
                },
                causality_id=event.event_id,
                timestamp_ms=event.timestamp_ms,
            )
        source_timestamp_ms = received_timestamp_ms
    health = monitor.evaluate(
        source=str(payload["source"]),
        source_timestamp_ms=source_timestamp_ms,
        received_timestamp_ms=received_timestamp_ms,
    )
    return AuditEvent.create(
        event_type="FeedHealthEvaluated",
        worker="market_monitor",
        payload={
            "source": health.source,
            "healthy": health.healthy,
            "reason": health.reason,
            "age_ms": health.age_ms,
        },
        causality_id=event.event_id,
        timestamp_ms=event.timestamp_ms,
    )


def load_live_message_records(path: str | Path) -> list[LiveMessageRecord]:
    records: list[LiveMessageRecord] = []
    with Path(path).open("r", encoding="utf-8") as live_file:
        for line_number, line in enumerate(live_file, start=1):
            if not line.strip():
                continue
            try:
                raw_record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at live message line {line_number}") from exc
            records.append(_live_message_record(raw_record, line_number))
    return records


def write_live_message_records(
    path: str | Path,
    records: tuple[LiveMessageRecord, ...],
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as live_file:
        for record in records:
            live_file.write(
                json.dumps(
                    {
                        "source": record.source,
                        "received_timestamp_ms": record.received_timestamp_ms,
                        "message": record.message,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            live_file.write("\n")


def _live_message_record(raw_record: object, line_number: int) -> LiveMessageRecord:
    if not isinstance(raw_record, dict):
        raise ValueError(f"live message line {line_number} must contain an object")
    try:
        source = str(raw_record["source"]).lower()
        received_timestamp_ms = int(raw_record["received_timestamp_ms"])
        message = raw_record["message"]
    except KeyError as exc:
        raise ValueError(
            f"live message line {line_number} missing field {exc.args[0]}"
        ) from exc
    if source not in {"kalshi", "coinbase"}:
        raise ValueError(f"live message line {line_number} has unsupported source")
    if not isinstance(message, Mapping):
        raise ValueError(f"live message line {line_number} message must be an object")
    return LiveMessageRecord(
        source=source,
        message=dict(message),
        received_timestamp_ms=received_timestamp_ms,
    )


async def _collect_network_messages(
    *,
    config: AppConfig,
    max_seconds: int,
    kalshi_market_tickers: tuple[str, ...],
) -> list[LiveMessageRecord]:
    try:
        import websockets
    except ImportError as exc:
        raise SafetyError("live data capture requires the websockets package") from exc

    tasks = [
        _collect_coinbase_messages(
            websockets_module=websockets,
            config=config,
            max_seconds=max_seconds,
        )
    ]
    if kalshi_market_tickers:
        tasks.append(
            _collect_kalshi_messages(
                websockets_module=websockets,
                config=config,
                max_seconds=max_seconds,
                market_tickers=kalshi_market_tickers,
            )
        )
    nested = await asyncio.gather(*tasks)
    return [record for records in nested for record in records]


async def _collect_coinbase_messages(
    *,
    websockets_module: Any,
    config: AppConfig,
    max_seconds: int,
) -> list[LiveMessageRecord]:
    subscription = CoinbaseWebSocketSubscription(
        product_ids=config.live_data.coinbase_product_ids,
        channels=config.live_data.coinbase_channels,
    )
    return await _collect_websocket_messages(
        websockets_module=websockets_module,
        url=config.live_data.coinbase_ws_url,
        source="coinbase",
        subscription_messages=subscription.messages(),
        max_seconds=max_seconds,
        headers=None,
    )


async def _collect_kalshi_messages(
    *,
    websockets_module: Any,
    config: AppConfig,
    max_seconds: int,
    market_tickers: tuple[str, ...],
) -> list[LiveMessageRecord]:
    subscription = KalshiWebSocketSubscription(
        market_tickers=market_tickers,
        channels=config.live_data.kalshi_channels,
    )
    headers = _kalshi_ws_auth_headers()
    return await _collect_websocket_messages(
        websockets_module=websockets_module,
        url=config.live_data.kalshi_ws_url,
        source="kalshi",
        subscription_messages=subscription.messages(),
        max_seconds=max_seconds,
        headers=headers,
    )


async def _collect_websocket_messages(
    *,
    websockets_module: Any,
    url: str,
    source: str,
    subscription_messages: tuple[Mapping[str, Any], ...],
    max_seconds: int,
    headers: Mapping[str, str] | None,
) -> list[LiveMessageRecord]:
    records: list[LiveMessageRecord] = []
    deadline = time.monotonic() + max_seconds
    async with _connect_websocket(websockets_module, url, headers) as websocket:
        for message in subscription_messages:
            await websocket.send(json.dumps(message, separators=(",", ":")))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            except TimeoutError:
                break
            received_timestamp_ms = _now_ms()
            records.append(
                LiveMessageRecord(
                    source=source,
                    message=_decode_ws_message(raw_message),
                    received_timestamp_ms=received_timestamp_ms,
                )
            )
    return records


def _connect_websocket(
    websockets_module: Any,
    url: str,
    headers: Mapping[str, str] | None,
) -> Any:
    if headers is None:
        return websockets_module.connect(url, max_size=WEBSOCKET_MAX_MESSAGE_BYTES)
    try:
        return websockets_module.connect(
            url,
            additional_headers=headers,
            max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
        )
    except TypeError:
        return websockets_module.connect(
            url,
            extra_headers=headers,
            max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
        )


def _kalshi_ws_auth_headers() -> Mapping[str, str]:
    key_id = os.environ.get("KALSHI_KEY_ID")
    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    if not key_id or not private_key_pem:
        raise SafetyError(
            "Kalshi live WebSocket capture requires KALSHI_KEY_ID and "
            "KALSHI_PRIVATE_KEY_PEM environment variables"
        )
    timestamp_ms = _now_ms()
    headers = build_kalshi_auth_headers(
        key_id=key_id,
        timestamp_ms=timestamp_ms,
        method="GET",
        path=KALSHI_WS_PATH,
        signer=_rsa_pss_sha256_signer(private_key_pem),
    )
    return headers.as_mapping()


def _rsa_pss_sha256_signer(private_key_pem: str) -> Any:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as exc:
        raise SafetyError("Kalshi signing requires the cryptography package") from exc

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )

    def signer(message: str) -> str:
        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        import base64

        return base64.b64encode(signature).decode("ascii")

    return signer


def _decode_ws_message(raw_message: object) -> Mapping[str, Any]:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    if not isinstance(raw_message, str):
        raise ValueError("websocket message must be text or bytes")
    decoded = json.loads(raw_message)
    if not isinstance(decoded, Mapping):
        raise ValueError("websocket message must contain a JSON object")
    return dict(decoded)


def _coinbase_event_from_payload(
    *,
    channel: str,
    event_payload: Mapping[str, Any],
    raw_message: Mapping[str, Any],
    received_timestamp_ms: int,
    source_timestamp_ms: int,
) -> AuditEvent:
    product_id = _optional_str(event_payload.get("product_id"))
    updates = event_payload.get("updates")
    tickers = event_payload.get("tickers")
    return AuditEvent.create(
        event_type=_coinbase_event_type(channel),
        worker="market_monitor",
        payload={
            "source": "coinbase_websocket",
            "channel": channel,
            "event_type": _optional_str(event_payload.get("type")),
            "product_id": product_id,
            "update_count": len(updates) if isinstance(updates, list) else 0,
            "ticker_count": len(tickers) if isinstance(tickers, list) else 0,
            "sequence": _optional_int(raw_message.get("sequence_num")),
            "source_timestamp_ms": source_timestamp_ms,
            "received_timestamp_ms": received_timestamp_ms,
            "raw_message": raw_message,
        },
        causality_id="live-data",
        timestamp_ms=received_timestamp_ms,
    )


def _kalshi_event_type(message_type: str) -> str:
    return {
        "ticker": "KalshiTickerReceived",
        "orderbook_snapshot": "KalshiOrderBookSnapshotReceived",
        "orderbook_delta": "KalshiOrderBookDeltaReceived",
        "market_lifecycle_v2": "KalshiMarketLifecycleReceived",
        "error": "KalshiWebSocketErrorReceived",
    }.get(message_type, "KalshiWebSocketMessageReceived")


def _coinbase_event_type(channel: str) -> str:
    normalized = channel.lower()
    if normalized in {"l2_data", "level2"}:
        return "CoinbaseLevel2Received"
    if normalized in {"ticker", "ticker_batch"}:
        return "CoinbaseTickerReceived"
    return "CoinbaseWebSocketMessageReceived"


def _source_timestamp_ms(
    raw_payload: Mapping[str, Any],
    received_timestamp_ms: int,
) -> int:
    for key in (
        "source_timestamp_ms",
        "timestamp_ms",
        "ts",
        "time_ms",
        "event_time_ms",
    ):
        value = raw_payload.get(key)
        if value is not None:
            parsed = _optional_int(value)
            if parsed is not None:
                return parsed
    for key in ("timestamp", "time", "event_time", "updated_ts"):
        value = raw_payload.get(key)
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return received_timestamp_ms


def _parse_timestamp(value: object) -> int | None:
    parsed_int = _optional_int(value)
    if parsed_int is not None:
        return parsed_int
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _mapping_value(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _last_received_timestamp(messages: tuple[LiveMessageRecord, ...]) -> int:
    if not messages:
        return _now_ms()
    return max(message.received_timestamp_ms for message in messages)


def _now_ms() -> int:
    return int(time.time() * 1000)
