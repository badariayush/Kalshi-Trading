from __future__ import annotations

from dataclasses import dataclass

from kalshi_crypto.events import AuditEvent


@dataclass(frozen=True, slots=True)
class RawKalshiMarketReplay:
    market_ticker: str
    series_ticker: str
    underlying: str
    strike: str
    lifecycle_status: str
    open_timestamp_ms: int
    close_timestamp_ms: int
    received_timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.market_ticker:
            raise ValueError("market_ticker is required")
        if not self.series_ticker:
            raise ValueError("series_ticker is required")
        if not self.underlying:
            raise ValueError("underlying is required")
        if not self.strike:
            raise ValueError("strike is required")
        if not self.lifecycle_status:
            raise ValueError("lifecycle_status is required")
        if self.open_timestamp_ms < 0:
            raise ValueError("open_timestamp_ms must be non-negative")
        if self.close_timestamp_ms <= self.open_timestamp_ms:
            raise ValueError("close_timestamp_ms must be after open_timestamp_ms")
        if self.received_timestamp_ms < 0:
            raise ValueError("received_timestamp_ms must be non-negative")


def lifecycle_events_from_market(
    market: RawKalshiMarketReplay,
) -> tuple[AuditEvent, ...]:
    discovered = AuditEvent.create(
        event_type="WindowDiscovered",
        worker="market_monitor",
        payload=_base_payload(market),
        causality_id="data-only-replay",
        timestamp_ms=market.received_timestamp_ms,
    )
    status = market.lifecycle_status.strip().lower()
    if status in {"open", "active"}:
        return (
            discovered,
            _child_event("WindowOpened", discovered, market, {}),
        )
    if status in {"closing", "close_soon", "closing_soon"}:
        return (
            discovered,
            _child_event(
                "WindowClosingSoon",
                discovered,
                market,
                {"time_to_close_ms": market.close_timestamp_ms - market.received_timestamp_ms},
            ),
        )
    if status in {"closed", "settled"}:
        return (
            discovered,
            _child_event("WindowClosed", discovered, market, {}),
        )
    if status in {"settlement_pending", "pending_settlement"}:
        return (
            discovered,
            _child_event("SettlementPending", discovered, market, {}),
        )
    return (discovered,)


def _child_event(
    event_type: str,
    parent: AuditEvent,
    market: RawKalshiMarketReplay,
    extra_payload: dict[str, object],
) -> AuditEvent:
    payload = _base_payload(market)
    payload.update(extra_payload)
    return AuditEvent.create(
        event_type=event_type,
        worker="market_monitor",
        payload=payload,
        causality_id=parent.event_id,
        timestamp_ms=market.received_timestamp_ms,
    )


def _base_payload(market: RawKalshiMarketReplay) -> dict[str, object]:
    return {
        "market_ticker": market.market_ticker,
        "series_ticker": market.series_ticker,
        "underlying": market.underlying,
        "strike": market.strike,
        "lifecycle_status": market.lifecycle_status,
        "open_timestamp_ms": market.open_timestamp_ms,
        "close_timestamp_ms": market.close_timestamp_ms,
        "received_timestamp_ms": market.received_timestamp_ms,
    }
