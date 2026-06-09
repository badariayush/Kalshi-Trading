from __future__ import annotations

from decimal import Decimal
import unittest

from mm_bot.config import RiskConfig, StrategyConfig
from mm_bot.orderbook import YesOrderBook
from mm_bot.strategy import generate_quotes


class StrategyTests(unittest.TestCase):
    def test_midpoint_quotes_and_inventory_skew(self) -> None:
        book = YesOrderBook("BTC", bids={Decimal("0.48"): Decimal("1")}, asks={Decimal("0.52"): Decimal("1")})
        strategy = StrategyConfig(min_spread=Decimal("0.04"), inventory_skew=Decimal("0.06"))
        risk = RiskConfig(max_abs_inventory=3)
        flat = generate_quotes(book, 0, strategy, risk)
        long = generate_quotes(book, 3, strategy, risk)
        self.assertEqual([q.price for q in flat.quotes], [Decimal("0.48"), Decimal("0.52")])
        self.assertEqual([q.price for q in long.quotes], [Decimal("0.46")])
        self.assertIn("max_long_inventory_blocks_bid", long.blocked_reasons)


if __name__ == "__main__":
    unittest.main()
