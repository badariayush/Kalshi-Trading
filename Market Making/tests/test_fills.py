from __future__ import annotations

from decimal import Decimal
import unittest

from mm_bot.fills import conservative_fill_from_book, conservative_fill_from_trade
from mm_bot.strategy import Quote


class FillTests(unittest.TestCase):
    def test_conservative_book_fill_requires_cross(self) -> None:
        bid = Quote("BTC", "bid", Decimal("0.48"), 1)
        self.assertIsNone(conservative_fill_from_book(bid, best_bid=Decimal("0.47"), best_ask=Decimal("0.49")))
        self.assertIsNotNone(conservative_fill_from_book(bid, best_bid=Decimal("0.47"), best_ask=Decimal("0.48")))
        ask = Quote("BTC", "ask", Decimal("0.52"), 1)
        self.assertIsNone(conservative_fill_from_book(ask, best_bid=Decimal("0.51"), best_ask=Decimal("0.53")))
        self.assertIsNotNone(conservative_fill_from_book(ask, best_bid=Decimal("0.52"), best_ask=Decimal("0.53")))

    def test_trade_fill_uses_taker_side(self) -> None:
        ask = Quote("BTC", "ask", Decimal("0.52"), 1)
        payload = {"type": "trade", "msg": {"market_ticker": "BTC", "taker_book_side": "bid", "yes_price_dollars": "0.53"}}
        self.assertIsNotNone(conservative_fill_from_trade(ask, payload))


if __name__ == "__main__":
    unittest.main()
