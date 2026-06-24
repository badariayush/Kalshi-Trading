from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from kalshi_crypto.models import BookQuote, Side


@dataclass(frozen=True, slots=True)
class PriceLevel:
    price: Decimal
    quantity: int

    def __post_init__(self) -> None:
        if self.price <= Decimal("0") or self.price >= Decimal("1"):
            raise ValueError("price must be between 0 and 1")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")


@dataclass(frozen=True, slots=True)
class NormalizedOrderBook:
    market_ticker: str
    yes_ask: BookQuote | None
    no_ask: BookQuote | None
    source_timestamp_ms: int
    received_timestamp_ms: int

    def ask_for(self, side: Side) -> BookQuote | None:
        return self.yes_ask if side is Side.YES else self.no_ask


def normalize_kalshi_bid_books(
    market_ticker: str,
    yes_bids: Iterable[PriceLevel],
    no_bids: Iterable[PriceLevel],
    source_timestamp_ms: int,
    received_timestamp_ms: int,
) -> NormalizedOrderBook:
    if not market_ticker:
        raise ValueError("market_ticker is required")
    if source_timestamp_ms < 0:
        raise ValueError("source_timestamp_ms must be non-negative")
    if received_timestamp_ms < source_timestamp_ms:
        raise ValueError("received_timestamp_ms must be at or after source_timestamp_ms")

    age_ms = received_timestamp_ms - source_timestamp_ms
    best_yes_bid = _best_bid(yes_bids)
    best_no_bid = _best_bid(no_bids)

    return NormalizedOrderBook(
        market_ticker=market_ticker,
        yes_ask=_ask_from_opposite_bid(best_no_bid, age_ms),
        no_ask=_ask_from_opposite_bid(best_yes_bid, age_ms),
        source_timestamp_ms=source_timestamp_ms,
        received_timestamp_ms=received_timestamp_ms,
    )


def _best_bid(levels: Iterable[PriceLevel]) -> PriceLevel | None:
    best: PriceLevel | None = None
    for level in levels:
        if best is None or level.price > best.price:
            best = level
    return best


def _ask_from_opposite_bid(level: PriceLevel | None, age_ms: int) -> BookQuote | None:
    if level is None:
        return None
    return BookQuote(
        price=Decimal("1") - level.price,
        depth=level.quantity,
        age_ms=age_ms,
    )
