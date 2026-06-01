from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
from pathlib import Path
import sys
from time import time

from arb_bot.config import load_config
from arb_bot.matching.pair_scorer import auto_pair_markets, top_pair_candidates
from arb_bot.models import BookLevel, MarketPair, OrderBook, PairStatus, Venue
from arb_bot.reporting.reports import summarize_database
from arb_bot.runtime.paper_runner import LiveDataPaperRunner
from arb_bot.strategy.arbitrage import ArbSettings, find_opportunities
from arb_bot.storage.db import ArbDatabase
from arb_bot.venues.kalshi import KalshiConnector
from arb_bot.venues.polymarket import PolymarketConnector


def _demo() -> None:
    now = time()
    pair = MarketPair(
        pair_id="demo",
        name="Demo equivalent market",
        category="crypto",
        polymarket_market_id="poly-demo",
        kalshi_market_id="kalshi-demo",
        confidence=Decimal("0.95"),
        status=PairStatus.ACTIVE,
    )
    poly = OrderBook(
        venue=Venue.POLYMARKET,
        market_id="poly-demo",
        yes_asks=[BookLevel(Decimal("0.42"), Decimal("30"))],
        no_asks=[BookLevel(Decimal("0.58"), Decimal("30"))],
        timestamp=now,
    )
    kalshi = OrderBook(
        venue=Venue.KALSHI,
        market_id="kalshi-demo",
        yes_asks=[BookLevel(Decimal("0.48"), Decimal("10"))],
        no_asks=[BookLevel(Decimal("0.55"), Decimal("12"))],
        timestamp=now,
    )
    opps = find_opportunities(pair, poly, kalshi, ArbSettings(), now=now)
    if not opps:
        print("No demo opportunity found")
        return
    for opp in opps:
        print(f"{opp.direction}: size={opp.size} net_edge={opp.net_edge} expected_profit={opp.expected_profit}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arb-bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run a local deterministic arbitrage example")
    discover = sub.add_parser("discover", help="Discover live markets and auto-pairs without entering positions")
    discover.add_argument("--config", default="configs/config.example.toml")
    discover.add_argument("--db", default="data/discovery.sqlite3")
    discover.add_argument("--query", default="crypto")
    discover.add_argument("--limit", type=int, default=None)
    discover.add_argument("--min-confidence", type=str, default=None)
    discover.add_argument("--fresh-db", action="store_true")
    paper = sub.add_parser("paper", help="Run live-data paper trading with WebSocket monitors and no real orders")
    paper.add_argument("--config", default="configs/config.example.toml")
    paper.add_argument("--db", required=True)
    paper.add_argument("--max-seconds", type=float, default=300)
    paper.add_argument("--query", default="crypto")
    paper.add_argument("--limit", type=int, default=None)
    paper.add_argument("--min-confidence", type=str, default=None)
    paper.add_argument("--rediscover-seconds", type=float, default=None)
    paper.add_argument("--fresh-db", action="store_true")
    report = sub.add_parser("report", help="Summarize a local SQLite database")
    report.add_argument("--db", default="data/arbitrage.sqlite3")
    args = parser.parse_args(argv)

    if args.command == "demo":
        _demo()
        return 0
    if args.command == "discover":
        return _discover(args)
    if args.command == "paper":
        try:
            return asyncio.run(_paper(args))
        except KeyboardInterrupt:
            print("\nPaper run stopped by user. Generate a report with:", file=sys.stderr)
            print('PYTHONPATH=src python -m arb_bot.cli report --db "$(ls -t data/session_*.sqlite3 | head -1)"', file=sys.stderr)
            return 130
    if args.command == "report":
        print(summarize_database(Path(args.db)))
        return 0
    parser.error("unknown command")
    return 2


def _prepare_db_path(path: str, fresh_db: bool) -> Path:
    db_path = Path(path)
    if fresh_db and db_path.exists():
        db_path.unlink()
    if db_path.exists() and not fresh_db:
        return db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def _discover(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.min_confidence is not None:
        object.__setattr__(config.runtime, "min_pair_confidence", Decimal(args.min_confidence))
    db_path = _prepare_db_path(args.db, args.fresh_db)
    db = ArbDatabase(db_path)
    poly = PolymarketConnector()
    kalshi = KalshiConnector()
    try:
        limit = args.limit or config.runtime.discovery_limit
        poly_markets = poly.discover_markets(limit=limit, tag=args.query)
        kalshi_markets = kalshi.discover_markets(limit=limit, query=args.query)
        for market in [*poly_markets, *kalshi_markets]:
            db.record_market(market)
        paired = auto_pair_markets(
            poly_markets,
            kalshi_markets,
            min_confidence=config.runtime.min_pair_confidence,
            max_pairs=config.runtime.max_pairs,
        )
        for pair, _, _ in paired:
            db.record_pair(pair)
        for confidence, poly_market, kalshi_market in top_pair_candidates(poly_markets, kalshi_markets, limit=25):
            db.record_pair_candidate(
                poly_market,
                kalshi_market,
                str(confidence),
                confidence >= config.runtime.min_pair_confidence,
            )
        db.record_event(
            "discover",
            "info",
            "discovery completed",
            {"polymarket": len(poly_markets), "kalshi": len(kalshi_markets), "pairs": len(paired)},
        )
    finally:
        db.close()
    print(f"Polymarket markets: {len(poly_markets)}")
    print(f"Kalshi markets: {len(kalshi_markets)}")
    print(f"Auto-pairs: {len(paired)}")
    print(f"Database: {db_path}")
    return 0


async def _paper(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.min_confidence is not None:
        object.__setattr__(config.runtime, "min_pair_confidence", Decimal(args.min_confidence))
    if args.limit is not None:
        object.__setattr__(config.runtime, "discovery_limit", args.limit)
    if config.runtime.mode != "paper":
        print("Refusing to run live-data paper mode unless config runtime.mode = \"paper\".", file=sys.stderr)
        return 2
    db_path = _prepare_db_path(args.db, args.fresh_db)
    if args.rediscover_seconds is not None:
        object.__setattr__(config.runtime, "rediscovery_interval_seconds", args.rediscover_seconds)
    runner = LiveDataPaperRunner(config, db_path)
    result = await runner.run(max_seconds=args.max_seconds, asset_query=args.query)
    print(f"Startup/rediscovery market universe: {result.markets_discovered}")
    print(f"Auto-pairs: {result.pairs_discovered}")
    print(f"Opportunities: {result.opportunities}")
    print(f"Paper positions opened: {result.paper_positions}")
    print(f"WebSocket messages logged: {result.websocket_messages}")
    print(f"Database: {result.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
