from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CFBenchmarkTick:
    index_ticker: str
    price: Decimal
    source_timestamp_ms: int
    received_timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.index_ticker:
            raise ValueError("index_ticker is required")
        if self.price <= Decimal("0"):
            raise ValueError("price must be positive")
        if self.source_timestamp_ms < 0:
            raise ValueError("source_timestamp_ms must be non-negative")
        if self.received_timestamp_ms < self.source_timestamp_ms:
            raise ValueError("received_timestamp_ms must be at or after source_timestamp_ms")


@dataclass(frozen=True, slots=True)
class Candle:
    index_ticker: str
    start_timestamp_ms: int
    end_timestamp_ms: int
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    tick_count: int
    source_timestamp_ms: int
    received_timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.index_ticker:
            raise ValueError("index_ticker is required")
        if self.start_timestamp_ms < 0:
            raise ValueError("start_timestamp_ms must be non-negative")
        if self.end_timestamp_ms <= self.start_timestamp_ms:
            raise ValueError("end_timestamp_ms must be after start_timestamp_ms")
        if self.tick_count <= 0:
            raise ValueError("tick_count must be positive")
        for field_name in ("open_price", "high_price", "low_price", "close_price"):
            value = getattr(self, field_name)
            if value <= Decimal("0"):
                raise ValueError(f"{field_name} must be positive")
        if self.low_price > self.high_price:
            raise ValueError("low_price must be at or below high_price")
        if not self.low_price <= self.open_price <= self.high_price:
            raise ValueError("open_price must be within candle range")
        if not self.low_price <= self.close_price <= self.high_price:
            raise ValueError("close_price must be within candle range")
        if self.source_timestamp_ms < self.start_timestamp_ms:
            raise ValueError("source_timestamp_ms must be inside or after candle start")
        if self.received_timestamp_ms < self.source_timestamp_ms:
            raise ValueError("received_timestamp_ms must be at or after source_timestamp_ms")


def build_candles(
    ticks: Iterable[CFBenchmarkTick],
    interval_ms: int,
) -> tuple[Candle, ...]:
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")

    sorted_ticks = tuple(sorted(ticks, key=lambda tick: tick.source_timestamp_ms))
    if not sorted_ticks:
        return ()
    index_ticker = sorted_ticks[0].index_ticker
    if any(tick.index_ticker != index_ticker for tick in sorted_ticks):
        raise ValueError("build_candles requires a single index_ticker")

    candles: list[Candle] = []
    bucket_ticks: list[CFBenchmarkTick] = []
    current_start = _bucket_start(sorted_ticks[0].source_timestamp_ms, interval_ms)
    for tick in sorted_ticks:
        tick_start = _bucket_start(tick.source_timestamp_ms, interval_ms)
        if tick_start != current_start:
            candles.append(_candle_from_bucket(index_ticker, current_start, interval_ms, bucket_ticks))
            bucket_ticks = []
            current_start = tick_start
        bucket_ticks.append(tick)
    if bucket_ticks:
        candles.append(_candle_from_bucket(index_ticker, current_start, interval_ms, bucket_ticks))
    return tuple(candles)


def _bucket_start(timestamp_ms: int, interval_ms: int) -> int:
    return timestamp_ms - (timestamp_ms % interval_ms)


def _candle_from_bucket(
    index_ticker: str,
    start_timestamp_ms: int,
    interval_ms: int,
    ticks: list[CFBenchmarkTick],
) -> Candle:
    if not ticks:
        raise ValueError("cannot build candle from empty tick bucket")
    prices = tuple(tick.price for tick in ticks)
    return Candle(
        index_ticker=index_ticker,
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=start_timestamp_ms + interval_ms,
        open_price=prices[0],
        high_price=max(prices),
        low_price=min(prices),
        close_price=prices[-1],
        tick_count=len(ticks),
        source_timestamp_ms=max(tick.source_timestamp_ms for tick in ticks),
        received_timestamp_ms=max(tick.received_timestamp_ms for tick in ticks),
    )
