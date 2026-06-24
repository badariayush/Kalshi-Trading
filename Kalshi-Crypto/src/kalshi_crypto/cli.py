from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from kalshi_crypto.config import ConfigError, RuntimeMode, load_app_config
from kalshi_crypto.data_only import run_data_only_replay
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.live_collectors import run_live_data_audit
from kalshi_crypto.paper_algorithm import run_paper_algorithm
from kalshi_crypto.report import load_report


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor(args.config, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "doctor-live-data":
        return _doctor_live_data(args.config, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "data-only":
        return _data_only(
            args.config,
            args.replay_file,
            args.audit_db,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if args.command == "live-data":
        return _live_data(
            args.config,
            args.audit_db,
            args.max_seconds,
            tuple(args.kalshi_market_ticker),
            args.input_file,
            args.output_file,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if args.command == "report":
        return _report(args.audit_db, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "paper":
        return _paper(
            args.config,
            args.audit_db,
            args.max_seconds,
            args.live_input_file,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

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

    data_only = subparsers.add_parser(
        "data-only",
        help="run data-only ingestion from a local replay file",
    )
    data_only.add_argument("--config", required=True, help="path to TOML config")
    data_only.add_argument(
        "--replay-file",
        help="local JSONL replay file; use live-data for WebSocket audits",
    )
    data_only.add_argument(
        "--audit-db",
        default="data/audit.sqlite3",
        help="SQLite audit database path",
    )

    live_data = subparsers.add_parser(
        "live-data",
        help="audit live-data WebSocket messages without order execution",
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
        help="Kalshi market ticker to subscribe to; repeat for multiple markets",
    )
    live_data.add_argument(
        "--input-file",
        help="local JSONL provider-message file; skips network capture when set",
    )
    live_data.add_argument(
        "--output-file",
        help="write captured or replayed provider messages as JSONL",
    )

    report = subparsers.add_parser("report", help="summarize an audit SQLite database")
    report.add_argument(
        "--audit-db",
        default="data/audit.sqlite3",
        help="SQLite audit database path",
    )

    paper = subparsers.add_parser(
        "paper",
        help="run the paper algorithm with real order submission disabled",
    )
    paper.add_argument("--config", required=True, help="path to TOML config")
    paper.add_argument(
        "--audit-db",
        default="data/paper.sqlite3",
        help="SQLite audit database path",
    )
    paper.add_argument(
        "--max-seconds",
        type=int,
        default=600,
        help="paper run duration budget; deterministic fixture currently runs once",
    )
    paper.add_argument(
        "--live-input-file",
        help="local provider-message JSONL file to drive the paper worker chain",
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


def _data_only(
    config_path: str,
    replay_file: str | None,
    audit_db: str,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        config = load_app_config(config_path)
        summary = run_data_only_replay(
            config=config,
            replay_file=replay_file,
            audit_db=Path(audit_db),
        )
    except (ConfigError, SafetyError, ValueError) as exc:
        print(f"data_only_error={exc}", file=stderr)
        return 2

    print("status=ok", file=stdout)
    print(f"mode={config.runtime.mode.value}", file=stdout)
    print("network=not_attempted", file=stdout)
    print("execution=not_attempted", file=stdout)
    print(f"replay_events={summary.replay_events}", file=stdout)
    print(f"normalized_orderbooks={summary.normalized_orderbooks}", file=stdout)
    print(f"market_events={summary.market_events}", file=stdout)
    print(f"cf_benchmark_ticks={summary.cf_benchmark_ticks}", file=stdout)
    print(f"cf_candles={summary.cf_candles}", file=stdout)
    print(
        f"current_windows={_csv_or_none(summary.current_window_tickers)}",
        file=stdout,
    )
    print(f"next_windows={_csv_or_none(summary.next_window_tickers)}", file=stdout)
    print(f"stale_events={summary.stale_events}", file=stdout)
    print(f"audit_events={summary.audit_events}", file=stdout)
    return 0


def _live_data(
    config_path: str,
    audit_db: str,
    max_seconds: int,
    kalshi_market_tickers: tuple[str, ...],
    input_file: str | None,
    output_file: str | None,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        config = load_app_config(config_path)
        summary = run_live_data_audit(
            config=config,
            audit_db=Path(audit_db),
            max_seconds=max_seconds,
            kalshi_market_tickers=kalshi_market_tickers,
            input_file=input_file,
            output_file=output_file,
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
    print(f"audit_events={summary.audit_events}", file=stdout)
    return 0


def _report(audit_db: str, stdout: TextIO, stderr: TextIO) -> int:
    try:
        summary = load_report(Path(audit_db))
    except (OSError, ValueError) as exc:
        print(f"report_error={exc}", file=stderr)
        return 2

    print(f"status={summary.status}", file=stdout)
    print(f"total_events={summary.total_events}", file=stdout)
    print(f"feed_unhealthy_events={summary.feed_unhealthy_events}", file=stdout)
    print(f"execution_failures={summary.execution_failures}", file=stdout)
    print(f"closed_positions={summary.closed_positions}", file=stdout)
    print(f"profitable_positions={summary.profitable_positions}", file=stdout)
    print(f"losing_positions={summary.losing_positions}", file=stdout)
    print(f"flat_positions={summary.flat_positions}", file=stdout)
    print(f"total_realized_pnl={summary.total_realized_pnl}", file=stdout)
    return 0


def _paper(
    config_path: str,
    audit_db: str,
    max_seconds: int,
    live_input_file: str | None,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        config = load_app_config(config_path)
        summary = run_paper_algorithm(
            config=config,
            audit_db=Path(audit_db),
            max_seconds=max_seconds,
            stdout=stdout,
            live_input_file=live_input_file,
        )
    except (ConfigError, SafetyError, ValueError, RuntimeError) as exc:
        print(f"paper_error={exc}", file=stderr)
        return 2

    print("status=ok", file=stdout)
    print(f"audit_events={summary.audit_events}", file=stdout)
    print(f"report_status={summary.report.status}", file=stdout)
    print(f"total_realized_pnl={summary.report.total_realized_pnl}", file=stdout)
    print("order_submission=disabled", file=stdout)
    return 0


def _csv_or_none(values: tuple[str, ...]) -> str:
    if not values:
        return "none"
    return ",".join(values)


if __name__ == "__main__":
    raise SystemExit(main())
