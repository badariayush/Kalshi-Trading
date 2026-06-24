from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TextIO

from kalshi_crypto.candles import Candle
from kalshi_crypto.config import AppConfig
from kalshi_crypto.entry_risk import EntryRiskConfig, evaluate_entry_signal
from kalshi_crypto.events import AuditEvent
from kalshi_crypto.execution import SafetyError
from kalshi_crypto.live_inputs import LivePaperInputs, live_paper_inputs_from_file
from kalshi_crypto.market_lifecycle import RawKalshiMarketReplay, lifecycle_events_from_market
from kalshi_crypto.market_state import MarketWindowRegistry
from kalshi_crypto.models import Side
from kalshi_crypto.orderbook import NormalizedOrderBook, PriceLevel, normalize_kalshi_bid_books
from kalshi_crypto.paper_execution import (
    PaperExecutionOrder,
    execute_paper_entry,
    execute_paper_exit,
)
from kalshi_crypto.report import ReportSummary, load_report
from kalshi_crypto.signal import SignalConfig, SignalReady, generate_signal
from kalshi_crypto.storage import SQLiteAuditStore


@dataclass(frozen=True, slots=True)
class PaperRunSummary:
    audit_events: int
    report: ReportSummary


def run_paper_algorithm(
    *,
    config: AppConfig,
    audit_db: str | Path,
    max_seconds: int,
    stdout: TextIO,
    live_input_file: str | Path | None = None,
) -> PaperRunSummary:
    if max_seconds <= 0:
        raise ValueError("max_seconds must be positive")
    if config.order_api.allow_order_submission:
        raise SafetyError("paper algorithm requires order submission to remain disabled")

    store = SQLiteAuditStore(audit_db)
    events = _paper_events(
        config=config,
        max_seconds=max_seconds,
        stdout=stdout,
        live_input_file=live_input_file,
    )
    for event in events:
        store.append(event)
    report = load_report(audit_db)
    return PaperRunSummary(audit_events=len(events), report=report)


def _paper_events(
    *,
    config: AppConfig,
    max_seconds: int,
    stdout: TextIO,
    live_input_file: str | Path | None,
) -> tuple[AuditEvent, ...]:
    del max_seconds
    if live_input_file is not None:
        return _paper_events_from_live_inputs(
            config=config,
            live_inputs=live_paper_inputs_from_file(live_input_file),
            stdout=stdout,
        )

    timestamp_ms = 1_000
    market = RawKalshiMarketReplay(
        market_ticker="KXBTCD-PAPER",
        series_ticker="KXBTC15M",
        underlying="BTC",
        strike="100000",
        lifecycle_status="open",
        open_timestamp_ms=1_000,
        close_timestamp_ms=901_000,
        received_timestamp_ms=timestamp_ms,
    )
    orderbook = normalize_kalshi_bid_books(
        market_ticker="KXBTCD-PAPER",
        yes_bids=(PriceLevel(price=Decimal("0.40"), quantity=50),),
        no_bids=(PriceLevel(price=Decimal("0.53"), quantity=50),),
        source_timestamp_ms=119_900,
        received_timestamp_ms=120_000,
    )
    return _worker_chain_events(
        config=config,
        market=market,
        orderbook=orderbook,
        candles=_paper_candles(),
        candle_event_type="CFBenchmarkCandleClosed",
        network="simulated_fixture",
        raw_messages=None,
        stdout=stdout,
    )


def _paper_events_from_live_inputs(
    *,
    config: AppConfig,
    live_inputs: LivePaperInputs,
    stdout: TextIO,
) -> tuple[AuditEvent, ...]:
    return _worker_chain_events(
        config=config,
        market=live_inputs.market,
        orderbook=live_inputs.orderbook,
        candles=live_inputs.candles,
        candle_event_type="CoinbaseCandleClosed",
        network="live_message_file",
        raw_messages=live_inputs.raw_messages,
        stdout=stdout,
    )


def _worker_chain_events(
    *,
    config: AppConfig,
    market: RawKalshiMarketReplay,
    orderbook: NormalizedOrderBook,
    candles: tuple[Candle, ...],
    candle_event_type: str,
    network: str,
    raw_messages: int | None,
    stdout: TextIO,
) -> tuple[AuditEvent, ...]:
    events: list[AuditEvent] = list(lifecycle_events_from_market(market))
    registry = MarketWindowRegistry.empty().apply_all(events)
    window = registry.window(orderbook.market_ticker)
    if window is None:
        raise RuntimeError("paper market window was not projected")
    orderbook_event = AuditEvent.create(
        event_type="OrderBookSnapshotNormalized",
        worker="market_monitor",
        payload={
            "market_ticker": orderbook.market_ticker,
            "source": "kalshi_websocket" if network == "live_message_file" else "paper_fixture",
            "yes_ask_price": str(orderbook.yes_ask.price) if orderbook.yes_ask else None,
            "yes_ask_depth": orderbook.yes_ask.depth if orderbook.yes_ask else 0,
            "no_ask_price": str(orderbook.no_ask.price) if orderbook.no_ask else None,
            "no_ask_depth": orderbook.no_ask.depth if orderbook.no_ask else 0,
            "source_timestamp_ms": orderbook.source_timestamp_ms,
            "received_timestamp_ms": orderbook.received_timestamp_ms,
        },
        causality_id=events[-1].event_id,
        timestamp_ms=orderbook.received_timestamp_ms,
    )
    events.append(orderbook_event)

    candle_event = _candle_event(candles[-1], orderbook_event.event_id, candle_event_type)
    events.append(candle_event)
    now_ms = orderbook.received_timestamp_ms + 1_000
    signal = generate_signal(
        window=window,
        candles=candles,
        orderbook=orderbook,
        config=SignalConfig(short_ema_period=2, long_ema_period=3),
        now_ms=now_ms,
    )
    if not isinstance(signal, SignalReady):
        raise RuntimeError(f"paper signal skipped: {signal.reason}")
    signal_event = _signal_event(signal, candle_event.event_id, now_ms)
    events.append(signal_event)

    risk_decision = evaluate_entry_signal(
        signal=signal,
        orderbook=orderbook,
        config=EntryRiskConfig(
            quantity=10,
            min_probability_edge=Decimal("0.005"),
            min_confidence=Decimal("0.01"),
            min_depth_contracts=config.trade_management.min_depth_contracts,
            max_book_age_ms=config.trade_management.max_entry_book_age_ms,
        ),
        timestamp_ms=now_ms + 1_000,
    )
    events.append(risk_decision.event)
    if not risk_decision.authorized or risk_decision.side is None or risk_decision.price is None:
        raise RuntimeError(f"paper entry was vetoed: {risk_decision.reason}")

    entry = execute_paper_entry(
        PaperExecutionOrder(
            market_ticker=signal.market_ticker,
            side=risk_decision.side,
            quantity=risk_decision.quantity,
            price=risk_decision.price,
            authorization_event_id=risk_decision.event.event_id,
            timestamp_ms=now_ms + 2_000,
        ),
        stdout=stdout,
    )
    events.extend(entry.events)

    exit_price = Decimal("0.64") if risk_decision.side is Side.YES else Decimal("0.64")
    exit_authorized = AuditEvent.create(
        event_type="ExitAuthorized",
        worker="risk",
        payload={
            "market_ticker": signal.market_ticker,
            "side": risk_decision.side.value,
            "quantity": risk_decision.quantity,
            "price": str(exit_price),
            "reason": "take_profit",
        },
        causality_id=entry.events[-1].event_id,
        timestamp_ms=now_ms + 120_000,
    )
    events.append(exit_authorized)
    exit_result = execute_paper_exit(
        position=entry.position,
        exit_price=exit_price,
        authorization_event_id=exit_authorized.event_id,
        timestamp_ms=now_ms + 121_000,
        stdout=stdout,
    )
    events.extend(exit_result.events)
    events.append(
        AuditEvent.create(
            event_type="PaperRunCompleted",
            worker="orchestrator",
            payload={
                "mode": config.runtime.mode.value,
                "network": network,
                "order_submission": "disabled",
                "audit_events": len(events) + 1,
                "raw_messages": raw_messages,
            },
            causality_id=exit_result.events[-1].event_id,
            timestamp_ms=now_ms + 122_000,
        )
    )
    return tuple(events)


def _paper_candles() -> tuple[Candle, ...]:
    return (
        _candle("99000", 0),
        _candle("100500", 60_000),
        _candle("102000", 120_000),
    )


def _candle(close: str, start_timestamp_ms: int) -> Candle:
    close_price = Decimal(close)
    return Candle(
        index_ticker="BRTI",
        start_timestamp_ms=start_timestamp_ms,
        end_timestamp_ms=start_timestamp_ms + 60_000,
        open_price=close_price - Decimal("100"),
        high_price=close_price + Decimal("50"),
        low_price=close_price - Decimal("150"),
        close_price=close_price,
        tick_count=3,
        source_timestamp_ms=start_timestamp_ms + 59_000,
        received_timestamp_ms=start_timestamp_ms + 59_100,
    )


def _candle_event(
    candle: Candle,
    causality_id: str,
    event_type: str,
) -> AuditEvent:
    return AuditEvent.create(
        event_type=event_type,
        worker="signal",
        payload={
            "index_ticker": candle.index_ticker,
            "source": "coinbase_websocket"
            if event_type == "CoinbaseCandleClosed"
            else "cf_benchmark",
            "start_timestamp_ms": candle.start_timestamp_ms,
            "end_timestamp_ms": candle.end_timestamp_ms,
            "close_price": str(candle.close_price),
            "tick_count": candle.tick_count,
        },
        causality_id=causality_id,
        timestamp_ms=candle.received_timestamp_ms,
    )


def _signal_event(signal: SignalReady, causality_id: str, timestamp_ms: int) -> AuditEvent:
    return AuditEvent.create(
        event_type="SignalReady",
        worker="signal",
        payload={
            "market_ticker": signal.market_ticker,
            "probability_yes": str(signal.probability_yes),
            "confidence": str(signal.confidence),
            "reason": signal.reason,
            "latest_close": str(signal.features.latest_close),
        },
        causality_id=causality_id,
        timestamp_ms=timestamp_ms,
    )
