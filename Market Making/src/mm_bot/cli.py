from __future__ import annotations

import argparse
import asyncio
import sys

from mm_bot.config import load_config
from mm_bot.paper import PaperMarketMaker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mm-bot")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="Discover open Kalshi BTC 15-minute markets")
    discover.add_argument("--config", default="configs/config.example.toml")
    paper = sub.add_parser("paper", help="Run WebSocket paper market making without live orders")
    paper.add_argument("--config", default="configs/config.example.toml")
    paper.add_argument("--max-seconds", type=float, default=300)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    bot = PaperMarketMaker(config)
    if args.command == "discover":
        bot.discover()
        return 0
    if args.command == "paper":
        try:
            asyncio.run(bot.run(max_seconds=args.max_seconds))
            return 0
        except KeyboardInterrupt:
            print("paper market maker stopped", file=sys.stderr)
            return 130
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
