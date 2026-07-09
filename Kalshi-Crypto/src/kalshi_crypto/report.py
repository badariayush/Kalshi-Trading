from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from kalshi_crypto.storage import SQLiteAuditStore


@dataclass(frozen=True, slots=True)
class ReportSummary:
    total_events: int
    feed_unhealthy_events: int
    execution_failures: int
    simulated_orders: int
    closed_positions: int
    profitable_positions: int
    losing_positions: int
    flat_positions: int
    total_realized_pnl: Decimal
    open_positions: int
    win_rate_pct: Decimal
    average_realized_pnl: Decimal
    total_fees: Decimal

    @property
    def status(self) -> str:
        if self.execution_failures > 0:
            return "error"
        if self.feed_unhealthy_events > 0:
            return "warning"
        if self.closed_positions == 0:
            return "no_trades"
        if self.total_realized_pnl > Decimal("0"):
            return "profit"
        if self.total_realized_pnl < Decimal("0"):
            return "loss"
        return "flat"


def build_report(
    records: Iterable[dict[str, Any]],
    *,
    total_events: int | None = None,
    feed_unhealthy_events: int | None = None,
) -> ReportSummary:
    counted_events = 0
    counted_feed_unhealthy_events = 0
    execution_failures = 0
    simulated_orders = 0
    closed_positions = 0
    profitable_positions = 0
    losing_positions = 0
    flat_positions = 0
    total_realized_pnl = Decimal("0")
    entry_markets: set[str] = set()
    closed_markets: set[str] = set()
    order_fees_by_market: dict[str, Decimal] = {}
    close_fees_by_market: dict[str, Decimal] = {}

    for record in records:
        counted_events += 1
        event_type = record.get("event_type")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "FeedHealthEvaluated" and payload.get("healthy") is False:
            counted_feed_unhealthy_events += 1
        if event_type == "ExecutionFailed":
            execution_failures += 1
        if event_type == "SimulatedOrderPlaced":
            simulated_orders += 1
            market_ticker = str(payload.get("market_ticker", ""))
            if market_ticker:
                if int(payload.get("leg_index", 1)) == 1:
                    entry_markets.add(market_ticker)
                order_fees_by_market[market_ticker] = (
                    order_fees_by_market.get(market_ticker, Decimal("0"))
                    + Decimal(str(payload.get("fee", "0")))
                )
        if event_type == "PositionClosed":
            closed_positions += 1
            market_ticker = str(payload.get("market_ticker", ""))
            if market_ticker:
                closed_markets.add(market_ticker)
                close_fees_by_market[market_ticker] = Decimal(
                    str(payload.get("total_fees", "0"))
                )
            realized_pnl = Decimal(str(payload.get("realized_pnl", "0")))
            total_realized_pnl += realized_pnl
            if realized_pnl > Decimal("0"):
                profitable_positions += 1
            elif realized_pnl < Decimal("0"):
                losing_positions += 1
            else:
                flat_positions += 1

    total_fees = sum(
        (
            close_fees_by_market.get(
                market_ticker,
                order_fees_by_market.get(market_ticker, Decimal("0")),
            )
            for market_ticker in entry_markets | closed_markets
        ),
        Decimal("0"),
    )
    win_rate_pct = (
        Decimal(profitable_positions) / Decimal(closed_positions) * Decimal("100")
        if closed_positions
        else Decimal("0")
    )
    average_realized_pnl = (
        total_realized_pnl / Decimal(closed_positions)
        if closed_positions
        else Decimal("0")
    )

    return ReportSummary(
        total_events=counted_events if total_events is None else total_events,
        feed_unhealthy_events=(
            counted_feed_unhealthy_events
            if feed_unhealthy_events is None
            else feed_unhealthy_events
        ),
        execution_failures=execution_failures,
        simulated_orders=simulated_orders,
        closed_positions=closed_positions,
        profitable_positions=profitable_positions,
        losing_positions=losing_positions,
        flat_positions=flat_positions,
        total_realized_pnl=total_realized_pnl.quantize(Decimal("0.01")),
        open_positions=len(entry_markets - closed_markets),
        win_rate_pct=win_rate_pct.quantize(Decimal("0.01")),
        average_realized_pnl=average_realized_pnl.quantize(Decimal("0.01")),
        total_fees=total_fees.quantize(Decimal("0.01")),
    )


def load_report(audit_db: str | Path) -> ReportSummary:
    store = SQLiteAuditStore(audit_db)
    records = store.read_report_events()
    unhealthy_events = sum(
        int(record.get("payload", {}).get("feed_unhealthy_events", 0))
        for record in records
        if record.get("event_type") == "LiveDataAuditCompleted"
        and isinstance(record.get("payload"), dict)
    )
    return build_report(
        records,
        total_events=store.latest_sequence(),
        feed_unhealthy_events=unhealthy_events,
    )
