from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from mm_bot.config import RiskConfig, StrategyConfig
from mm_bot.orderbook import YesOrderBook

QuoteSide = Literal["bid", "ask"]


@dataclass(frozen=True, slots=True)
class Quote:
    market_ticker: str
    side: QuoteSide
    price: Decimal
    size: int


@dataclass(frozen=True, slots=True)
class QuoteDecision:
    quotes: list[Quote]
    blocked_reasons: list[str]
    fair_value: Decimal | None


def generate_quotes(book: YesOrderBook, inventory: int, strategy: StrategyConfig, risk: RiskConfig) -> QuoteDecision:
    fair = book.midpoint()
    if fair is None:
        return QuoteDecision([], ["missing_or_crossed_book"], None)
    half_spread = strategy.min_spread / Decimal("2")
    inventory_ratio = Decimal(inventory) / Decimal(max(1, risk.max_abs_inventory))
    center = fair - (inventory_ratio * strategy.inventory_skew)
    bid = _tick(center - half_spread)
    ask = _tick(center + half_spread)
    blocked: list[str] = []
    quotes: list[Quote] = []

    if ask - bid < strategy.min_spread:
        blocked.append("spread_too_narrow")
        return QuoteDecision([], blocked, fair)
    if bid < strategy.min_price or bid > strategy.max_price:
        blocked.append("bid_out_of_bounds")
    elif inventory >= risk.max_abs_inventory:
        blocked.append("max_long_inventory_blocks_bid")
    else:
        quotes.append(Quote(book.market_ticker, "bid", bid, strategy.quote_size))

    if ask < strategy.min_price or ask > strategy.max_price:
        blocked.append("ask_out_of_bounds")
    elif inventory <= -risk.max_abs_inventory:
        blocked.append("max_short_inventory_blocks_ask")
    else:
        quotes.append(Quote(book.market_ticker, "ask", ask, strategy.quote_size))

    return QuoteDecision(quotes, blocked, fair)


def should_requote(old: Quote | None, new: Quote, threshold: Decimal) -> bool:
    if old is None:
        return True
    return old.size != new.size or abs(old.price - new.price) >= threshold


def _tick(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
