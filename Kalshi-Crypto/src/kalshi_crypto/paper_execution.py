from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TextIO

from kalshi_crypto.events import AuditEvent
from kalshi_crypto.models import Position, Side
from kalshi_crypto.money import kalshi_general_taker_fee


@dataclass(frozen=True, slots=True)
class PaperExecutionOrder:
    market_ticker: str
    side: Side
    quantity: int
    price: Decimal
    authorization_event_id: str
    timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.market_ticker:
            raise ValueError("market_ticker is required")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.price <= Decimal("0") or self.price >= Decimal("1"):
            raise ValueError("price must be between 0 and 1")
        if not self.authorization_event_id:
            raise ValueError("authorization_event_id is required")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class PaperEntryResult:
    position: Position
    events: tuple[AuditEvent, ...]


@dataclass(frozen=True, slots=True)
class PaperExitResult:
    realized_pnl: Decimal
    events: tuple[AuditEvent, ...]


def execute_paper_entry(
    order: PaperExecutionOrder,
    stdout: TextIO,
) -> PaperEntryResult:
    fee = kalshi_general_taker_fee(order.quantity, order.price)
    position = Position(
        market_ticker=order.market_ticker,
        side=order.side,
        quantity=order.quantity,
        entry_price=order.price,
        entry_fee=fee,
    )
    submitted = _order_submitted_event(
        order=order,
        action="buy",
        causality_id=order.authorization_event_id,
    )
    filled = _fill_recorded_event(
        order=order,
        action="buy",
        fee=fee,
        causality_id=submitted.event_id,
        realized_pnl=None,
    )
    print(
        "order placed for "
        f"{order.market_ticker} {order.side.value.upper()} at {_price(order.price)} "
        f"for {order.quantity} contracts (paper)",
        file=stdout,
    )
    print(
        "order filled for "
        f"{order.market_ticker} {order.side.value.upper()} at {_price(order.price)} "
        f"for {order.quantity} contracts (paper)",
        file=stdout,
    )
    return PaperEntryResult(position=position, events=(submitted, filled))


def execute_paper_exit(
    position: Position,
    exit_price: Decimal,
    authorization_event_id: str,
    timestamp_ms: int,
    stdout: TextIO,
) -> PaperExitResult:
    order = PaperExecutionOrder(
        market_ticker=position.market_ticker,
        side=position.side,
        quantity=position.quantity,
        price=exit_price,
        authorization_event_id=authorization_event_id,
        timestamp_ms=timestamp_ms,
    )
    exit_fee = kalshi_general_taker_fee(position.quantity, exit_price)
    proceeds = Decimal(position.quantity) * exit_price - exit_fee
    realized_pnl = (proceeds - position.original_cost).quantize(Decimal("0.01"))
    submitted = _order_submitted_event(
        order=order,
        action="sell",
        causality_id=authorization_event_id,
    )
    filled = _fill_recorded_event(
        order=order,
        action="sell",
        fee=exit_fee,
        causality_id=submitted.event_id,
        realized_pnl=realized_pnl,
    )
    closed = AuditEvent.create(
        event_type="PositionClosed",
        worker="execution",
        payload={
            "execution_backend": "paper_simulated",
            "market_ticker": position.market_ticker,
            "side": position.side.value,
            "quantity": position.quantity,
            "entry_price": _price(position.entry_price),
            "entry_fee": _money(position.entry_fee),
            "exit_price": _price(exit_price),
            "exit_fee": _money(exit_fee),
            "realized_pnl": _money(realized_pnl),
            "outcome": "profit" if realized_pnl >= Decimal("0") else "loss",
        },
        causality_id=filled.event_id,
        timestamp_ms=timestamp_ms,
    )
    outcome_label = "profit" if realized_pnl >= Decimal("0") else "loss"
    print(
        "order sold for "
        f"{position.market_ticker} {position.side.value.upper()} at {_price(exit_price)} "
        f"for {position.quantity} contracts: {outcome_label} "
        f"{_money(abs(realized_pnl))} (paper)",
        file=stdout,
    )
    return PaperExitResult(realized_pnl=realized_pnl, events=(submitted, filled, closed))


def _order_submitted_event(
    order: PaperExecutionOrder,
    action: str,
    causality_id: str,
) -> AuditEvent:
    return AuditEvent.create(
        event_type="OrderSubmitted",
        worker="execution",
        payload={
            "execution_backend": "paper_simulated",
            "action": action,
            "market_ticker": order.market_ticker,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": _price(order.price),
            "order_transport": "print_only",
        },
        causality_id=causality_id,
        timestamp_ms=order.timestamp_ms,
    )


def _fill_recorded_event(
    order: PaperExecutionOrder,
    action: str,
    fee: Decimal,
    causality_id: str,
    realized_pnl: Decimal | None,
) -> AuditEvent:
    payload: dict[str, object] = {
        "execution_backend": "paper_simulated",
        "action": action,
        "market_ticker": order.market_ticker,
        "side": order.side.value,
        "quantity": order.quantity,
        "price": _price(order.price),
        "fee": _money(fee),
        "order_transport": "print_only",
    }
    if realized_pnl is not None:
        payload["realized_pnl"] = _money(realized_pnl)
    return AuditEvent.create(
        event_type="FillRecorded",
        worker="execution",
        payload=payload,
        causality_id=causality_id,
        timestamp_ms=order.timestamp_ms,
    )


def _price(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))
