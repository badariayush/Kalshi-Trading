from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from typing import Any, TextIO

from kalshi_crypto.config import AppConfig
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.feed_health import FeedHealthMonitor
from kalshi_crypto.kalshi_auth import build_kalshi_auth_headers
from kalshi_crypto.live_feeds import (
    CoinbaseWebSocketSubscription,
    KalshiWebSocketSubscription,
)
from kalshi_crypto.paper_strategy import (
    PaperFeedRecord,
    PaperMarket,
    PaperRealtimeState,
    PaperSimulatedExit,
    PaperSimulatedOrder,
    PaperStrategyResult,
    advance_realtime_paper,
    evaluate_live_paper_strategy,
)
from kalshi_crypto.storage import SQLiteAuditStore

KALSHI_WS_PATH = "/trade-api/ws/v2"
WEBSOCKET_MAX_MESSAGE_BYTES = 8 * 1024 * 1024
CLOCK_SKEW_TOLERANCE_MS = 1_000
RecordHandler = Callable[["LiveMessageRecord"], None]


@dataclass(frozen=True, slots=True)
class LiveDataSummary:
    raw_messages: int
    kalshi_messages: int
    coinbase_messages: int
    feed_unhealthy_events: int
    simulated_orders: int
    audit_events: int
    network: str
    simulated_positions_closed: int = 0
    simulated_realized_pnl: Decimal = Decimal("0")


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
    stdout: TextIO | None = None,
    paper_market: PaperMarket | None = None,
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
            stdout=stdout,
            config=config,
            paper_market=paper_market,
        )

    realtime_state = PaperRealtimeState.empty()

    def on_record(record: LiveMessageRecord) -> None:
        nonlocal realtime_state
        if paper_market is None:
            return
        if (
            config.circuit_breakers.halt_new_entries_on_feed_unhealthy
            and not _record_feed_is_healthy(
                record,
                monitor,
                future_clock_skew_tolerance_ms=(
                    config.circuit_breakers.future_clock_skew_tolerance_ms
                ),
            )
        ):
            return
        step = advance_realtime_paper(
            state=realtime_state,
            record=_paper_feed_record(record),
            market=paper_market,
            config=config,
        )
        realtime_state = step.state
        for simulated_order in step.orders:
            store.append(_simulated_order_event(simulated_order, paper_market))
            _print_simulated_order(stdout, simulated_order)
        if step.exit is not None:
            store.append(_simulated_exit_event(step.exit))
            _print_simulated_exit(stdout, step.exit)

    messages = tuple(
        sorted(
            asyncio.run(
                _collect_network_messages(
                    config=config,
                    max_seconds=max_seconds,
                    kalshi_market_tickers=kalshi_market_tickers,
                    on_record=on_record,
                )
            ),
            key=lambda record: record.received_timestamp_ms,
        )
    )
    if output_file is not None:
        write_live_message_records(output_file, messages)
    paper_result = None
    if paper_market is not None:
        paper_result = evaluate_live_paper_strategy(
            records=realtime_state.records,
            market=paper_market,
            config=config,
        )
    return _append_live_message_records(
        messages=messages,
        store=store,
        monitor=monitor,
        network="attempted",
        timestamp_ms=_last_received_timestamp(messages),
        stdout=stdout,
        config=config,
        paper_market=paper_market,
        paper_result=paper_result,
        paper_events_stored=True,
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
    stdout: TextIO | None,
    config: AppConfig,
    paper_market: PaperMarket | None,
    paper_result: PaperStrategyResult | None = None,
    paper_events_stored: bool = False,
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
            health_event = _health_event(
                event,
                monitor,
                future_clock_skew_tolerance_ms=(
                    config.circuit_breakers.future_clock_skew_tolerance_ms
                ),
            )
            store.append(health_event)
            audit_events += 1
            if health_event.payload.get("healthy") is False:
                feed_unhealthy_events += 1
        if record.source == "kalshi":
            kalshi_messages += 1
        if record.source == "coinbase":
            coinbase_messages += 1

    simulated_orders = 0
    simulated_positions_closed = 0
    simulated_realized_pnl = Decimal("0")
    if paper_market is not None:
        strategy_result = paper_result or evaluate_live_paper_strategy(
            records=tuple(_paper_feed_record(message) for message in messages),
            market=paper_market,
            config=config,
        )
        simulated_orders = len(strategy_result.orders)
        if not paper_events_stored:
            for simulated_order in strategy_result.orders:
                store.append(_simulated_order_event(simulated_order, paper_market))
                audit_events += 1
                _print_simulated_order(stdout, simulated_order)
            if strategy_result.exit is not None:
                store.append(_simulated_exit_event(strategy_result.exit))
                audit_events += 1
                _print_simulated_exit(stdout, strategy_result.exit)
        else:
            audit_events += len(strategy_result.orders)
            audit_events += 1 if strategy_result.exit is not None else 0
        if strategy_result.exit is not None:
            simulated_positions_closed = 1
            simulated_realized_pnl = strategy_result.exit.realized_pnl
        if not strategy_result.orders:
            store.append(
                AuditEvent.create(
                    event_type="PaperTradeSkipped",
                    worker="risk",
                    payload={
                        "market_ticker": paper_market.ticker,
                        "reason": strategy_result.skip_reason or "no_authorized_entry",
                        "source": "live_feeds",
                        "execution": "not_attempted",
                        "order_submission": "disabled",
                    },
                    causality_id=paper_market.ticker,
                    timestamp_ms=timestamp_ms,
                )
            )
            audit_events += 1
            if stdout is not None:
                print(
                    "simulated_order_skipped="
                    f"market_ticker={paper_market.ticker} "
                    f"reason={strategy_result.skip_reason or 'no_authorized_entry'}",
                    file=stdout,
                )

    summary_event = AuditEvent.create(
        event_type="LiveDataAuditCompleted",
        worker="market_monitor",
        payload={
            "raw_messages": len(messages),
            "kalshi_messages": kalshi_messages,
            "coinbase_messages": coinbase_messages,
            "feed_unhealthy_events": feed_unhealthy_events,
            "simulated_orders": simulated_orders,
            "simulated_positions_closed": simulated_positions_closed,
            "simulated_realized_pnl": _decimal_text(simulated_realized_pnl),
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
        simulated_orders=simulated_orders,
        audit_events=audit_events + 1,
        network=network,
        simulated_positions_closed=simulated_positions_closed,
        simulated_realized_pnl=simulated_realized_pnl,
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


def _health_event(
    event: AuditEvent,
    monitor: FeedHealthMonitor,
    *,
    future_clock_skew_tolerance_ms: int = CLOCK_SKEW_TOLERANCE_MS,
) -> AuditEvent:
    payload = event.payload
    source_timestamp_ms = int(payload["source_timestamp_ms"])
    received_timestamp_ms = int(payload["received_timestamp_ms"])
    if source_timestamp_ms > received_timestamp_ms:
        skew_ms = source_timestamp_ms - received_timestamp_ms
        if skew_ms > future_clock_skew_tolerance_ms:
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


def _record_feed_is_healthy(
    record: LiveMessageRecord,
    monitor: FeedHealthMonitor,
    *,
    future_clock_skew_tolerance_ms: int = CLOCK_SKEW_TOLERANCE_MS,
) -> bool:
    return all(
        _health_event(
            event,
            monitor,
            future_clock_skew_tolerance_ms=future_clock_skew_tolerance_ms,
        ).payload.get("healthy")
        is True
        for event in _events_from_live_message_record(record)
    )


def _simulated_order_event(
    simulated_order: PaperSimulatedOrder,
    paper_market: PaperMarket,
) -> AuditEvent:
    return AuditEvent.create(
        event_type="SimulatedOrderPlaced",
        worker="execution",
        payload={
            "market_ticker": simulated_order.market_ticker,
            "action": "buy",
            "side": simulated_order.side,
            "price": _decimal_text(simulated_order.price),
            "quantity": simulated_order.quantity,
            "fee": _decimal_text(simulated_order.fee),
            "coinbase_product_id": simulated_order.coinbase_product_id,
            "coinbase_price": _decimal_text(simulated_order.coinbase_price),
            "reason": simulated_order.reason,
            "leg_index": simulated_order.leg_index,
            "probability_yes": _optional_decimal_text(
                simulated_order.probability_yes
            ),
            "confidence": _optional_decimal_text(simulated_order.confidence),
            "event_ticker": paper_market.event_ticker,
            "market_open_time_ms": paper_market.open_time_ms,
            "market_close_time_ms": paper_market.close_time_ms,
            "market_strike": _decimal_text(paper_market.strike),
            "source": "live_feeds",
            "execution": "simulated_fill_only",
            "order_submission": "disabled",
            "real_order_submitted": False,
        },
        causality_id=simulated_order.market_ticker,
        timestamp_ms=simulated_order.timestamp_ms,
    )


def _simulated_exit_event(simulated_exit: PaperSimulatedExit) -> AuditEvent:
    return AuditEvent.create(
        event_type="PositionClosed",
        worker="execution",
        payload={
            "market_ticker": simulated_exit.market_ticker,
            "side": simulated_exit.side,
            "exit_price": _decimal_text(simulated_exit.price),
            "quantity": simulated_exit.quantity,
            "exit_fee": _decimal_text(simulated_exit.fee),
            "total_fees": _decimal_text(simulated_exit.total_fees),
            "realized_pnl": _decimal_text(simulated_exit.realized_pnl),
            "outcome": _pnl_outcome(simulated_exit.realized_pnl),
            "exit_reason": simulated_exit.reason,
            "simulated": True,
            "source": "live_feeds",
            "execution": "simulated_fill_only",
            "order_submission": "disabled",
            "real_order_submitted": False,
        },
        causality_id=simulated_exit.market_ticker,
        timestamp_ms=simulated_exit.timestamp_ms,
    )


def _print_simulated_order(
    stdout: TextIO | None,
    simulated_order: PaperSimulatedOrder,
) -> None:
    if stdout is None:
        return
    print(
        "simulated_order_placed="
        f"market_ticker={simulated_order.market_ticker} "
        f"leg={simulated_order.leg_index} "
        f"reason={simulated_order.reason} "
        f"side={simulated_order.side} "
        f"price={_decimal_text(simulated_order.price)} "
        f"quantity={simulated_order.quantity} "
        f"coinbase_product_id={simulated_order.coinbase_product_id} "
        f"coinbase_price={_decimal_text(simulated_order.coinbase_price)} "
        "source=live_feeds execution=simulated_fill_only "
        "order_submission=disabled",
        file=stdout,
    )


def _print_simulated_exit(
    stdout: TextIO | None,
    simulated_exit: PaperSimulatedExit,
) -> None:
    if stdout is None:
        return
    print(
        "simulated_exit_filled="
        f"market_ticker={simulated_exit.market_ticker} "
        f"reason={simulated_exit.reason} "
        f"side={simulated_exit.side} "
        f"price={_decimal_text(simulated_exit.price)} "
        f"quantity={simulated_exit.quantity} "
        f"realized_pnl={_decimal_text(simulated_exit.realized_pnl)} "
        f"total_fees={_decimal_text(simulated_exit.total_fees)} "
        "source=live_feeds execution=simulated_fill_only "
        "order_submission=disabled",
        file=stdout,
    )


def _pnl_outcome(pnl: Decimal) -> str:
    if pnl > Decimal("0"):
        return "profit"
    if pnl < Decimal("0"):
        return "loss"
    return "flat"


def _paper_feed_record(record: LiveMessageRecord) -> PaperFeedRecord:
    return PaperFeedRecord(
        source=record.source,
        message=record.message,
        received_timestamp_ms=record.received_timestamp_ms,
    )


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


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
    on_record: RecordHandler | None = None,
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
            on_record=on_record,
        )
    ]
    if kalshi_market_tickers:
        tasks.append(
            _collect_kalshi_messages(
                websockets_module=websockets,
                config=config,
                max_seconds=max_seconds,
                market_tickers=kalshi_market_tickers,
                on_record=on_record,
            )
        )
    nested = await asyncio.gather(*tasks)
    return [record for records in nested for record in records]


async def _collect_coinbase_messages(
    *,
    websockets_module: Any,
    config: AppConfig,
    max_seconds: int,
    on_record: RecordHandler | None = None,
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
        on_record=on_record,
    )


async def _collect_kalshi_messages(
    *,
    websockets_module: Any,
    config: AppConfig,
    max_seconds: int,
    market_tickers: tuple[str, ...],
    on_record: RecordHandler | None = None,
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
        on_record=on_record,
    )


async def _collect_websocket_messages(
    *,
    websockets_module: Any,
    url: str,
    source: str,
    subscription_messages: tuple[Mapping[str, Any], ...],
    max_seconds: int,
    headers: Mapping[str, str] | None,
    on_record: RecordHandler | None = None,
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
            record = LiveMessageRecord(
                source=source,
                message=_decode_ws_message(raw_message),
                received_timestamp_ms=received_timestamp_ms,
            )
            records.append(record)
            if on_record is not None:
                on_record(record)
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
    key_id = _kalshi_key_id_from_env()
    private_key_pem = _kalshi_private_key_pem_from_env()
    timestamp_ms = _now_ms()
    headers = build_kalshi_auth_headers(
        key_id=key_id,
        timestamp_ms=timestamp_ms,
        method="GET",
        path=KALSHI_WS_PATH,
        signer=_rsa_pss_sha256_signer(private_key_pem),
    )
    return headers.as_mapping()


def _kalshi_key_id_from_env() -> str:
    for env_name in ("KALSHI_API_KEY_ID", "KALSHI_KEY_ID"):
        value = os.environ.get(env_name)
        if value:
            return value
    raise SafetyError(
        "Kalshi live WebSocket capture requires KALSHI_API_KEY_ID"
    )


def _kalshi_private_key_pem_from_env() -> str:
    private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if private_key_path:
        path = Path(private_key_path).expanduser()
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SafetyError(
                f"could not read KALSHI_PRIVATE_KEY_PATH file: {path}"
            ) from exc

    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    if private_key_pem:
        return private_key_pem

    raise SafetyError(
        "Kalshi live WebSocket capture requires KALSHI_PRIVATE_KEY_PATH"
    )


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
        "time_ms",
        "event_time_ms",
    ):
        value = raw_payload.get(key)
        if value is not None:
            parsed = _optional_int(value)
            if parsed is not None:
                return parsed
    ts_value = raw_payload.get("ts")
    if ts_value is not None:
        parsed_ts = _optional_int(ts_value)
        if parsed_ts is not None:
            return _normalize_epoch_timestamp_ms(parsed_ts)
    for key in ("timestamp", "time", "event_time", "updated_ts"):
        value = raw_payload.get(key)
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    return received_timestamp_ms


def _normalize_epoch_timestamp_ms(value: int) -> int:
    if value < 10_000_000_000:
        return value * 1000
    return value


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
