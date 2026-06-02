from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
import json
import uuid


SignalStrength = Literal["moderate", "strong", "very_strong"]
ConfidenceLevel = Literal["tradable", "research"]
Side = Literal["YES", "NO"]
EventType = Literal["ENTER", "EXIT", "HALT", "REJECT", "INFO"]


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class MarketMetadata:
    market_ticker: str
    category: str
    volume: float
    close_time: datetime


@dataclass(slots=True)
class TradeEvent:
    market_ticker: str
    side: Side
    price: float
    size: int
    timestamp: datetime
    market_volume: float
    category: str
    close_time: datetime

    @property
    def size_ratio(self) -> float:
        if self.market_volume <= 0:
            return 0.0
        return self.size / self.market_volume


@dataclass(slots=True)
class OrderbookState:
    market_ticker: str
    yes_bids: dict[float, int] = field(default_factory=dict)
    yes_asks: dict[float, int] = field(default_factory=dict)
    updated_at: datetime | None = None

    def best_bid(self) -> float | None:
        return max(self.yes_bids) if self.yes_bids else None

    def best_ask(self) -> float | None:
        return min(self.yes_asks) if self.yes_asks else None

    def mid_price(self) -> float | None:
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2

    def expected_exit_price(self, side: Side, size: int) -> float | None:
        if size <= 0:
            return None
        if side == "YES":
            levels = sorted(self.yes_bids.items(), reverse=True)
        else:
            levels = sorted(((1 - price), qty) for price, qty in self.yes_asks.items())
        remaining = size
        total = 0.0
        for price, qty in levels:
            take = min(remaining, qty)
            total += price * take
            remaining -= take
            if remaining == 0:
                return total / size
        return None


@dataclass(slots=True)
class SignalCandidate:
    market_ticker: str
    side: Side
    signal_strength: SignalStrength
    confidence_level: ConfidenceLevel
    price: float
    size_ratio: float
    cluster_count: int
    timestamp: datetime
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VirtualPosition:
    position_id: str
    market_ticker: str
    side: Side
    entry_price: float
    size: int
    signal_strength: SignalStrength
    confidence_level: ConfidenceLevel
    entry_time: datetime
    stop_price: float
    take_profit_price: float
    reason: str
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 1.0
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None

    @classmethod
    def create(
        cls,
        market_ticker: str,
        side: Side,
        entry_price: float,
        size: int,
        signal_strength: SignalStrength,
        confidence_level: ConfidenceLevel,
        entry_time: datetime,
        stop_price: float,
        take_profit_price: float,
        reason: str,
    ) -> "VirtualPosition":
        return cls(
            position_id=str(uuid.uuid4()),
            market_ticker=market_ticker,
            side=side,
            entry_price=entry_price,
            size=size,
            signal_strength=signal_strength,
            confidence_level=confidence_level,
            entry_time=entry_time,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            reason=reason,
            highest_price=entry_price,
            lowest_price=entry_price,
        )

    def mark_price(self, price: float) -> None:
        self.highest_price = max(self.highest_price, price)
        self.lowest_price = min(self.lowest_price, price)
        self.max_favorable_excursion = max(self.max_favorable_excursion, price - self.entry_price)
        self.max_adverse_excursion = min(self.max_adverse_excursion, price - self.entry_price)

    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.size


@dataclass(slots=True)
class ActionEvent:
    event_type: EventType
    timestamp: datetime
    market_ticker: str | None
    side: Side | None
    price: float | None
    size: int | None
    signal_strength: SignalStrength | None
    confidence_level: ConfidenceLevel | None
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return json.dumps(payload, sort_keys=True)


@dataclass(slots=True)
class SessionStats:
    starting_cash: float
    realized_pnl: float = 0.0
    cash: float = 0.0
    peak_equity: float = 0.0
    daily_losses: int = 0
    consecutive_losses: int = 0

    def __post_init__(self) -> None:
        self.cash = self.starting_cash
        self.peak_equity = self.starting_cash
