from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from pathlib import Path
from typing import Iterable

from kalshi_crypto.candles import CFBenchmarkTick
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.market_lifecycle import RawKalshiMarketReplay
from kalshi_crypto.orderbook import PriceLevel


@dataclass(frozen=True, slots=True)
class RawKalshiOrderBookReplay:
    market_ticker: str
    yes_bids: tuple[PriceLevel, ...]
    no_bids: tuple[PriceLevel, ...]
    source_timestamp_ms: int
    received_timestamp_ms: int


ReplayItem = AuditEvent | RawKalshiOrderBookReplay | RawKalshiMarketReplay | CFBenchmarkTick


def load_replay_events(path: str | Path) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    for item in load_replay_items(path):
        if not isinstance(item, AuditEvent):
            raise ValueError("replay file contains raw records where events were expected")
        events.append(item)
    return events


def load_replay_items(path: str | Path) -> list[ReplayItem]:
    replay_path = Path(path)
    items: list[ReplayItem] = []
    with replay_path.open("r", encoding="utf-8") as replay_file:
        for line_number, line in enumerate(replay_file, start=1):
            if not line.strip():
                continue
            try:
                raw_item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at replay line {line_number}") from exc
            items.append(_item_from_dict(raw_item, line_number))
    return items


def _item_from_dict(raw_item: object, line_number: int) -> ReplayItem:
    if not isinstance(raw_item, dict):
        raise ValueError(f"replay line {line_number} must contain an object")
    if raw_item.get("record_type") == "kalshi_orderbook":
        return _raw_orderbook_from_dict(raw_item, line_number)
    if raw_item.get("record_type") == "kalshi_market":
        return _raw_market_from_dict(raw_item, line_number)
    if raw_item.get("record_type") == "cf_benchmark_tick":
        return _cf_benchmark_tick_from_dict(raw_item, line_number)
    return _event_from_dict(raw_item, line_number)


def _event_from_dict(raw_event: object, line_number: int) -> AuditEvent:
    if not isinstance(raw_event, dict):
        raise ValueError(f"replay line {line_number} must contain an event object")

    try:
        return AuditEvent.create(
            event_id=str(raw_event["event_id"]),
            event_type=str(raw_event["event_type"]),
            worker=str(raw_event["worker"]),
            timestamp_ms=int(raw_event["timestamp_ms"]),
            causality_id=str(raw_event["causality_id"]),
            payload=_payload(raw_event["payload"], line_number),
        )
    except KeyError as exc:
        raise ValueError(f"replay line {line_number} missing field {exc.args[0]}") from exc


def _payload(value: object, line_number: int) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"replay line {line_number} payload must be an object")
    return value


def iter_replay_events(path: str | Path) -> Iterable[AuditEvent]:
    return iter(load_replay_events(path))


def iter_replay_items(path: str | Path) -> Iterable[ReplayItem]:
    return iter(load_replay_items(path))


def _raw_orderbook_from_dict(
    raw_item: dict[str, object],
    line_number: int,
) -> RawKalshiOrderBookReplay:
    try:
        market_ticker = str(raw_item["market_ticker"])
        source_timestamp_ms = int(raw_item["source_timestamp_ms"])
        received_timestamp_ms = int(raw_item["received_timestamp_ms"])
        yes_bids = _price_levels(raw_item["yes_bids"], line_number, "yes_bids")
        no_bids = _price_levels(raw_item["no_bids"], line_number, "no_bids")
    except KeyError as exc:
        raise ValueError(f"replay line {line_number} missing field {exc.args[0]}") from exc

    return RawKalshiOrderBookReplay(
        market_ticker=market_ticker,
        yes_bids=yes_bids,
        no_bids=no_bids,
        source_timestamp_ms=source_timestamp_ms,
        received_timestamp_ms=received_timestamp_ms,
    )


def _price_levels(
    raw_levels: object,
    line_number: int,
    field_name: str,
) -> tuple[PriceLevel, ...]:
    if not isinstance(raw_levels, list):
        raise ValueError(f"replay line {line_number} {field_name} must be a list")
    levels: list[PriceLevel] = []
    for index, raw_level in enumerate(raw_levels):
        if not isinstance(raw_level, dict):
            raise ValueError(
                f"replay line {line_number} {field_name}[{index}] must be an object"
            )
        try:
            levels.append(
                PriceLevel(
                    price=Decimal(str(raw_level["price"])),
                    quantity=int(raw_level["quantity"]),
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"replay line {line_number} {field_name}[{index}] missing field {exc.args[0]}"
            ) from exc
    return tuple(levels)


def _raw_market_from_dict(
    raw_item: dict[str, object],
    line_number: int,
) -> RawKalshiMarketReplay:
    try:
        return RawKalshiMarketReplay(
            market_ticker=str(raw_item["market_ticker"]),
            series_ticker=str(raw_item["series_ticker"]),
            underlying=str(raw_item["underlying"]),
            strike=str(raw_item["strike"]),
            lifecycle_status=str(raw_item["lifecycle_status"]),
            open_timestamp_ms=int(raw_item["open_timestamp_ms"]),
            close_timestamp_ms=int(raw_item["close_timestamp_ms"]),
            received_timestamp_ms=int(raw_item["received_timestamp_ms"]),
        )
    except KeyError as exc:
        raise ValueError(f"replay line {line_number} missing field {exc.args[0]}") from exc


def _cf_benchmark_tick_from_dict(
    raw_item: dict[str, object],
    line_number: int,
) -> CFBenchmarkTick:
    try:
        return CFBenchmarkTick(
            index_ticker=str(raw_item["index_ticker"]),
            price=Decimal(str(raw_item["price"])),
            source_timestamp_ms=int(raw_item["source_timestamp_ms"]),
            received_timestamp_ms=int(raw_item["received_timestamp_ms"]),
        )
    except KeyError as exc:
        raise ValueError(f"replay line {line_number} missing field {exc.args[0]}") from exc
