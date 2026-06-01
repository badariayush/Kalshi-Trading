from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from arb_bot.models import BookLevel


@dataclass(frozen=True, slots=True)
class DepthFill:
    size: Decimal
    avg_price: Decimal
    cost: Decimal


def fill_at_market(levels: list[BookLevel], desired_size: Decimal) -> DepthFill | None:
    if desired_size <= 0:
        raise ValueError("desired_size must be positive")
    remaining = desired_size
    total_cost = Decimal("0")
    filled = Decimal("0")
    for level in sorted(levels, key=lambda item: item.price):
        take = min(remaining, level.size)
        total_cost += take * level.price
        filled += take
        remaining -= take
        if remaining == 0:
            break
    if filled == 0 or remaining > 0:
        return None
    return DepthFill(size=filled, avg_price=total_cost / filled, cost=total_cost)
