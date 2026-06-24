from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kalshi_crypto.candles import CFBenchmarkTick, build_candles
from kalshi_crypto.config import AppConfig
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.feed_health import FeedHealthMonitor
from kalshi_crypto.market_lifecycle import RawKalshiMarketReplay, lifecycle_events_from_market
from kalshi_crypto.market_state import MarketWindowRegistry
from kalshi_crypto.models import Side
from kalshi_crypto.orderbook import normalize_kalshi_bid_books
from kalshi_crypto.replay import RawKalshiOrderBookReplay, ReplayItem, load_replay_items
from kalshi_crypto.storage import SQLiteAuditStore


@dataclass(frozen=True, slots=True)
class DataOnlySummary:
    replay_events: int
    stale_events: int
    normalized_orderbooks: int
    market_events: int
    cf_benchmark_ticks: int
    cf_candles: int
    audit_events: int
    current_window_tickers: tuple[str, ...]
    next_window_tickers: tuple[str, ...]


def run_data_only_replay(
    config: AppConfig,
    replay_file: str | Path | None,
    audit_db: str | Path,
) -> DataOnlySummary:
    if replay_file is None:
        raise SafetyError("data-only requires a local replay file; use live-data for WebSocket audits")

    items = load_replay_items(replay_file)
    store = SQLiteAuditStore(audit_db)
    processed = _append_replay_items(
        items,
        store,
        config.circuit_breakers.data_feed_stale_ms,
    )
    summary_event = AuditEvent.create(
        event_type="DataOnlyRunCompleted",
        worker="market_monitor",
        payload={
            "replay_events": len(items),
            "stale_events": processed.stale_events,
            "normalized_orderbooks": processed.normalized_orderbooks,
            "market_events": processed.market_events,
            "cf_benchmark_ticks": processed.cf_benchmark_ticks,
            "cf_candles": processed.cf_candles,
            "current_window_tickers": processed.current_window_tickers,
            "next_window_tickers": processed.next_window_tickers,
            "network": "not_attempted",
            "execution": "not_attempted",
        },
        causality_id="data-only-replay",
        timestamp_ms=processed.last_timestamp_ms,
    )
    store.append(summary_event)
    return DataOnlySummary(
        replay_events=len(items),
        stale_events=processed.stale_events,
        normalized_orderbooks=processed.normalized_orderbooks,
        market_events=processed.market_events,
        cf_benchmark_ticks=processed.cf_benchmark_ticks,
        cf_candles=processed.cf_candles,
        audit_events=processed.audit_events + 1,
        current_window_tickers=processed.current_window_tickers,
        next_window_tickers=processed.next_window_tickers,
    )


@dataclass(frozen=True, slots=True)
class _ProcessedReplay:
    stale_events: int
    normalized_orderbooks: int
    market_events: int
    cf_benchmark_ticks: int
    cf_candles: int
    audit_events: int
    last_timestamp_ms: int
    current_window_tickers: tuple[str, ...]
    next_window_tickers: tuple[str, ...]


def _append_replay_items(
    items: Iterable[ReplayItem],
    store: SQLiteAuditStore,
    max_stale_ms: int,
) -> _ProcessedReplay:
    monitor = FeedHealthMonitor(max_stale_ms=max_stale_ms)
    stale_events = 0
    normalized_orderbooks = 0
    market_events = 0
    cf_benchmark_ticks = 0
    audit_events = 0
    last_timestamp_ms = 0
    registry = MarketWindowRegistry.empty()
    cf_ticks: list[CFBenchmarkTick] = []
    for item in items:
        events = _events_from_item(item, monitor)
        for event in events:
            store.append(event)
            registry = registry.apply(event)
            audit_events += 1
            last_timestamp_ms = max(last_timestamp_ms, event.timestamp_ms)
        if isinstance(item, AuditEvent) and _is_stale_market_data(item, monitor):
            stale_events += 1
        elif any(
            event.event_type == "FeedHealthEvaluated"
            and event.payload.get("healthy") is False
            for event in events
        ):
            stale_events += 1
        if isinstance(item, RawKalshiOrderBookReplay):
            normalized_orderbooks += 1
        if isinstance(item, RawKalshiMarketReplay):
            market_events += len(events)
        if isinstance(item, CFBenchmarkTick):
            cf_benchmark_ticks += 1
            cf_ticks.append(item)
    cf_candles = 0
    for event in _cf_candle_events(cf_ticks):
        store.append(event)
        audit_events += 1
        cf_candles += 1
        last_timestamp_ms = max(last_timestamp_ms, event.timestamp_ms)
    current_window_tickers = _current_window_tickers(registry, last_timestamp_ms)
    next_window_tickers = _next_window_tickers(registry, last_timestamp_ms)
    return _ProcessedReplay(
        stale_events=stale_events,
        normalized_orderbooks=normalized_orderbooks,
        market_events=market_events,
        cf_benchmark_ticks=cf_benchmark_ticks,
        cf_candles=cf_candles,
        audit_events=audit_events,
        last_timestamp_ms=last_timestamp_ms,
        current_window_tickers=current_window_tickers,
        next_window_tickers=next_window_tickers,
    )


def _events_from_item(
    item: ReplayItem,
    monitor: FeedHealthMonitor,
) -> tuple[AuditEvent, ...]:
    if isinstance(item, AuditEvent):
        return (item,)
    if isinstance(item, RawKalshiMarketReplay):
        return lifecycle_events_from_market(item)
    if isinstance(item, CFBenchmarkTick):
        return _events_from_cf_benchmark_tick(item, monitor)
    return _events_from_raw_orderbook(item, monitor)


def _events_from_raw_orderbook(
    item: RawKalshiOrderBookReplay,
    monitor: FeedHealthMonitor,
) -> tuple[AuditEvent, AuditEvent]:
    book = normalize_kalshi_bid_books(
        market_ticker=item.market_ticker,
        yes_bids=item.yes_bids,
        no_bids=item.no_bids,
        source_timestamp_ms=item.source_timestamp_ms,
        received_timestamp_ms=item.received_timestamp_ms,
    )
    health = monitor.evaluate(
        source="kalshi_orderbook",
        source_timestamp_ms=item.source_timestamp_ms,
        received_timestamp_ms=item.received_timestamp_ms,
    )
    normalized_event = AuditEvent.create(
        event_type="OrderBookSnapshotNormalized",
        worker="market_monitor",
        payload={
            "market_ticker": book.market_ticker,
            "source": "kalshi_orderbook",
            "source_timestamp_ms": book.source_timestamp_ms,
            "received_timestamp_ms": book.received_timestamp_ms,
            **_ask_payload("yes", book.ask_for(Side.YES)),
            **_ask_payload("no", book.ask_for(Side.NO)),
        },
        causality_id="data-only-replay",
        timestamp_ms=item.received_timestamp_ms,
    )
    health_event = AuditEvent.create(
        event_type="FeedHealthEvaluated",
        worker="market_monitor",
        payload={
            "source": health.source,
            "healthy": health.healthy,
            "reason": health.reason,
            "age_ms": health.age_ms,
        },
        causality_id=normalized_event.event_id,
        timestamp_ms=item.received_timestamp_ms,
    )
    return normalized_event, health_event


def _events_from_cf_benchmark_tick(
    item: CFBenchmarkTick,
    monitor: FeedHealthMonitor,
) -> tuple[AuditEvent, AuditEvent]:
    health = monitor.evaluate(
        source=f"cf_benchmark:{item.index_ticker}",
        source_timestamp_ms=item.source_timestamp_ms,
        received_timestamp_ms=item.received_timestamp_ms,
    )
    tick_event = AuditEvent.create(
        event_type="CFBenchmarkTickIngested",
        worker="market_monitor",
        payload={
            "index_ticker": item.index_ticker,
            "price": str(item.price),
            "source_timestamp_ms": item.source_timestamp_ms,
            "received_timestamp_ms": item.received_timestamp_ms,
        },
        causality_id="data-only-replay",
        timestamp_ms=item.received_timestamp_ms,
    )
    health_event = AuditEvent.create(
        event_type="FeedHealthEvaluated",
        worker="market_monitor",
        payload={
            "source": health.source,
            "healthy": health.healthy,
            "reason": health.reason,
            "age_ms": health.age_ms,
        },
        causality_id=tick_event.event_id,
        timestamp_ms=item.received_timestamp_ms,
    )
    return tick_event, health_event


def _cf_candle_events(ticks: Iterable[CFBenchmarkTick]) -> tuple[AuditEvent, ...]:
    grouped: dict[str, list[CFBenchmarkTick]] = {}
    for tick in ticks:
        grouped.setdefault(tick.index_ticker, []).append(tick)

    events: list[AuditEvent] = []
    for index_ticker in sorted(grouped):
        candles = build_candles(grouped[index_ticker], interval_ms=60_000)
        for candle in candles:
            events.append(
                AuditEvent.create(
                    event_type="CFBenchmarkCandleClosed",
                    worker="signal",
                    payload={
                        "index_ticker": candle.index_ticker,
                        "start_timestamp_ms": candle.start_timestamp_ms,
                        "end_timestamp_ms": candle.end_timestamp_ms,
                        "open_price": str(candle.open_price),
                        "high_price": str(candle.high_price),
                        "low_price": str(candle.low_price),
                        "close_price": str(candle.close_price),
                        "tick_count": candle.tick_count,
                        "source_timestamp_ms": candle.source_timestamp_ms,
                        "received_timestamp_ms": candle.received_timestamp_ms,
                    },
                    causality_id="data-only-replay",
                    timestamp_ms=candle.received_timestamp_ms,
                )
            )
    return tuple(events)


def _ask_payload(prefix: str, quote: object) -> dict[str, object]:
    if quote is None:
        return {
            f"{prefix}_ask_price": None,
            f"{prefix}_ask_depth": 0,
            f"{prefix}_ask_age_ms": None,
        }
    return {
        f"{prefix}_ask_price": str(quote.price),
        f"{prefix}_ask_depth": quote.depth,
        f"{prefix}_ask_age_ms": quote.age_ms,
    }


def _is_stale_market_data(event: AuditEvent, monitor: FeedHealthMonitor) -> bool:
    payload = event.payload
    source = payload.get("source")
    source_timestamp_ms = payload.get("source_timestamp_ms")
    received_timestamp_ms = payload.get("received_timestamp_ms")
    if not isinstance(source, str):
        return False
    if not isinstance(source_timestamp_ms, int):
        return False
    if not isinstance(received_timestamp_ms, int):
        return False
    return not monitor.evaluate(
        source=source,
        source_timestamp_ms=source_timestamp_ms,
        received_timestamp_ms=received_timestamp_ms,
    ).healthy


def _current_window_tickers(
    registry: MarketWindowRegistry,
    now_ms: int,
) -> tuple[str, ...]:
    return tuple(
        window.market_ticker
        for underlying in registry.underlyings()
        for window in registry.current_windows(underlying, now_ms)
    )


def _next_window_tickers(
    registry: MarketWindowRegistry,
    now_ms: int,
) -> tuple[str, ...]:
    windows = []
    for underlying in registry.underlyings():
        window = registry.next_window(underlying, now_ms)
        if window is not None:
            windows.append(window.market_ticker)
    return tuple(windows)
