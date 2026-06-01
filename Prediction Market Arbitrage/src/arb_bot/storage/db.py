from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from time import time

from arb_bot.execution.paper import PaperFill
from arb_bot.models import ArbitrageOpportunity, MarketPair, Side, VenueMarket

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class ArbDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.conn.commit()

    def record_event(self, event_type: str, severity: str, message: str, payload: dict[str, object] | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO runtime_events (event_type, severity, message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_type, severity, message, json.dumps(payload or {}, sort_keys=True), time()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_market(self, market: VenueMarket) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO venue_markets
            (venue, market_id, title, category, yes_token_id, no_token_id, close_time, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue, market_id) DO UPDATE SET
                title = excluded.title,
                category = excluded.category,
                yes_token_id = excluded.yes_token_id,
                no_token_id = excluded.no_token_id,
                close_time = excluded.close_time
            """,
            (
                market.venue.value,
                market.market_id,
                market.title,
                market.category,
                market.yes_token_id,
                market.no_token_id,
                market.close_time,
                time(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_pair(self, pair: MarketPair) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO market_pairs
            (pair_id, name, category, polymarket_market_id, kalshi_market_id, confidence, status, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pair_id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                confidence = excluded.confidence,
                status = excluded.status
            """,
            (
                pair.pair_id,
                pair.name,
                pair.category,
                pair.polymarket_market_id,
                pair.kalshi_market_id,
                str(pair.confidence),
                pair.status.value,
                time(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_pair_candidate(
        self,
        poly_market: VenueMarket,
        kalshi_market: VenueMarket,
        confidence: str,
        passed_threshold: bool,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO pair_candidates
            (
                polymarket_market_id, kalshi_market_id,
                polymarket_title, kalshi_title,
                polymarket_category, kalshi_category,
                confidence, passed_threshold, discovered_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                poly_market.market_id,
                kalshi_market.market_id,
                poly_market.title,
                kalshi_market.title,
                poly_market.category,
                kalshi_market.category,
                confidence,
                1 if passed_threshold else 0,
                time(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_opportunity(self, opp: ArbitrageOpportunity) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO opportunities
            (pair_id, direction, size, gross_cost, net_cost, net_edge, expected_profit, detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opp.pair_id,
                opp.direction,
                str(opp.size),
                str(opp.gross_cost),
                str(opp.net_cost),
                str(opp.net_edge),
                str(opp.expected_profit),
                opp.detected_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_paper_fill(self, fill: PaperFill, opportunity_id: int | None = None) -> int:
        opp = fill.opportunity
        cur = self.conn.execute(
            """
            INSERT INTO paper_fills
            (opportunity_id, pair_id, direction, size, expected_profit, filled_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                opp.pair_id,
                opp.direction,
                str(opp.size),
                str(fill.expected_profit),
                fill.filled_at,
                fill.status,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def record_open_paper_position(self, opp: ArbitrageOpportunity, opportunity_id: int | None = None) -> int:
        entry_cost = opp.net_cost * opp.size
        cur = self.conn.execute(
            """
            INSERT INTO paper_positions
            (
                opportunity_id, pair_id, direction, status, size,
                yes_venue, yes_market_id, yes_entry_price,
                no_venue, no_market_id, no_entry_price,
                entry_cost, expected_profit, opened_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                opp.pair_id,
                opp.direction,
                "open",
                str(opp.size),
                opp.yes_leg.venue.value,
                opp.yes_leg.market_id,
                str(opp.yes_leg.avg_price),
                opp.no_leg.venue.value,
                opp.no_leg.market_id,
                str(opp.no_leg.avg_price),
                str(entry_cost),
                str(opp.expected_profit),
                opp.detected_at,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def resolve_paper_position(
        self,
        position_id: int,
        winning_side: Side,
        realized_payout: str,
        realized_profit: str,
        resolved_at: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE paper_positions
            SET status = 'resolved',
                winning_side = ?,
                realized_payout = ?,
                realized_profit = ?,
                resolved_at = ?
            WHERE id = ?
            """,
            (winning_side.value, realized_payout, realized_profit, resolved_at, position_id),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
