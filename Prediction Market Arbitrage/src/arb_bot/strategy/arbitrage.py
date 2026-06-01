from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from time import time

from arb_bot.models import ArbLeg, ArbitrageOpportunity, MarketPair, OrderBook, Side, Venue


@dataclass(frozen=True, slots=True)
class ArbSettings:
    min_net_edge: Decimal = Decimal("0.02")
    slippage_buffer: Decimal = Decimal("0.005")
    min_trade_size: Decimal = Decimal("1")
    max_book_age_seconds: float = 2.5
    min_pair_confidence: Decimal = Decimal("0.90")
    fee_per_contract: Decimal = Decimal("0")


def find_opportunities(
    pair: MarketPair,
    polymarket_book: OrderBook,
    kalshi_book: OrderBook,
    settings: ArbSettings,
    now: float | None = None,
) -> list[ArbitrageOpportunity]:
    current_time = time() if now is None else now
    if pair.confidence < settings.min_pair_confidence:
        return []
    if polymarket_book.age_seconds(current_time) > settings.max_book_age_seconds:
        return []
    if kalshi_book.age_seconds(current_time) > settings.max_book_age_seconds:
        return []

    return [
        opp
        for opp in (
            _scan_direction(
                pair=pair,
                direction="polymarket_yes__kalshi_no",
                yes_book=polymarket_book,
                yes_venue=Venue.POLYMARKET,
                no_book=kalshi_book,
                no_venue=Venue.KALSHI,
                settings=settings,
                now=current_time,
            ),
            _scan_direction(
                pair=pair,
                direction="kalshi_yes__polymarket_no",
                yes_book=kalshi_book,
                yes_venue=Venue.KALSHI,
                no_book=polymarket_book,
                no_venue=Venue.POLYMARKET,
                settings=settings,
                now=current_time,
            ),
        )
        if opp is not None
    ]


def _scan_direction(
    pair: MarketPair,
    direction: str,
    yes_book: OrderBook,
    yes_venue: Venue,
    no_book: OrderBook,
    no_venue: Venue,
    settings: ArbSettings,
    now: float,
) -> ArbitrageOpportunity | None:
    yes_levels = sorted(yes_book.asks_for(Side.YES), key=lambda item: item.price)
    no_levels = sorted(no_book.asks_for(Side.NO), key=lambda item: item.price)
    yi = ni = 0
    remaining_yes = yes_levels[0].size if yes_levels else Decimal("0")
    remaining_no = no_levels[0].size if no_levels else Decimal("0")
    total_size = Decimal("0")
    yes_cost = Decimal("0")
    no_cost = Decimal("0")

    while yi < len(yes_levels) and ni < len(no_levels):
        yes_price = yes_levels[yi].price
        no_price = no_levels[ni].price
        per_contract_fees = settings.fee_per_contract * Decimal("2")
        net_cost_at_level = yes_price + no_price + settings.slippage_buffer + per_contract_fees
        net_edge_at_level = Decimal("1") - net_cost_at_level
        if net_edge_at_level < settings.min_net_edge:
            break

        take = min(remaining_yes, remaining_no)
        total_size += take
        yes_cost += take * yes_price
        no_cost += take * no_price
        remaining_yes -= take
        remaining_no -= take

        if remaining_yes == 0:
            yi += 1
            if yi < len(yes_levels):
                remaining_yes = yes_levels[yi].size
        if remaining_no == 0:
            ni += 1
            if ni < len(no_levels):
                remaining_no = no_levels[ni].size

    if total_size < settings.min_trade_size:
        return None

    gross_cost = (yes_cost + no_cost) / total_size
    fees = settings.fee_per_contract * Decimal("2")
    net_cost = gross_cost + fees + settings.slippage_buffer
    net_edge = Decimal("1") - net_cost
    if net_edge < settings.min_net_edge:
        return None

    return ArbitrageOpportunity(
        pair_id=pair.pair_id,
        direction=direction,
        yes_leg=ArbLeg(yes_venue, yes_book.market_id, Side.YES, yes_cost / total_size, total_size),
        no_leg=ArbLeg(no_venue, no_book.market_id, Side.NO, no_cost / total_size, total_size),
        size=total_size,
        gross_cost=gross_cost,
        fees=fees,
        slippage_buffer=settings.slippage_buffer,
        net_cost=net_cost,
        net_edge=net_edge,
        detected_at=now,
    )
