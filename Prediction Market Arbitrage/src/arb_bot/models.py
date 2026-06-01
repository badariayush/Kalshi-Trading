from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from time import time


class Venue(StrEnum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class Side(StrEnum):
    YES = "yes"
    NO = "no"


class PairStatus(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        if self.price < 0 or self.price > 1:
            raise ValueError("price must be between 0 and 1")
        if self.size <= 0:
            raise ValueError("size must be positive")


@dataclass(slots=True)
class OrderBook:
    venue: Venue
    market_id: str
    yes_asks: list[BookLevel]
    no_asks: list[BookLevel]
    timestamp: float = field(default_factory=time)

    def asks_for(self, side: Side) -> list[BookLevel]:
        return self.yes_asks if side is Side.YES else self.no_asks

    def age_seconds(self, now: float | None = None) -> float:
        return (time() if now is None else now) - self.timestamp


@dataclass(frozen=True, slots=True)
class MarketPair:
    pair_id: str
    name: str
    category: str
    polymarket_market_id: str
    kalshi_market_id: str
    confidence: Decimal
    status: PairStatus = PairStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class VenueMarket:
    venue: Venue
    market_id: str
    title: str
    category: str
    yes_token_id: str | None = None
    no_token_id: str | None = None
    close_time: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArbLeg:
    venue: Venue
    market_id: str
    side: Side
    avg_price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class ArbitrageOpportunity:
    pair_id: str
    direction: str
    yes_leg: ArbLeg
    no_leg: ArbLeg
    size: Decimal
    gross_cost: Decimal
    fees: Decimal
    slippage_buffer: Decimal
    net_cost: Decimal
    net_edge: Decimal
    detected_at: float = field(default_factory=time)

    @property
    def expected_profit(self) -> Decimal:
        return self.size * self.net_edge
