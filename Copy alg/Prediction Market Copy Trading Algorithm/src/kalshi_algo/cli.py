from __future__ import annotations

import argparse
import asyncio
import signal
import sqlite3
import sys
from pathlib import Path

from .client import KalshiClient
from .config import load_config
from .engine import StrategyEngine, run_report
from .persistence import EventStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kalshi-algo")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-demo-trader")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--db", required=True)
    run_parser.add_argument("--max-seconds", type=float, default=None)
    run_parser.add_argument("--drain-on-timeout", action="store_true")
    run_parser.add_argument("--drain-on-interrupt", action="store_true")
    db_mode = run_parser.add_mutually_exclusive_group()
    db_mode.add_argument("--append-db", action="store_true")
    db_mode.add_argument("--fresh-db", action="store_true")

    replay_parser = subparsers.add_parser("replay-session")
    replay_parser.add_argument("--config", required=True)
    replay_parser.add_argument("--db", required=True)
    replay_parser.add_argument("--input", required=True)

    backfill_parser = subparsers.add_parser("backfill-markets")
    backfill_parser.add_argument("--config", required=True)

    report_parser = subparsers.add_parser("generate-report")
    report_parser.add_argument("--config", required=True)
    report_parser.add_argument("--db", required=True)
    return parser


async def _run_demo_trader(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    client = KalshiClient.from_env()
    db_path = Path(args.db)
    if args.fresh_db and db_path.exists():
        db_path.unlink()
    elif not args.append_db and _db_has_events(db_path):
        print(
            f"Refusing to append to existing session DB: {db_path}\n"
            "Use a new --db path, pass --fresh-db to replace it, or pass --append-db to continue the same DB.",
            file=sys.stderr,
        )
        return 2
    with EventStore(args.db) as store:
        engine = StrategyEngine(config, store)
        drain_requested = False
        force_requested = False
        loop = asyncio.get_running_loop()
        previous_handler = None

        def request_drain() -> None:
            nonlocal drain_requested, force_requested
            if not args.drain_on_interrupt:
                force_requested = True
                return
            if not drain_requested:
                drain_requested = True
                engine.accepting_new_entries = False
                print(
                    "Ctrl+C received: draining open positions. Press Ctrl+C again to force-liquidate.",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                force_requested = True
                print(
                    "Second Ctrl+C received: force-liquidating open positions.",
                    file=sys.stderr,
                    flush=True,
                )

        try:
            previous_handler = signal.getsignal(signal.SIGINT)
            loop.add_signal_handler(signal.SIGINT, request_drain)
        except (NotImplementedError, RuntimeError):
            previous_handler = None
        try:
            await engine.run_live(
                client,
                max_seconds=args.max_seconds,
                drain_on_timeout=args.drain_on_timeout,
                drain_requested=lambda: drain_requested,
                force_liquidation_requested=lambda: force_requested,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            for event in engine.liquidate_all("keyboard_interrupt_liquidation"):
                engine.emit(event)
            return 130
        finally:
            try:
                loop.remove_signal_handler(signal.SIGINT)
                if callable(previous_handler):
                    signal.signal(signal.SIGINT, previous_handler)
            except (NotImplementedError, RuntimeError):
                pass
    return 0


def _db_has_events(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        with sqlite3.connect(path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'action_events'"
            )
            if cursor.fetchone() is None:
                return False
            cursor = conn.execute("SELECT 1 FROM action_events LIMIT 1")
            return cursor.fetchone() is not None
    except sqlite3.DatabaseError:
        return True


async def _replay_session(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    with EventStore(args.db) as store:
        engine = StrategyEngine(config, store)
        await engine.run_replay_file(args.input)
    return 0


async def _backfill_markets(args: argparse.Namespace) -> int:
    load_config(args.config)
    client = KalshiClient.from_env()
    rows = await client.backfill_markets()
    for row in rows:
        print(row)
    return 0


def _generate_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(run_report(config, args.db))
    return 0


def _dispatch(command: str, args: argparse.Namespace) -> int:
    if command == "run-demo-trader":
        return asyncio.run(_run_demo_trader(args))
    if command == "replay-session":
        return asyncio.run(_replay_session(args))
    if command == "backfill-markets":
        return asyncio.run(_backfill_markets(args))
    if command == "generate-report":
        return _generate_report(args)
    raise ValueError(f"Unsupported command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return _dispatch(args.command, args)


def run_demo_trader_main() -> int:
    return main(["run-demo-trader", *sys.argv[1:]])


def replay_session_main() -> int:
    return main(["replay-session", *sys.argv[1:]])


def backfill_markets_main() -> int:
    return main(["backfill-markets", *sys.argv[1:]])


def generate_report_main() -> int:
    return main(["generate-report", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
