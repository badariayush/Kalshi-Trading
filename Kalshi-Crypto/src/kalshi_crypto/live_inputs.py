from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from kalshi_crypto.candles import CFBenchmarkTick, Candle, build_candles
from kalshi_crypto.live_collectors import LiveMessageRecord, load_live_message_records
from kalshi_crypto.market_lifecycle import RawKalshiMarketReplay
from kalshi_crypto.models import BookQuote
from kalshi_crypto.orderbook import (
    NormalizedOrderBook,
    PriceLevel,
    normalize_kalshi_bid_books,
)


@dataclass(frozen=True, slots=True)
class LivePaperInputs:
    market: RawKalshiMarketReplay
    orderbook: NormalizedOrderBook
    candles: tuple[Candle, ...]
    raw_messages: int


def live_paper_inputs_from_file(path: str | Path) -> LivePaperInputs:
    records = tuple(load_live_message_records(path))
    return live_paper_inputs_from_records(records)


def live_paper_inputs_from_records(
    records: tuple[LiveMessageRecord, ...],
) -> LivePaperInputs:
    market: RawKalshiMarketReplay | None = None
    orderbook: NormalizedOrderBook | None = None
    ticks: list[CFBenchmarkTick] = []

    for record in records:
        if record.source == "kalshi":
            message = _mapping(record.message)
            message_type = str(message.get("type", ""))
            payload = _mapping(message.get("msg"))
            if message_type == "market_lifecycle_v2" or _has_market_fields(payload):
                market = _market_from_payload(payload, record.received_timestamp_ms)
            if message_type in {"orderbook_snapshot", "orderbook_delta", "ticker"}:
                parsed_book = _orderbook_from_payload(payload, record.received_timestamp_ms)
                if parsed_book is not None:
                    orderbook = parsed_book
        if record.source == "coinbase":
            ticks.extend(_coinbase_ticks_from_message(record))

    if market is None:
        raise ValueError("live paper input requires a Kalshi market_lifecycle_v2 message")
    if orderbook is None:
        raise ValueError("live paper input requires a Kalshi orderbook or ticker message")
    candles = build_candles(ticks, interval_ms=60_000)
    if len(candles) < 3:
        raise ValueError("live paper input requires at least 3 Coinbase candle buckets")
    return LivePaperInputs(
        market=market,
        orderbook=orderbook,
        candles=candles,
        raw_messages=len(records),
    )


def _market_from_payload(
    payload: Mapping[str, Any],
    received_timestamp_ms: int,
) -> RawKalshiMarketReplay:
    market_ticker = _required_str(payload, "market_ticker")
    open_timestamp_ms = _int_value(
        payload.get("open_timestamp_ms") or payload.get("open_time_ms") or 0
    )
    close_timestamp_ms = _int_value(
        payload.get("close_timestamp_ms")
        or payload.get("close_time_ms")
        or payload.get("expiration_timestamp_ms")
    )
    return RawKalshiMarketReplay(
        market_ticker=market_ticker,
        series_ticker=str(payload.get("series_ticker", _series_from_market(market_ticker))),
        underlying=str(payload.get("underlying", _underlying_from_market(market_ticker))),
        strike=str(payload.get("strike", payload.get("floor_strike", ""))),
        lifecycle_status=str(payload.get("lifecycle_status", payload.get("status", "open"))),
        open_timestamp_ms=open_timestamp_ms,
        close_timestamp_ms=close_timestamp_ms,
        received_timestamp_ms=received_timestamp_ms,
    )


def _orderbook_from_payload(
    payload: Mapping[str, Any],
    received_timestamp_ms: int,
) -> NormalizedOrderBook | None:
    market_ticker = _optional_str(payload.get("market_ticker") or payload.get("ticker"))
    if market_ticker is None:
        return None
    source_timestamp_ms = _source_timestamp_ms(payload, received_timestamp_ms)

    yes_bids = _price_levels(
        payload.get("yes_bids")
        or payload.get("yes")
        or payload.get("yes_bid")
        or payload.get("yes_bid_dollars"),
    )
    no_bids = _price_levels(
        payload.get("no_bids")
        or payload.get("no")
        or payload.get("no_bid")
        or payload.get("no_bid_dollars"),
    )
    if yes_bids or no_bids:
        return normalize_kalshi_bid_books(
            market_ticker=market_ticker,
            yes_bids=yes_bids,
            no_bids=no_bids,
            source_timestamp_ms=source_timestamp_ms,
            received_timestamp_ms=max(received_timestamp_ms, source_timestamp_ms),
        )

    yes_ask = _quote_from_ask(payload.get("yes_ask") or payload.get("yes_ask_dollars"))
    no_ask = _quote_from_ask(payload.get("no_ask") or payload.get("no_ask_dollars"))
    if yes_ask is None and no_ask is None:
        return None
    age_ms = max(0, received_timestamp_ms - source_timestamp_ms)
    return NormalizedOrderBook(
        market_ticker=market_ticker,
        yes_ask=_book_quote(yes_ask, age_ms),
        no_ask=_book_quote(no_ask, age_ms),
        source_timestamp_ms=source_timestamp_ms,
        received_timestamp_ms=max(received_timestamp_ms, source_timestamp_ms),
    )


def _coinbase_ticks_from_message(record: LiveMessageRecord) -> tuple[CFBenchmarkTick, ...]:
    message = _mapping(record.message)
    source_timestamp_ms = _source_timestamp_ms(message, record.received_timestamp_ms)
    ticks: list[CFBenchmarkTick] = []
    events = message.get("events")
    if isinstance(events, list):
        for event in events:
            ticks.extend(
                _coinbase_ticks_from_event(
                    _mapping(event),
                    fallback_source_timestamp_ms=source_timestamp_ms,
                    received_timestamp_ms=record.received_timestamp_ms,
                )
            )
    else:
        ticks.extend(
            _coinbase_ticks_from_event(
                message,
                fallback_source_timestamp_ms=source_timestamp_ms,
                received_timestamp_ms=record.received_timestamp_ms,
            )
        )
    return tuple(ticks)


def _coinbase_ticks_from_event(
    event: Mapping[str, Any],
    *,
    fallback_source_timestamp_ms: int,
    received_timestamp_ms: int,
) -> tuple[CFBenchmarkTick, ...]:
    ticks: list[CFBenchmarkTick] = []
    tickers = event.get("tickers")
    if isinstance(tickers, list):
        for ticker in tickers:
            parsed = _coinbase_tick_from_payload(
                _mapping(ticker),
                fallback_source_timestamp_ms,
                received_timestamp_ms,
            )
            if parsed is not None:
                ticks.append(parsed)
    parsed = _coinbase_tick_from_payload(
        event,
        fallback_source_timestamp_ms,
        received_timestamp_ms,
    )
    if parsed is not None:
        ticks.append(parsed)
    return tuple(ticks)


def _coinbase_tick_from_payload(
    payload: Mapping[str, Any],
    fallback_source_timestamp_ms: int,
    received_timestamp_ms: int,
) -> CFBenchmarkTick | None:
    product_id = _optional_str(payload.get("product_id") or payload.get("product"))
    if product_id not in {"BTC-USD", "ETH-USD"}:
        return None
    price_value = payload.get("price") or payload.get("best_bid") or payload.get("price_level")
    if price_value is None:
        return None
    source_timestamp_ms = _source_timestamp_ms(payload, fallback_source_timestamp_ms)
    return CFBenchmarkTick(
        index_ticker=product_id,
        price=Decimal(str(price_value)),
        source_timestamp_ms=source_timestamp_ms,
        received_timestamp_ms=max(received_timestamp_ms, source_timestamp_ms),
    )


def _price_levels(raw_levels: object) -> tuple[PriceLevel, ...]:
    if raw_levels is None:
        return ()
    if isinstance(raw_levels, str | int | float | Decimal):
        return (PriceLevel(price=_probability(raw_levels), quantity=10),)
    if not isinstance(raw_levels, list | tuple):
        raise ValueError("orderbook levels must be a list")
    levels: list[PriceLevel] = []
    for raw_level in raw_levels:
        if isinstance(raw_level, Mapping):
            price = raw_level.get("price") or raw_level.get("price_level")
            quantity = raw_level.get("quantity") or raw_level.get("count") or raw_level.get("size")
        elif isinstance(raw_level, list | tuple) and len(raw_level) >= 2:
            price = raw_level[0]
            quantity = raw_level[1]
        else:
            raise ValueError("orderbook level must be an object or [price, quantity]")
        levels.append(PriceLevel(price=_probability(price), quantity=int(quantity)))
    return tuple(levels)


def _quote_from_ask(raw_ask: object) -> Decimal | None:
    if raw_ask is None:
        return None
    return _probability(raw_ask)


def _book_quote(price: Decimal | None, age_ms: int) -> BookQuote | None:
    if price is None:
        return None
    return BookQuote(price=price, depth=10, age_ms=age_ms)


def _probability(value: object) -> Decimal:
    probability = Decimal(str(value))
    if probability > Decimal("1"):
        probability = probability / Decimal("100")
    if probability <= Decimal("0") or probability >= Decimal("1"):
        raise ValueError("probability price must be between 0 and 1")
    return probability


def _source_timestamp_ms(payload: Mapping[str, Any], fallback: int) -> int:
    for key in ("source_timestamp_ms", "timestamp_ms", "event_time_ms"):
        value = payload.get(key)
        if value is not None:
            return _int_value(value)
    return fallback


def _has_market_fields(payload: Mapping[str, Any]) -> bool:
    return "market_ticker" in payload and (
        "close_timestamp_ms" in payload or "close_time_ms" in payload
    )


def _series_from_market(market_ticker: str) -> str:
    return market_ticker.split("-", 1)[0]


def _underlying_from_market(market_ticker: str) -> str:
    upper = market_ticker.upper()
    if "ETH" in upper:
        return "ETH"
    return "BTC"


def _required_str(payload: Mapping[str, Any], field_name: str) -> str:
    value = _optional_str(payload.get(field_name))
    if value is None or not value:
        raise ValueError(f"{field_name} is required")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_value(value: object) -> int:
    if value is None:
        raise ValueError("integer value is required")
    return int(value)


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}
