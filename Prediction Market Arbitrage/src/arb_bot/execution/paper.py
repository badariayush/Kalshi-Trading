from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from time import time

from arb_bot.models import ArbitrageOpportunity
from arb_bot.risk.limits import PortfolioState, apply_open_position


@dataclass(frozen=True, slots=True)
class PaperFill:
    opportunity: ArbitrageOpportunity
    filled_at: float
    status: str = "both_legs_paper_filled"

    @property
    def expected_profit(self) -> Decimal:
        return self.opportunity.expected_profit


class PaperExecutor:
    def __init__(self, portfolio: PortfolioState) -> None:
        self.portfolio = portfolio
        self.fills: list[PaperFill] = []

    def execute(self, opportunity: ArbitrageOpportunity) -> PaperFill:
        fill = PaperFill(opportunity=opportunity, filled_at=time())
        self.fills.append(fill)
        apply_open_position(opportunity, self.portfolio)
        return fill
