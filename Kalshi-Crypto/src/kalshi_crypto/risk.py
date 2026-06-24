from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from kalshi_crypto.models import BookQuote, Position
from kalshi_crypto.money import kalshi_general_taker_fee

FeeModel = Callable[[int, Decimal], Decimal]


@dataclass(frozen=True, slots=True)
class PartialHedgeConfig:
    opposing_ask_trigger: Decimal
    max_loss: Decimal
    max_contracts_pct_of_original_size: Decimal
    slippage_buffer: Decimal
    max_book_age_ms: int

    def __post_init__(self) -> None:
        if self.opposing_ask_trigger <= Decimal("0") or self.opposing_ask_trigger >= Decimal("1"):
            raise ValueError("opposing_ask_trigger must be between 0 and 1")
        if self.max_loss <= Decimal("0"):
            raise ValueError("max_loss must be positive")
        if self.max_contracts_pct_of_original_size <= Decimal("0"):
            raise ValueError("max_contracts_pct_of_original_size must be positive")
        if self.slippage_buffer < Decimal("0"):
            raise ValueError("slippage_buffer must be non-negative")
        if self.max_book_age_ms <= 0:
            raise ValueError("max_book_age_ms must be positive")


@dataclass(frozen=True, slots=True)
class PartialHedgeDecision:
    authorized: bool
    reason: str
    quantity: int
    required_quantity: int | None
    wrong_side_loss: Decimal | None


def wrong_side_loss(
    position: Position,
    hedge_quantity: int,
    opposing_ask: Decimal,
    slippage_buffer: Decimal,
    fee_model: FeeModel = kalshi_general_taker_fee,
) -> Decimal:
    if hedge_quantity < 0:
        raise ValueError("hedge_quantity must be non-negative")
    if hedge_quantity == 0:
        return position.original_cost

    hedge_fee = fee_model(hedge_quantity, opposing_ask)
    hedge_cost = Decimal(hedge_quantity) * (opposing_ask + slippage_buffer) + hedge_fee
    hedge_payout = Decimal(hedge_quantity)
    return position.original_cost + hedge_cost - hedge_payout


def solve_partial_hedge(
    position: Position,
    opposing_quote: BookQuote,
    config: PartialHedgeConfig,
    fee_model: FeeModel = kalshi_general_taker_fee,
) -> PartialHedgeDecision:
    if opposing_quote.age_ms > config.max_book_age_ms:
        return PartialHedgeDecision(False, "stale_book", 0, None, None)
    if opposing_quote.price < config.opposing_ask_trigger:
        return PartialHedgeDecision(False, "trigger_not_crossed", 0, None, None)

    max_contracts = _max_hedge_contracts(position, config)
    required_quantity: int | None = None
    required_loss: Decimal | None = None

    for quantity in range(1, max_contracts + 1):
        candidate_loss = wrong_side_loss(
            position,
            quantity,
            opposing_quote.price,
            config.slippage_buffer,
            fee_model,
        )
        if candidate_loss <= config.max_loss:
            required_quantity = quantity
            required_loss = candidate_loss
            break

    if required_quantity is None:
        return PartialHedgeDecision(
            False,
            "max_loss_target_unreachable",
            0,
            None,
            None,
        )

    if opposing_quote.depth < required_quantity:
        return PartialHedgeDecision(
            False,
            "insufficient_depth_for_loss_cap",
            0,
            required_quantity,
            required_loss,
        )

    return PartialHedgeDecision(
        True,
        "ok",
        required_quantity,
        required_quantity,
        required_loss,
    )


def _max_hedge_contracts(position: Position, config: PartialHedgeConfig) -> int:
    raw_limit = Decimal(position.quantity) * config.max_contracts_pct_of_original_size
    max_contracts = int(raw_limit.to_integral_value(rounding=ROUND_FLOOR))
    return min(position.quantity, max_contracts)
