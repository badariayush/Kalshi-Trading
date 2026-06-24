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


def build_report(records: Iterable[dict[str, Any]]) -> ReportSummary:
    total_events = 0
    feed_unhealthy_events = 0
    execution_failures = 0
    simulated_orders = 0
    closed_positions = 0
    profitable_positions = 0
    losing_positions = 0
    flat_positions = 0
    total_realized_pnl = Decimal("0")

    for record in records:
        total_events += 1
        event_type = record.get("event_type")
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "FeedHealthEvaluated" and payload.get("healthy") is False:
            feed_unhealthy_events += 1
        if event_type == "ExecutionFailed":
            execution_failures += 1
        if event_type == "SimulatedOrderPlaced":
            simulated_orders += 1
        if event_type == "PositionClosed":
            closed_positions += 1
            realized_pnl = Decimal(str(payload.get("realized_pnl", "0")))
            total_realized_pnl += realized_pnl
            if realized_pnl > Decimal("0"):
                profitable_positions += 1
            elif realized_pnl < Decimal("0"):
                losing_positions += 1
            else:
                flat_positions += 1

    return ReportSummary(
        total_events=total_events,
        feed_unhealthy_events=feed_unhealthy_events,
        execution_failures=execution_failures,
        simulated_orders=simulated_orders,
        closed_positions=closed_positions,
        profitable_positions=profitable_positions,
        losing_positions=losing_positions,
        flat_positions=flat_positions,
        total_realized_pnl=total_realized_pnl.quantize(Decimal("0.01")),
    )


def load_report(audit_db: str | Path) -> ReportSummary:
    return build_report(SQLiteAuditStore(audit_db).read_all())
