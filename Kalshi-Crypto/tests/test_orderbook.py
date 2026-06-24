from __future__ import annotations

from decimal import Decimal
import unittest

from kalshi_crypto.models import Side
from kalshi_crypto.orderbook import PriceLevel, normalize_kalshi_bid_books


class OrderBookNormalizationTests(unittest.TestCase):
    def test_normalizes_kalshi_yes_no_bids_into_executable_asks(self) -> None:
        book = normalize_kalshi_bid_books(
            market_ticker="KXBTCD-TEST",
            yes_bids=[
                PriceLevel(price=Decimal("0.42"), quantity=10),
                PriceLevel(price=Decimal("0.40"), quantity=100),
            ],
            no_bids=[
                PriceLevel(price=Decimal("0.37"), quantity=20),
                PriceLevel(price=Decimal("0.35"), quantity=100),
            ],
            source_timestamp_ms=1_000,
            received_timestamp_ms=1_125,
        )

        self.assertEqual(book.market_ticker, "KXBTCD-TEST")
        self.assertEqual(book.ask_for(Side.YES).price, Decimal("0.63"))
        self.assertEqual(book.ask_for(Side.YES).depth, 20)
        self.assertEqual(book.ask_for(Side.YES).age_ms, 125)
        self.assertEqual(book.ask_for(Side.NO).price, Decimal("0.58"))
        self.assertEqual(book.ask_for(Side.NO).depth, 10)

    def test_returns_missing_ask_when_opposite_bid_book_is_empty(self) -> None:
        book = normalize_kalshi_bid_books(
            market_ticker="KXBTCD-TEST",
            yes_bids=[PriceLevel(price=Decimal("0.42"), quantity=10)],
            no_bids=[],
            source_timestamp_ms=1_000,
            received_timestamp_ms=1_125,
        )

        self.assertIsNone(book.ask_for(Side.YES))
        self.assertEqual(book.ask_for(Side.NO).price, Decimal("0.58"))

    def test_rejects_invalid_provider_prices_quantities_and_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "price must be between 0 and 1"):
            PriceLevel(price=Decimal("1.00"), quantity=1)

        with self.assertRaisesRegex(ValueError, "quantity must be positive"):
            PriceLevel(price=Decimal("0.50"), quantity=0)

        with self.assertRaisesRegex(ValueError, "received_timestamp_ms"):
            normalize_kalshi_bid_books(
                market_ticker="KXBTCD-TEST",
                yes_bids=[],
                no_bids=[],
                source_timestamp_ms=2_000,
                received_timestamp_ms=1_000,
            )


if __name__ == "__main__":
    unittest.main()
