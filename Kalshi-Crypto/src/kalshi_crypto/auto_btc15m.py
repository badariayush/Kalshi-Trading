from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import time
from typing import TextIO

from kalshi_crypto.config import AppConfig
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.live_collectors import LiveDataSummary, run_live_data_audit
from kalshi_crypto.market_discovery import (
    Btc15mDiscovery,
    JsonFetcher,
    KalshiMarketWindow,
    discover_next_btc_15m_market,
    fetch_market_result,
)
from kalshi_crypto.paper_strategy import PaperMarket
from kalshi_crypto.storage import SQLiteAuditStore


MIN_SECONDS_TO_RESOLUTION_FOR_ENTRY = 600
DEFAULT_MARKET_CAPTURE_SECONDS = 900


@dataclass(frozen=True, slots=True)
class AutoBtc15mSummary:
    markets_run: int
    markets_skipped_too_close: int
    simulated_orders: int
    simulated_positions_closed: int
    simulated_total_pnl: Decimal
    audit_events: int
    last_market_ticker: str | None
    next_market_ticker: str | None
    network: str


Clock = Callable[[], int]
Sleeper = Callable[[float], None]
MarketRunner = Callable[..., LiveDataSummary]


@dataclass(frozen=True, slots=True)
class _SimulatedLeg:
    side: str
    entry_price: Decimal
    quantity: int
    fee: Decimal


@dataclass(frozen=True, slots=True)
class _PendingSimulatedPosition:
    market: KalshiMarketWindow
    legs: tuple[_SimulatedLeg, ...]


def run_auto_btc_15m_live_data(
    *,
    config: AppConfig,
    audit_db: str | Path,
    max_seconds: int,
    market_capture_seconds: int = DEFAULT_MARKET_CAPTURE_SECONDS,
    max_markets: int = 0,
    output_file: str | Path | None = None,
    stdout: TextIO | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
    fetch_json: JsonFetcher | None = None,
    market_runner: MarketRunner = run_live_data_audit,
) -> AutoBtc15mSummary:
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if market_capture_seconds <= 0:
        raise ValueError("market_capture_seconds must be positive")
    if max_markets < 0:
        raise ValueError("max_markets must be non-negative")

    now_ms = clock or _now_ms
    sleep_seconds = sleeper or time.sleep
    deadline_ms = now_ms() + (max_seconds * 1000)
    traded_market_tickers: set[str] = set()
    markets_run = 0
    markets_skipped_too_close = 0
    simulated_orders = 0
    simulated_positions_closed = 0
    simulated_total_pnl = Decimal("0")
    audit_events = 0
    last_market_ticker: str | None = None
    next_market_ticker: str | None = None
    pending_positions = _recover_pending_positions(Path(audit_db))

    discovery = discover_next_btc_15m_market(
        now_ms=now_ms(),
        fetch_json=fetch_json,
    )
    _print_initial_discovery(discovery, stdout)
    target_market = discovery.next_market

    while now_ms() < deadline_ms:
        pending_positions, closed_count, closed_pnl, closed_events = (
            _settle_pending_positions(
                pending_positions=pending_positions,
                audit_db=Path(audit_db),
                fetch_json=fetch_json,
                now_ms=now_ms(),
                stdout=stdout,
            )
        )
        simulated_positions_closed += closed_count
        simulated_total_pnl += closed_pnl
        audit_events += closed_events

        if max_markets and markets_run >= max_markets:
            if not pending_positions:
                break
            sleep_seconds(
                _seconds_until_next_pending_check(
                    pending_positions=pending_positions,
                    now_ms=now_ms(),
                    deadline_ms=deadline_ms,
                )
            )
            continue

        if target_market.ticker in traded_market_tickers:
            target_market = _rediscover_next_market(
                now_ms=now_ms(),
                fetch_json=fetch_json,
                stdout=stdout,
            )
            continue

        wait_seconds = max(0.0, (target_market.open_time_ms - now_ms()) / 1000)
        if wait_seconds > 0:
            if now_ms() + int(wait_seconds * 1000) >= deadline_ms:
                next_market_ticker = target_market.ticker
                break
            _print(
                stdout,
                "auto_waiting_for_market="
                f"ticker={target_market.ticker} seconds={int(wait_seconds)}",
            )
            sleep_seconds(wait_seconds)
            pending_positions, closed_count, closed_pnl, closed_events = (
                _settle_pending_positions(
                    pending_positions=pending_positions,
                    audit_db=Path(audit_db),
                    fetch_json=fetch_json,
                    now_ms=now_ms(),
                    stdout=stdout,
                )
            )
            simulated_positions_closed += closed_count
            simulated_total_pnl += closed_pnl
            audit_events += closed_events

        seconds_to_resolution = (target_market.close_time_ms - now_ms()) / 1000
        if seconds_to_resolution <= MIN_SECONDS_TO_RESOLUTION_FOR_ENTRY:
            markets_skipped_too_close += 1
            _print(
                stdout,
                "auto_market_skipped="
                f"ticker={target_market.ticker} reason=too_close_to_resolution "
                f"seconds_to_resolution={int(seconds_to_resolution)}",
            )
            target_market = _rediscover_next_market(
                now_ms=now_ms(),
                fetch_json=fetch_json,
                stdout=stdout,
            )
            continue

        following_market = _safe_rediscover_next_market(
            now_ms=now_ms(),
            fetch_json=fetch_json,
            stdout=stdout,
        )
        if following_market is not None and following_market.ticker != target_market.ticker:
            next_market_ticker = following_market.ticker
            _print(
                stdout,
                "auto_prepared_following_market_ticker="
                f"{following_market.ticker}",
            )

        capture_seconds = _market_monitor_seconds(
            requested_seconds=market_capture_seconds,
            seconds_to_resolution=seconds_to_resolution,
            remaining_seconds=(deadline_ms - now_ms()) / 1000,
        )
        if capture_seconds <= 0:
            markets_skipped_too_close += 1
            _print(
                stdout,
                f"auto_market_skipped=ticker={target_market.ticker} "
                "reason=no_time_before_ten_minute_guard",
            )
            target_market = following_market or _rediscover_next_market(
                now_ms=now_ms(),
                fetch_json=fetch_json,
                stdout=stdout,
            )
            continue

        before_order_count = simulated_orders
        _print(
            stdout,
            "auto_market_start="
            f"ticker={target_market.ticker} capture_seconds={capture_seconds} "
            f"seconds_to_resolution={int(seconds_to_resolution)}",
        )
        paper_market = _paper_market(target_market)
        if paper_market is None:
            _print(
                stdout,
                "auto_paper_strategy_disabled="
                f"ticker={target_market.ticker} reason=missing_market_strike",
            )
        summary = market_runner(
            config=config,
            audit_db=Path(audit_db),
            max_seconds=capture_seconds,
            kalshi_market_tickers=(target_market.ticker,),
            input_file=None,
            output_file=_output_file_for_market(output_file, target_market.ticker),
            stdout=stdout,
            paper_market=paper_market,
        )
        traded_market_tickers.add(target_market.ticker)
        markets_run += 1
        simulated_orders += summary.simulated_orders
        simulated_positions_closed += summary.simulated_positions_closed
        simulated_total_pnl += summary.simulated_realized_pnl
        audit_events += summary.audit_events
        last_market_ticker = target_market.ticker
        if (
            simulated_orders > before_order_count
            and summary.simulated_positions_closed == 0
        ):
            pending_position = _pending_position_from_audit(
                audit_db=Path(audit_db),
                market=target_market,
            )
            if pending_position is not None:
                pending_positions = (*pending_positions, pending_position)
                _print(
                    stdout,
                    "simulated_position_opened="
                    f"market_ticker={pending_position.market.ticker} "
                    f"legs={len(pending_position.legs)} "
                    f"sides={','.join(leg.side for leg in pending_position.legs)}",
                )
        _print(
            stdout,
            "auto_market_complete="
            f"ticker={target_market.ticker} simulated_orders={summary.simulated_orders}",
        )

        target_market = following_market or _rediscover_next_market(
            now_ms=now_ms(),
            fetch_json=fetch_json,
            stdout=stdout,
        )
        next_market_ticker = target_market.ticker

    pending_positions, closed_count, closed_pnl, closed_events = _settle_pending_positions(
        pending_positions=pending_positions,
        audit_db=Path(audit_db),
        fetch_json=fetch_json,
        now_ms=now_ms(),
        stdout=stdout,
    )
    simulated_positions_closed += closed_count
    simulated_total_pnl += closed_pnl
    audit_events += closed_events

    return AutoBtc15mSummary(
        markets_run=markets_run,
        markets_skipped_too_close=markets_skipped_too_close,
        simulated_orders=simulated_orders,
        simulated_positions_closed=simulated_positions_closed,
        simulated_total_pnl=simulated_total_pnl.quantize(Decimal("0.01")),
        audit_events=audit_events,
        last_market_ticker=last_market_ticker,
        next_market_ticker=next_market_ticker,
        network="attempted",
    )


def _print_initial_discovery(discovery: Btc15mDiscovery, stdout: TextIO | None) -> None:
    if discovery.current_market is not None:
        _print(
            stdout,
            "auto_skipped_current_market_ticker="
            f"{discovery.current_market.ticker}",
        )
    _print(
        stdout,
        "auto_next_market_ticker="
        f"{discovery.next_market.ticker} "
        f"open_time_ms={discovery.next_market.open_time_ms} "
        f"close_time_ms={discovery.next_market.close_time_ms}",
    )


def _rediscover_next_market(
    *,
    now_ms: int,
    fetch_json: JsonFetcher | None,
    stdout: TextIO | None,
) -> KalshiMarketWindow:
    discovery = discover_next_btc_15m_market(now_ms=now_ms, fetch_json=fetch_json)
    _print_initial_discovery(discovery, stdout)
    return discovery.next_market


def _safe_rediscover_next_market(
    *,
    now_ms: int,
    fetch_json: JsonFetcher | None,
    stdout: TextIO | None,
) -> KalshiMarketWindow | None:
    try:
        return _rediscover_next_market(
            now_ms=now_ms,
            fetch_json=fetch_json,
            stdout=stdout,
        )
    except SafetyError as exc:
        _print(stdout, f"auto_next_market_warning={exc}")
        return None


def _market_monitor_seconds(
    *,
    requested_seconds: int,
    seconds_to_resolution: float,
    remaining_seconds: float,
) -> int:
    seconds_before_close = int(seconds_to_resolution) - 1
    return max(0, min(requested_seconds, int(remaining_seconds), seconds_before_close))


def _pending_position_from_audit(
    *,
    audit_db: Path,
    market: KalshiMarketWindow,
) -> _PendingSimulatedPosition | None:
    records = SQLiteAuditStore(audit_db).read_all()
    if any(
        record.get("event_type") == "PositionClosed"
        and isinstance(record.get("payload"), dict)
        and record["payload"].get("market_ticker") == market.ticker
        for record in records
    ):
        return None
    legs: list[_SimulatedLeg] = []
    for record in records:
        if record.get("event_type") != "SimulatedOrderPlaced":
            continue
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if payload.get("market_ticker") != market.ticker:
            continue
        legs.append(
            _SimulatedLeg(
                side=str(payload.get("side", "yes")),
                entry_price=Decimal(str(payload.get("price", "0"))),
                quantity=int(payload.get("quantity", 1)),
                fee=Decimal(str(payload.get("fee", "0"))),
            )
        )
    if not legs:
        return None
    return _PendingSimulatedPosition(market=market, legs=tuple(legs[:2]))


def _recover_pending_positions(
    audit_db: Path,
) -> tuple[_PendingSimulatedPosition, ...]:
    records = SQLiteAuditStore(audit_db).read_all()
    closed_tickers = {
        str(payload.get("market_ticker"))
        for record in records
        if record.get("event_type") == "PositionClosed"
        if isinstance((payload := record.get("payload")), dict)
        if payload.get("market_ticker")
    }
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for record in records:
        if record.get("event_type") != "SimulatedOrderPlaced":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        ticker = str(payload.get("market_ticker", ""))
        if not ticker or ticker in closed_tickers:
            continue
        grouped.setdefault(ticker, []).append(payload)

    recovered: list[_PendingSimulatedPosition] = []
    for ticker, payloads in grouped.items():
        first = payloads[0]
        event_ticker = str(first.get("event_ticker", ""))
        open_time_ms = _optional_int(first.get("market_open_time_ms"))
        close_time_ms = _optional_int(first.get("market_close_time_ms"))
        strike = _optional_decimal(first.get("market_strike"))
        if not event_ticker or open_time_ms is None or close_time_ms is None:
            continue
        market = KalshiMarketWindow(
            ticker=ticker,
            event_ticker=event_ticker,
            status="closed",
            result=None,
            open_time_ms=open_time_ms,
            close_time_ms=close_time_ms,
            strike=strike,
        )
        legs = tuple(
            _SimulatedLeg(
                side=str(payload.get("side", "")),
                entry_price=Decimal(str(payload.get("price", "0"))),
                quantity=int(payload.get("quantity", 0)),
                fee=Decimal(str(payload.get("fee", "0"))),
            )
            for payload in payloads[:2]
            if str(payload.get("side", "")) in {"yes", "no"}
            and int(payload.get("quantity", 0)) > 0
        )
        if legs:
            recovered.append(_PendingSimulatedPosition(market=market, legs=legs))
    return tuple(recovered)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _optional_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed > Decimal("0") else None


def _settle_pending_positions(
    *,
    pending_positions: tuple[_PendingSimulatedPosition, ...],
    audit_db: Path,
    fetch_json: JsonFetcher | None,
    now_ms: int,
    stdout: TextIO | None,
) -> tuple[tuple[_PendingSimulatedPosition, ...], int, Decimal, int]:
    remaining_positions: list[_PendingSimulatedPosition] = []
    closed_count = 0
    total_pnl = Decimal("0")
    audit_events = 0
    store = SQLiteAuditStore(audit_db)
    for position in pending_positions:
        if now_ms < position.market.close_time_ms:
            remaining_positions.append(position)
            continue
        result = fetch_market_result(market=position.market, fetch_json=fetch_json)
        if result is None:
            remaining_positions.append(position)
            continue
        pnl = _simulated_pnl(position=position, result=result)
        event = _simulated_position_closed_event(
            position=position,
            result=result,
            pnl=pnl,
            timestamp_ms=now_ms,
        )
        store.append(event)
        closed_count += 1
        total_pnl += pnl
        audit_events += 1
        _print(
            stdout,
            "simulated_position_closed="
            f"market_ticker={position.market.ticker} result={result} "
            f"realized_pnl={_decimal_text(pnl)} "
            f"legs={len(position.legs)}",
        )
    return tuple(remaining_positions), closed_count, total_pnl, audit_events


def _simulated_pnl(*, position: _PendingSimulatedPosition, result: str) -> Decimal:
    return sum(
        (
            (Decimal("1") if result == leg.side else Decimal("0"))
            - leg.entry_price
        )
        * Decimal(leg.quantity)
        - leg.fee
        for leg in position.legs
    )


def _simulated_position_closed_event(
    *,
    position: _PendingSimulatedPosition,
    result: str,
    pnl: Decimal,
    timestamp_ms: int,
) -> AuditEvent:
    return AuditEvent.create(
        event_type="PositionClosed",
        worker="execution",
        payload={
            "market_ticker": position.market.ticker,
            "legs": [
                {
                    "side": leg.side,
                    "entry_price": _decimal_text(leg.entry_price),
                    "quantity": leg.quantity,
                    "fee": _decimal_text(leg.fee),
                }
                for leg in position.legs
            ],
            "result": result,
            "realized_pnl": _decimal_text(pnl),
            "total_fees": _decimal_text(
                sum((leg.fee for leg in position.legs), Decimal("0"))
            ),
            "outcome": _pnl_outcome(pnl),
            "simulated": True,
            "source": "kalshi_public_result",
            "execution": "simulated_pnl_only",
            "order_submission": "disabled",
        },
        causality_id=position.market.ticker,
        timestamp_ms=timestamp_ms,
    )


def _paper_market(market: KalshiMarketWindow) -> PaperMarket | None:
    if market.strike is None:
        return None
    return PaperMarket(
        ticker=market.ticker,
        series_ticker="KXBTC15M",
        underlying="BTC",
        strike=market.strike,
        open_time_ms=market.open_time_ms,
        close_time_ms=market.close_time_ms,
        event_ticker=market.event_ticker,
    )


def _pnl_outcome(pnl: Decimal) -> str:
    if pnl > 0:
        return "profit"
    if pnl < 0:
        return "loss"
    return "flat"


def _seconds_until_next_pending_check(
    *,
    pending_positions: tuple[_PendingSimulatedPosition, ...],
    now_ms: int,
    deadline_ms: int,
) -> float:
    next_close_ms = min(position.market.close_time_ms for position in pending_positions)
    target_ms = min(max(next_close_ms, now_ms + 15_000), deadline_ms)
    return max(0.0, (target_ms - now_ms) / 1000)


def _output_file_for_market(
    output_file: str | Path | None,
    market_ticker: str,
) -> Path | None:
    if output_file is None:
        return None
    path = Path(output_file)
    suffix = path.suffix or ".jsonl"
    stem = path.stem if path.suffix else path.name
    return path.with_name(f"{stem}-{market_ticker}{suffix}")


def _print(stdout: TextIO | None, message: str) -> None:
    if stdout is not None:
        print(message, file=stdout)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decimal_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")
