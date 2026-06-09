from __future__ import annotations

from decimal import Decimal
import unittest

from mm_bot.orderbook import YesOrderBook, apply_ws_message


class OrderBookTests(unittest.TestCase):
    def test_snapshot_and_delta_use_yes_scale(self) -> None:
        books: dict[str, YesOrderBook] = {}
        book = apply_ws_message(books, {"type": "orderbook_snapshot", "msg": {"market_ticker": "BTC", "yes": [["0.48", 2]], "no": [["0.53", 3]]}})
        self.assertIsNotNone(book)
        assert book is not None
        self.assertEqual(book.best_bid, Decimal("0.48"))
        self.assertEqual(book.best_ask, Decimal("0.53"))
        self.assertEqual(book.midpoint(), Decimal("0.505"))
        apply_ws_message(books, {"type": "orderbook_delta", "msg": {"market_ticker": "BTC", "book_side": "bid", "price_dollars": "0.49", "delta": "4"}})
        apply_ws_message(books, {"type": "orderbook_delta", "msg": {"market_ticker": "BTC", "book_side": "ask", "price_dollars": "0.52", "delta": "1"}})
        self.assertEqual(book.best_bid, Decimal("0.49"))
        self.assertEqual(book.best_ask, Decimal("0.52"))


if __name__ == "__main__":
    unittest.main()
