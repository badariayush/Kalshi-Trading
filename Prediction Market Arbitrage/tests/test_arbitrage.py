from __future__ import annotations

from decimal import Decimal
from time import time
import unittest

from arb_bot.models import BookLevel, MarketPair, OrderBook, PairStatus, Venue
from arb_bot.strategy.arbitrage import ArbSettings, find_opportunities


class ArbitrageTests(unittest.TestCase):
    def test_finds_profitable_cross_venue_yes_no_arb(self) -> None:
        now = time()
        pair = MarketPair("p1", "Example", "crypto", "poly", "kalshi", Decimal("0.95"), PairStatus.ACTIVE)
        poly = OrderBook(Venue.POLYMARKET, "poly", [BookLevel(Decimal("0.42"), Decimal("30"))], [BookLevel(Decimal("0.58"), Decimal("30"))], now)
        kalshi = OrderBook(Venue.KALSHI, "kalshi", [BookLevel(Decimal("0.49"), Decimal("30"))], [BookLevel(Decimal("0.55"), Decimal("12"))], now)

        opps = find_opportunities(pair, poly, kalshi, ArbSettings(), now=now)

        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0].direction, "polymarket_yes__kalshi_no")
        self.assertEqual(opps[0].size, Decimal("12"))
        self.assertEqual(opps[0].net_edge, Decimal("0.025"))

    def test_walks_depth_and_stops_when_edge_falls_below_threshold(self) -> None:
        now = time()
        pair = MarketPair("p1", "Example", "crypto", "poly", "kalshi", Decimal("0.95"), PairStatus.ACTIVE)
        poly = OrderBook(
            Venue.POLYMARKET,
            "poly",
            [BookLevel(Decimal("0.42"), Decimal("5")), BookLevel(Decimal("0.45"), Decimal("20"))],
            [BookLevel(Decimal("0.58"), Decimal("25"))],
            now,
        )
        kalshi = OrderBook(
            Venue.KALSHI,
            "kalshi",
            [BookLevel(Decimal("0.49"), Decimal("25"))],
            [BookLevel(Decimal("0.53"), Decimal("10")), BookLevel(Decimal("0.54"), Decimal("50"))],
            now,
        )

        opps = find_opportunities(pair, poly, kalshi, ArbSettings(), now=now)

        self.assertEqual(len(opps), 1)
        self.assertEqual(opps[0].size, Decimal("5"))
        self.assertEqual(opps[0].net_edge, Decimal("0.045"))

    def test_rejects_stale_books(self) -> None:
        now = time()
        pair = MarketPair("p1", "Example", "crypto", "poly", "kalshi", Decimal("0.95"), PairStatus.ACTIVE)
        stale = now - 3
        poly = OrderBook(Venue.POLYMARKET, "poly", [BookLevel(Decimal("0.42"), Decimal("30"))], [], stale)
        kalshi = OrderBook(Venue.KALSHI, "kalshi", [], [BookLevel(Decimal("0.55"), Decimal("30"))], now)

        opps = find_opportunities(pair, poly, kalshi, ArbSettings(max_book_age_seconds=2.5), now=now)

        self.assertEqual(opps, [])


if __name__ == "__main__":
    unittest.main()
