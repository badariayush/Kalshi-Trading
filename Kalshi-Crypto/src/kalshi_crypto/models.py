from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    YES = "yes"
    NO = "no"

    @property
    def opposite(self) -> "Side":
        return Side.NO if self is Side.YES else Side.YES


@dataclass(frozen=True, slots=True)
class Position:
    market_ticker: str
    side: Side
    quantity: int
    entry_price: Decimal
    entry_fee: Decimal

    def __post_init__(self) -> None:
        if not self.market_ticker:
            raise ValueError("market_ticker is required")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.entry_price <= Decimal("0") or self.entry_price >= Decimal("1"):
            raise ValueError("entry_price must be between 0 and 1")
        if self.entry_fee < Decimal("0"):
            raise ValueError("entry_fee must be non-negative")

    @property
    def original_cost(self) -> Decimal:
        return Decimal(self.quantity) * self.entry_price + self.entry_fee


@dataclass(frozen=True, slots=True)
class BookQuote:
    price: Decimal
    depth: int
    age_ms: int

    def __post_init__(self) -> None:
        if self.price <= Decimal("0") or self.price >= Decimal("1"):
            raise ValueError("price must be between 0 and 1")
        if self.depth < 0:
            raise ValueError("depth must be non-negative")
        if self.age_ms < 0:
            raise ValueError("age_ms must be non-negative")
