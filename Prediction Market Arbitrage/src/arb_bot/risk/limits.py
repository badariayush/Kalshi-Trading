from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from arb_bot.models import ArbitrageOpportunity, Venue


@dataclass(slots=True)
class PortfolioState:
    total_exposure: Decimal = Decimal("0")
    pair_exposure: dict[str, Decimal] = field(default_factory=dict)
    venue_exposure: dict[Venue, Decimal] = field(default_factory=lambda: {Venue.POLYMARKET: Decimal("0"), Venue.KALSHI: Decimal("0")})
    daily_realized_pnl: Decimal = Decimal("0")
    consecutive_losses: int = 0


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_notional_per_leg: Decimal = Decimal("25")
    max_total_exposure: Decimal = Decimal("100")
    max_pair_exposure: Decimal = Decimal("25")
    max_venue_exposure: Decimal = Decimal("75")
    daily_loss_limit: Decimal = Decimal("25")
    max_consecutive_losses: int = 3
    kill_switch_file: str | None = None


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    reason: str


def check_risk(opp: ArbitrageOpportunity, state: PortfolioState, limits: RiskLimits) -> RiskDecision:
    if limits.kill_switch_file and Path(limits.kill_switch_file).exists():
        return RiskDecision(False, "kill_switch_active")
    if state.daily_realized_pnl <= -limits.daily_loss_limit:
        return RiskDecision(False, "daily_loss_limit_hit")
    if state.consecutive_losses >= limits.max_consecutive_losses:
        return RiskDecision(False, "consecutive_loss_limit_hit")

    yes_notional = opp.yes_leg.avg_price * opp.size
    no_notional = opp.no_leg.avg_price * opp.size
    if yes_notional > limits.max_notional_per_leg or no_notional > limits.max_notional_per_leg:
        return RiskDecision(False, "per_leg_notional_limit")

    new_total = state.total_exposure + yes_notional + no_notional
    if new_total > limits.max_total_exposure:
        return RiskDecision(False, "total_exposure_limit")

    new_pair = state.pair_exposure.get(opp.pair_id, Decimal("0")) + yes_notional + no_notional
    if new_pair > limits.max_pair_exposure:
        return RiskDecision(False, "pair_exposure_limit")

    for leg, notional in ((opp.yes_leg, yes_notional), (opp.no_leg, no_notional)):
        new_venue = state.venue_exposure.get(leg.venue, Decimal("0")) + notional
        if new_venue > limits.max_venue_exposure:
            return RiskDecision(False, f"venue_exposure_limit:{leg.venue}")

    return RiskDecision(True, "ok")


def apply_open_position(opp: ArbitrageOpportunity, state: PortfolioState) -> None:
    yes_notional = opp.yes_leg.avg_price * opp.size
    no_notional = opp.no_leg.avg_price * opp.size
    total = yes_notional + no_notional
    state.total_exposure += total
    state.pair_exposure[opp.pair_id] = state.pair_exposure.get(opp.pair_id, Decimal("0")) + total
    state.venue_exposure[opp.yes_leg.venue] = state.venue_exposure.get(opp.yes_leg.venue, Decimal("0")) + yes_notional
    state.venue_exposure[opp.no_leg.venue] = state.venue_exposure.get(opp.no_leg.venue, Decimal("0")) + no_notional
