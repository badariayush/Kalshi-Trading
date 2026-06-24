from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kalshi_crypto.events import AuditEvent
from kalshi_crypto.models import Side
from kalshi_crypto.orderbook import NormalizedOrderBook
from kalshi_crypto.signal import SignalReady


@dataclass(frozen=True, slots=True)
class EntryRiskConfig:
    min_probability_edge: Decimal = Decimal("0.01")
    min_confidence: Decimal = Decimal("0.05")
    quantity: int = 10
    min_depth_contracts: int = 1
    max_book_age_ms: int = 2_500

    def __post_init__(self) -> None:
        if self.min_probability_edge < Decimal("0"):
            raise ValueError("min_probability_edge must be non-negative")
        if self.min_confidence < Decimal("0") or self.min_confidence > Decimal("1"):
            raise ValueError("min_confidence must be between 0 and 1")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.min_depth_contracts <= 0:
            raise ValueError("min_depth_contracts must be positive")
        if self.max_book_age_ms <= 0:
            raise ValueError("max_book_age_ms must be positive")


@dataclass(frozen=True, slots=True)
class EntryRiskDecision:
    authorized: bool
    reason: str
    side: Side | None
    quantity: int
    price: Decimal | None
    event: AuditEvent


def evaluate_entry_signal(
    *,
    signal: SignalReady,
    orderbook: NormalizedOrderBook,
    config: EntryRiskConfig,
    timestamp_ms: int,
) -> EntryRiskDecision:
    if timestamp_ms < 0:
        raise ValueError("timestamp_ms must be non-negative")
    side = Side.YES if signal.probability_yes >= Decimal("0.50") else Side.NO
    edge = abs(signal.probability_yes - Decimal("0.50"))
    quote = orderbook.ask_for(side)
    if signal.confidence < config.min_confidence:
        return _veto(signal, "confidence_below_threshold", timestamp_ms)
    if edge < config.min_probability_edge:
        return _veto(signal, "edge_below_threshold", timestamp_ms)
    if quote is None:
        return _veto(signal, "missing_executable_ask", timestamp_ms)
    if quote.age_ms > config.max_book_age_ms:
        return _veto(signal, "stale_book", timestamp_ms)
    if quote.depth < max(config.quantity, config.min_depth_contracts):
        return _veto(signal, "insufficient_depth", timestamp_ms)

    event = AuditEvent.create(
        event_type="EntryAuthorized",
        worker="risk",
        payload={
            "market_ticker": signal.market_ticker,
            "side": side.value,
            "quantity": config.quantity,
            "price": str(quote.price),
            "reason": "ok",
            "probability_yes": str(signal.probability_yes),
            "confidence": str(signal.confidence),
        },
        causality_id=signal.market_ticker,
        timestamp_ms=timestamp_ms,
    )
    return EntryRiskDecision(True, "ok", side, config.quantity, quote.price, event)


def _veto(
    signal: SignalReady,
    reason: str,
    timestamp_ms: int,
) -> EntryRiskDecision:
    event = AuditEvent.create(
        event_type="EntryVetoed",
        worker="risk",
        payload={
            "market_ticker": signal.market_ticker,
            "reason": reason,
            "probability_yes": str(signal.probability_yes),
            "confidence": str(signal.confidence),
        },
        causality_id=signal.market_ticker,
        timestamp_ms=timestamp_ms,
    )
    return EntryRiskDecision(False, reason, None, 0, None, event)
