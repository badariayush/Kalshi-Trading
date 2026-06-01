from __future__ import annotations

from decimal import Decimal
from time import time
import unittest

from arb_bot.models import BookLevel, MarketPair, OrderBook, PairStatus, Venue
from arb_bot.risk.limits import PortfolioState, RiskLimits, check_risk
from arb_bot.strategy.arbitrage import ArbSettings, find_opportunities


class RiskTests(unittest.TestCase):
    def _opportunity(self):
        now = time()
        pair = MarketPair("p1", "Example", "crypto", "poly", "kalshi", Decimal("0.95"), PairStatus.ACTIVE)
        poly = OrderBook(Venue.POLYMARKET, "poly", [BookLevel(Decimal("0.42"), Decimal("30"))], [], now)
        kalshi = OrderBook(Venue.KALSHI, "kalshi", [], [BookLevel(Decimal("0.55"), Decimal("12"))], now)
        return find_opportunities(pair, poly, kalshi, ArbSettings(), now=now)[0]

    def test_allows_trade_within_caps(self) -> None:
        decision = check_risk(self._opportunity(), PortfolioState(), RiskLimits())
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ok")

    def test_blocks_per_leg_notional_limit(self) -> None:
        decision = check_risk(self._opportunity(), PortfolioState(), RiskLimits(max_notional_per_leg=Decimal("1")))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "per_leg_notional_limit")


if __name__ == "__main__":
    unittest.main()
