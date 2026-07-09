from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from kalshi_crypto.auto_btc15m import (
    DEFAULT_MARKET_CAPTURE_SECONDS,
    run_auto_btc_15m_live_data,
)
from kalshi_crypto.config import ConfigError, RuntimeMode, load_app_config
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.live_collectors import run_live_data_audit
from kalshi_crypto.report import load_report
from kalshi_crypto.storage import SQLiteAuditStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor(args.config, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "doctor-live-data":
        return _doctor_live_data(args.config, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "live-data":
        return _live_data(
            args.config,
            args.audit_db,
            args.max_seconds,
            tuple(args.kalshi_market_ticker),
            args.output_file,
            args.auto_btc_15m,
            args.market_capture_seconds,
            args.max_markets,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if args.command == "report":
        return _report(args.audit_db, stdout=sys.stdout, stderr=sys.stderr)

    parser.error("unknown command")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kalshi_crypto")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="validate local config safely")
    doctor.add_argument("--config", required=True, help="path to TOML config")

    doctor_live_data = subparsers.add_parser(
        "doctor-live-data",
        help="validate live data and order API readiness without network calls",
    )
    doctor_live_data.add_argument("--config", required=True, help="path to TOML config")

    live_data = subparsers.add_parser(
        "live-data",
        help="capture and audit live Kalshi/Coinbase WebSocket messages",
    )
    live_data.add_argument("--config", required=True, help="path to TOML config")
    live_data.add_argument(
        "--audit-db",
        default="data/live.sqlite3",
        help="SQLite audit database path",
    )
    live_data.add_argument(
        "--max-seconds",
        type=int,
        default=600,
        help="duration budget for a network live-data capture",
    )
    live_data.add_argument(
        "--kalshi-market-ticker",
        action="append",
        default=[],
        help=(
            "actual Kalshi market ticker to subscribe to; repeat for multiple "
            "markets. Not needed with --auto-btc-15m"
        ),
    )
    live_data.add_argument(
        "--auto-btc-15m",
        action="store_true",
        help="skip the current BTC 15m market and roll through future BTC 15m markets",
    )
    live_data.add_argument(
        "--market-capture-seconds",
        type=int,
        default=DEFAULT_MARKET_CAPTURE_SECONDS,
        help="per-market capture duration for --auto-btc-15m",
    )
    live_data.add_argument(
        "--max-markets",
        type=int,
        default=0,
        help="maximum auto BTC 15m markets to run; 0 means until max-seconds expires",
    )
    live_data.add_argument(
        "--output-file",
        help="write captured live provider messages as JSONL",
    )

    report = subparsers.add_parser(
        "report",
        help="summarize a live-data audit SQLite database",
    )
    report.add_argument(
        "--audit-db",
        default="data/audit.sqlite3",
        help="SQLite audit database path",
    )

    return parser


def _doctor(config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    try:
        config = load_app_config(config_path)
    except ConfigError as exc:
        print(f"config_error={exc}", file=stderr)
        return 2

    print("status=ok", file=stdout)
    print(f"mode={config.runtime.mode.value}", file=stdout)
    print(
        f"trade_mcp={'enabled' if config.runtime.allow_trade_mcp else 'disabled'}",
        file=stdout,
    )
    print("execution=not_attempted", file=stdout)
    print("network=not_attempted", file=stdout)
    if config.runtime.mode is RuntimeMode.LIVE:
        print("warning=live_config_loaded_without_order_execution", file=stdout)

    return 0


def _doctor_live_data(config_path: str, stdout: TextIO, stderr: TextIO) -> int:
    try:
        config = load_app_config(config_path)
    except ConfigError as exc:
        print(f"config_error={exc}", file=stderr)
        return 2

    if config.order_api.enable_order_api and not config.live_data.enable_live_network:
        print(
            "config_error=order API requires live network readiness",
            file=stderr,
        )
        return 2
    if config.order_api.allow_order_submission:
        print(
            "config_error=order submission requires a separate execution approval",
            file=stderr,
        )
        return 2

    print("status=ok", file=stdout)
    print(f"mode={config.runtime.mode.value}", file=stdout)
    print("network=not_attempted", file=stdout)
    print("execution=not_attempted", file=stdout)
    print(
        f"live_network={'enabled' if config.live_data.enable_live_network else 'disabled'}",
        file=stdout,
    )
    print(
        f"order_api={'enabled' if config.order_api.enable_order_api else 'disabled'}",
        file=stdout,
    )
    print(
        "order_submission="
        f"{'enabled' if config.order_api.allow_order_submission else 'disabled'}",
        file=stdout,
    )
    print(f"kalshi_ws_url={config.live_data.kalshi_ws_url}", file=stdout)
    print(f"coinbase_ws_url={config.live_data.coinbase_ws_url}", file=stdout)
    print(f"kalshi_channels={','.join(config.live_data.kalshi_channels)}", file=stdout)
    print(
        f"coinbase_channels={','.join(config.live_data.coinbase_channels)}",
        file=stdout,
    )
    return 0


def _live_data(
    config_path: str,
    audit_db: str,
    max_seconds: int,
    kalshi_market_tickers: tuple[str, ...],
    output_file: str | None,
    auto_btc_15m: bool,
    market_capture_seconds: int,
    max_markets: int,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        config = load_app_config(config_path)
        if auto_btc_15m:
            auto_summary = run_auto_btc_15m_live_data(
                config=config,
                audit_db=Path(audit_db),
                max_seconds=max_seconds,
                market_capture_seconds=market_capture_seconds,
                max_markets=max_markets,
                output_file=output_file,
                stdout=stdout,
            )
            print("status=ok", file=stdout)
            print(f"mode={config.runtime.mode.value}", file=stdout)
            print(f"network={auto_summary.network}", file=stdout)
            print("execution=not_attempted", file=stdout)
            print("order_submission=disabled", file=stdout)
            print("auto_btc_15m=enabled", file=stdout)
            print(f"markets_run={auto_summary.markets_run}", file=stdout)
            print(
                f"markets_skipped_too_close="
                f"{auto_summary.markets_skipped_too_close}",
                file=stdout,
            )
            print(f"simulated_orders={auto_summary.simulated_orders}", file=stdout)
            print(
                f"simulated_positions_closed="
                f"{auto_summary.simulated_positions_closed}",
                file=stdout,
            )
            print(
                f"simulated_total_pnl={auto_summary.simulated_total_pnl}",
                file=stdout,
            )
            print(f"audit_events={auto_summary.audit_events}", file=stdout)
            print(
                f"last_market_ticker={auto_summary.last_market_ticker or 'none'}",
                file=stdout,
            )
            print(
                f"next_market_ticker={auto_summary.next_market_ticker or 'none'}",
                file=stdout,
            )
            return 0
        if not kalshi_market_tickers:
            raise ValueError(
                "--kalshi-market-ticker is required unless --auto-btc-15m is set"
            )
        summary = run_live_data_audit(
            config=config,
            audit_db=Path(audit_db),
            max_seconds=max_seconds,
            kalshi_market_tickers=kalshi_market_tickers,
            input_file=None,
            output_file=output_file,
            stdout=stdout,
        )
    except Exception as exc:
        print(f"live_data_error={exc}", file=stderr)
        return 2

    print("status=ok", file=stdout)
    print(f"mode={config.runtime.mode.value}", file=stdout)
    print(f"network={summary.network}", file=stdout)
    print("execution=not_attempted", file=stdout)
    print("order_submission=disabled", file=stdout)
    print(f"raw_messages={summary.raw_messages}", file=stdout)
    print(f"kalshi_messages={summary.kalshi_messages}", file=stdout)
    print(f"coinbase_messages={summary.coinbase_messages}", file=stdout)
    print(f"feed_unhealthy_events={summary.feed_unhealthy_events}", file=stdout)
    print(f"simulated_orders={summary.simulated_orders}", file=stdout)
    print(f"audit_events={summary.audit_events}", file=stdout)
    return 0


def _report(audit_db: str, stdout: TextIO, stderr: TextIO) -> int:
    try:
        scope_error = _live_report_scope_error(Path(audit_db))
        if scope_error is not None:
            print(f"report_error={scope_error}", file=stderr)
            return 2
        summary = load_report(Path(audit_db))
    except (OSError, ValueError) as exc:
        print(f"report_error={exc}", file=stderr)
        return 2

    print(f"status={summary.status}", file=stdout)
    print(f"total_events={summary.total_events}", file=stdout)
    print(f"feed_unhealthy_events={summary.feed_unhealthy_events}", file=stdout)
    print(f"execution_failures={summary.execution_failures}", file=stdout)
    print(f"simulated_orders={summary.simulated_orders}", file=stdout)
    print(f"closed_positions={summary.closed_positions}", file=stdout)
    print(f"profitable_positions={summary.profitable_positions}", file=stdout)
    print(f"losing_positions={summary.losing_positions}", file=stdout)
    print(f"flat_positions={summary.flat_positions}", file=stdout)
    print(f"open_positions={summary.open_positions}", file=stdout)
    print(f"win_rate_pct={summary.win_rate_pct}", file=stdout)
    print(f"average_realized_pnl={summary.average_realized_pnl}", file=stdout)
    print(f"total_fees={summary.total_fees}", file=stdout)
    print(f"total_realized_pnl={summary.total_realized_pnl}", file=stdout)
    return 0


def _live_report_scope_error(audit_db: Path) -> str | None:
    if not SQLiteAuditStore(audit_db).has_event_type("LiveDataAuditCompleted"):
        return "report command only accepts live-data audit databases"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
