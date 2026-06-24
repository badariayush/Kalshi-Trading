from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

CENT = Decimal("0.01")
KALSHI_TAKER_FEE_RATE = Decimal("0.07")
KALSHI_MAKER_FEE_RATE = Decimal("0.0175")


def round_up_to_cent(amount: Decimal) -> Decimal:
    if amount < Decimal("0"):
        raise ValueError("amount must be non-negative")
    return amount.quantize(CENT, rounding=ROUND_CEILING)


def kalshi_general_taker_fee(quantity: int, price: Decimal) -> Decimal:
    return _kalshi_fee(quantity, price, KALSHI_TAKER_FEE_RATE)


def kalshi_general_maker_fee(quantity: int, price: Decimal) -> Decimal:
    return _kalshi_fee(quantity, price, KALSHI_MAKER_FEE_RATE)


def _kalshi_fee(quantity: int, price: Decimal, rate: Decimal) -> Decimal:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if price <= Decimal("0") or price >= Decimal("1"):
        raise ValueError("price must be between 0 and 1")

    count = Decimal(quantity)
    return round_up_to_cent(rate * count * price * (Decimal("1") - price))
