from __future__ import annotations

from decimal import Decimal
from tempfile import TemporaryDirectory
from time import time
from pathlib import Path
import unittest

from arb_bot.models import BookLevel, MarketPair, OrderBook, PairStatus, Side, Venue
from arb_bot.storage.db import ArbDatabase
from arb_bot.strategy.arbitrage import ArbSettings, find_opportunities


class StorageTests(unittest.TestCase):
    def test_records_open_and_resolved_paper_position(self) -> None:
        now = time()
        pair = MarketPair("p1", "Example", "crypto", "poly", "kalshi", Decimal("0.95"), PairStatus.ACTIVE)
        poly = OrderBook(Venue.POLYMARKET, "poly", [BookLevel(Decimal("0.42"), Decimal("12"))], [], now)
        kalshi = OrderBook(Venue.KALSHI, "kalshi", [], [BookLevel(Decimal("0.55"), Decimal("12"))], now)
        opp = find_opportunities(pair, poly, kalshi, ArbSettings(), now=now)[0]

        with TemporaryDirectory() as tmp:
            db = ArbDatabase(Path(tmp) / "arb.sqlite3")
            opportunity_id = db.record_opportunity(opp)
            position_id = db.record_open_paper_position(opp, opportunity_id)
            db.resolve_paper_position(
                position_id=position_id,
                winning_side=Side.YES,
                realized_payout=str(opp.size),
                realized_profit=str(opp.expected_profit),
                resolved_at=now + 60,
            )
            row = db.conn.execute(
                "SELECT status, winning_side, realized_profit FROM paper_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            db.close()

        self.assertEqual(row, ("resolved", "yes", "0.300"))


if __name__ == "__main__":
    unittest.main()
