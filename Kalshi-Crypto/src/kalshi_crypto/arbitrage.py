from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from kalshi_crypto.models import BookQuote, Position
from kalshi_crypto.money import kalshi_general_taker_fee

FeeModel = Callable[[int, Decimal], Decimal]


@dataclass(frozen=True, slots=True)
class ArbitrageConfig:
    min_arb_margin: Decimal
    slippage_buffer: Decimal
    max_book_age_ms: int

    def __post_init__(self) -> None:
        if self.min_arb_margin <= Decimal("0"):
            raise ValueError("min_arb_margin must be positive")
        if self.slippage_buffer < Decimal("0"):
            raise ValueError("slippage_buffer must be non-negative")
        if self.max_book_age_ms <= 0:
            raise ValueError("max_book_age_ms must be positive")


@dataclass(frozen=True, slots=True)
class ArbitrageDecision:
    authorized: bool
    reason: str
    quantity: int
    locked_in_cost: Decimal | None
    threshold: Decimal


def evaluate_true_arbitrage(
    position: Position,
    opposing_quote: BookQuote,
    config: ArbitrageConfig,
    fee_model: FeeModel = kalshi_general_taker_fee,
) -> ArbitrageDecision:
    threshold = Decimal("1") - config.min_arb_margin

    if opposing_quote.age_ms > config.max_book_age_ms:
        return ArbitrageDecision(False, "stale_book", 0, None, threshold)
    if opposing_quote.depth < position.quantity:
        return ArbitrageDecision(False, "insufficient_depth", 0, None, threshold)

    hedge_fee = fee_model(position.quantity, opposing_quote.price)
    quantity = Decimal(position.quantity)
    locked_in_cost = (
        position.entry_price
        + opposing_quote.price
        + (position.entry_fee / quantity)
        + (hedge_fee / quantity)
        + config.slippage_buffer
    )

    if locked_in_cost <= threshold:
        return ArbitrageDecision(True, "ok", position.quantity, locked_in_cost, threshold)

    return ArbitrageDecision(
        False,
        "margin_not_met",
        0,
        locked_in_cost,
        threshold,
    )
