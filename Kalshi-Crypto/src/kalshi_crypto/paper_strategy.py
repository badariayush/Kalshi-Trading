from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from kalshi_crypto.arbitrage import ArbitrageConfig, evaluate_true_arbitrage
from kalshi_crypto.candles import CFBenchmarkTick, Candle, build_candles
from kalshi_crypto.config import AppConfig
from kalshi_crypto.entry_risk import EntryRiskConfig, evaluate_entry_signal
from kalshi_crypto.market_state import MarketWindow
from kalshi_crypto.models import BookQuote, Position, Side
from kalshi_crypto.money import kalshi_general_taker_fee
from kalshi_crypto.orderbook import NormalizedOrderBook
from kalshi_crypto.risk import PartialHedgeConfig, solve_partial_hedge
from kalshi_crypto.signal import SignalConfig, SignalReady, generate_signal


ENTRY_CUTOFF_BEFORE_CLOSE_MS = 600_000


@dataclass(frozen=True, slots=True)
class PaperFeedRecord:
    source: str
    message: Mapping[str, Any]
    received_timestamp_ms: int


@dataclass(frozen=True, slots=True)
class PaperMarket:
    ticker: str
    series_ticker: str
    underlying: str
    strike: Decimal
    open_time_ms: int
    close_time_ms: int
    event_ticker: str = ""

    def __post_init__(self) -> None:
        if not self.ticker or not self.series_ticker or not self.underlying:
            raise ValueError("paper market identifiers are required")
        if self.strike <= Decimal("0"):
            raise ValueError("paper market strike must be positive")
        if self.close_time_ms <= self.open_time_ms:
            raise ValueError("paper market close must be after open")


@dataclass(frozen=True, slots=True)
class PaperSimulatedOrder:
    market_ticker: str
    side: str
    price: Decimal
    quantity: int
    fee: Decimal
    coinbase_product_id: str
    coinbase_price: Decimal
    reason: str
    leg_index: int
    probability_yes: Decimal | None
    confidence: Decimal | None
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class PaperSimulatedExit:
    market_ticker: str
    side: str
    price: Decimal
    quantity: int
    fee: Decimal
    realized_pnl: Decimal
    total_fees: Decimal
    reason: str
    timestamp_ms: int


@dataclass(frozen=True, slots=True)
class PaperStrategyResult:
    orders: tuple[PaperSimulatedOrder, ...]
    exit: PaperSimulatedExit | None
    skip_reason: str | None


@dataclass(frozen=True, slots=True)
class PaperRealtimeState:
    records: tuple[PaperFeedRecord, ...]
    emitted_order_count: int
    exit_emitted: bool

    @classmethod
    def empty(cls) -> "PaperRealtimeState":
        return cls(records=(), emitted_order_count=0, exit_emitted=False)


@dataclass(frozen=True, slots=True)
class PaperRealtimeStep:
    state: PaperRealtimeState
    orders: tuple[PaperSimulatedOrder, ...]
    exit: PaperSimulatedExit | None


@dataclass(frozen=True, slots=True)
class _TimedPrice:
    timestamp_ms: int
    price: Decimal


@dataclass(frozen=True, slots=True)
class _TimedBook:
    timestamp_ms: int
    book: NormalizedOrderBook
    yes_bid: BookQuote
    no_bid: BookQuote

    def bid_for(self, side: Side) -> BookQuote:
        return self.yes_bid if side is Side.YES else self.no_bid


def advance_realtime_paper(
    *,
    state: PaperRealtimeState,
    record: PaperFeedRecord,
    market: PaperMarket,
    config: AppConfig,
) -> PaperRealtimeStep:
    if not _record_is_relevant(record, market, config):
        return PaperRealtimeStep(state=state, orders=(), exit=None)

    records = _append_sampled_record(state.records, record, config)
    if records is state.records:
        return PaperRealtimeStep(state=state, orders=(), exit=None)
    if record.source != "kalshi":
        return PaperRealtimeStep(
            state=PaperRealtimeState(
                records=records,
                emitted_order_count=state.emitted_order_count,
                exit_emitted=state.exit_emitted,
            ),
            orders=(),
            exit=None,
        )
    result = evaluate_live_paper_strategy(
        records=records,
        market=market,
        config=config,
    )
    new_orders = result.orders[state.emitted_order_count :]
    new_exit = result.exit if result.exit is not None and not state.exit_emitted else None
    new_state = PaperRealtimeState(
        records=records,
        emitted_order_count=len(result.orders),
        exit_emitted=state.exit_emitted or result.exit is not None,
    )
    return PaperRealtimeStep(state=new_state, orders=new_orders, exit=new_exit)


def evaluate_live_paper_strategy(
    *,
    records: tuple[PaperFeedRecord, ...],
    market: PaperMarket,
    config: AppConfig,
) -> PaperStrategyResult:
    prices = _coinbase_prices(records, config.paper_strategy.underlying_product_id)
    books = _kalshi_books(records, market.ticker)
    if not prices or not books:
        return PaperStrategyResult((), None, "missing_live_market_data")

    candles = _candles(prices, config)
    entry = _first_directional_entry(
        prices=prices,
        books=books,
        candles=candles,
        market=market,
        config=config,
    )
    if entry is None:
        return PaperStrategyResult((), None, "insufficient_live_signal_data")

    second_leg, simulated_exit = _management_action(
        entry=entry,
        prices=prices,
        books=books,
        config=config,
    )
    orders = (entry,) if second_leg is None else (entry, second_leg)
    return PaperStrategyResult(orders, simulated_exit, None)


def _first_directional_entry(
    *,
    prices: tuple[_TimedPrice, ...],
    books: tuple[_TimedBook, ...],
    candles: tuple[Candle, ...],
    market: PaperMarket,
    config: AppConfig,
) -> PaperSimulatedOrder | None:
    window = MarketWindow(
        market_ticker=market.ticker,
        series_ticker=market.series_ticker,
        underlying=market.underlying,
        strike=str(market.strike),
        open_timestamp_ms=market.open_time_ms,
        close_timestamp_ms=market.close_time_ms,
        status="open",
        last_event_timestamp_ms=market.open_time_ms,
    )
    signal_config = SignalConfig(
        short_ema_period=config.paper_strategy.short_ema_period,
        long_ema_period=config.paper_strategy.long_ema_period,
        max_book_age_ms=config.trade_management.max_entry_book_age_ms,
    )
    risk_config = EntryRiskConfig(
        min_probability_edge=config.paper_strategy.min_probability_edge,
        min_confidence=config.paper_strategy.min_confidence,
        quantity=config.paper_strategy.quantity,
        min_depth_contracts=config.trade_management.min_depth_contracts,
        max_book_age_ms=config.trade_management.max_entry_book_age_ms,
    )

    for timed_book in books:
        if timed_book.timestamp_ms >= market.close_time_ms:
            break
        if (
            timed_book.timestamp_ms
            >= market.close_time_ms - ENTRY_CUTOFF_BEFORE_CLOSE_MS
        ):
            break
        latest_price = _latest_timed_price_at(prices, timed_book.timestamp_ms)
        if latest_price is None:
            continue
        if (
            config.circuit_breakers.halt_new_entries_on_feed_unhealthy
            and timed_book.timestamp_ms - latest_price.timestamp_ms
            > config.circuit_breakers.data_feed_stale_ms
        ):
            continue
        available_candles = tuple(
            candle
            for candle in candles
            if candle.end_timestamp_ms <= timed_book.timestamp_ms
        )
        signal = generate_signal(
            window=window,
            candles=available_candles,
            orderbook=timed_book.book,
            config=signal_config,
            now_ms=timed_book.timestamp_ms,
        )
        if not isinstance(signal, SignalReady):
            continue
        decision = evaluate_entry_signal(
            signal=signal,
            orderbook=timed_book.book,
            config=risk_config,
            timestamp_ms=timed_book.timestamp_ms,
        )
        if not decision.authorized or decision.side is None or decision.price is None:
            continue
        fill_price = _buy_fill_price(decision.price, config)
        if fill_price is None:
            continue
        return PaperSimulatedOrder(
            market_ticker=market.ticker,
            side=decision.side.value,
            price=fill_price,
            quantity=decision.quantity,
            fee=kalshi_general_taker_fee(decision.quantity, fill_price),
            coinbase_product_id=config.paper_strategy.underlying_product_id,
            coinbase_price=latest_price.price,
            reason="directional_entry",
            leg_index=1,
            probability_yes=signal.probability_yes,
            confidence=signal.confidence,
            timestamp_ms=timed_book.timestamp_ms,
        )
    return None


def _management_action(
    *,
    entry: PaperSimulatedOrder,
    prices: tuple[_TimedPrice, ...],
    books: tuple[_TimedBook, ...],
    config: AppConfig,
) -> tuple[PaperSimulatedOrder | None, PaperSimulatedExit | None]:
    entry_side = Side(entry.side)
    position = Position(
        market_ticker=entry.market_ticker,
        side=entry_side,
        quantity=entry.quantity,
        entry_price=entry.price,
        entry_fee=entry.fee,
    )
    entry_book = _book_at_or_after(books, entry.timestamp_ms)
    previous_opposing = (
        entry_book.book.ask_for(entry_side.opposite) if entry_book is not None else None
    )
    if previous_opposing is None:
        return None, None

    arb_config = ArbitrageConfig(
        min_arb_margin=config.trade_management.min_arb_margin,
        slippage_buffer=config.trade_management.slippage_buffer,
        max_book_age_ms=config.trade_management.max_true_arb_book_age_ms,
    )
    hedge_config = _partial_hedge_config(position, config)

    for timed_book in books:
        if timed_book.timestamp_ms <= entry.timestamp_ms:
            continue
        opposing_quote = timed_book.book.ask_for(entry_side.opposite)
        if opposing_quote is None:
            continue
        simulated_exit = _take_profit_exit(
            entry=entry,
            timed_book=timed_book,
            config=config,
        )
        if simulated_exit is not None:
            return None, simulated_exit
        arb = evaluate_true_arbitrage(position, opposing_quote, arb_config)
        if arb.authorized:
            second_order = _second_order(
                entry=entry,
                prices=prices,
                timed_book=timed_book,
                price=opposing_quote.price,
                quantity=arb.quantity,
                reason="true_arbitrage",
                config=config,
            )
            if second_order is not None:
                return second_order, None

        trigger_crossed = (
            previous_opposing.price
            < config.trade_management.partial_hedge_opposing_ask_trigger
            <= opposing_quote.price
        )
        previous_opposing = opposing_quote
        if not config.trade_management.partial_hedge_enabled or not trigger_crossed:
            continue
        hedge = solve_partial_hedge(position, opposing_quote, hedge_config)
        if hedge.authorized:
            second_order = _second_order(
                entry=entry,
                prices=prices,
                timed_book=timed_book,
                price=opposing_quote.price,
                quantity=hedge.quantity,
                reason="partial_hedge",
                config=config,
            )
            if second_order is not None:
                return second_order, None
    return None, None


def _partial_hedge_config(
    position: Position,
    config: AppConfig,
) -> PartialHedgeConfig:
    trade = config.trade_management
    return PartialHedgeConfig(
        opposing_ask_trigger=trade.partial_hedge_opposing_ask_trigger,
        max_loss=trade.partial_hedge_max_loss_for_original_risk(
            position.original_cost
        ),
        max_contracts_pct_of_original_size=(
            trade.partial_hedge_max_contracts_pct_of_original_size
        ),
        slippage_buffer=trade.slippage_buffer,
        max_book_age_ms=trade.max_partial_hedge_book_age_ms,
    )


def _second_order(
    *,
    entry: PaperSimulatedOrder,
    prices: tuple[_TimedPrice, ...],
    timed_book: _TimedBook,
    price: Decimal,
    quantity: int,
    reason: str,
    config: AppConfig,
) -> PaperSimulatedOrder | None:
    coinbase_price = _latest_price_at(prices, timed_book.timestamp_ms)
    if coinbase_price is None:
        coinbase_price = entry.coinbase_price
    fill_price = _buy_fill_price(price, config)
    if fill_price is None:
        return None
    return PaperSimulatedOrder(
        market_ticker=entry.market_ticker,
        side=Side(entry.side).opposite.value,
        price=fill_price,
        quantity=quantity,
        fee=kalshi_general_taker_fee(quantity, fill_price),
        coinbase_product_id=config.paper_strategy.underlying_product_id,
        coinbase_price=coinbase_price,
        reason=reason,
        leg_index=2,
        probability_yes=entry.probability_yes,
        confidence=entry.confidence,
        timestamp_ms=timed_book.timestamp_ms,
    )


def _take_profit_exit(
    *,
    entry: PaperSimulatedOrder,
    timed_book: _TimedBook,
    config: AppConfig,
) -> PaperSimulatedExit | None:
    side = Side(entry.side)
    bid = timed_book.bid_for(side)
    if bid.depth < entry.quantity:
        return None
    target = entry.price * (Decimal("1") + config.trade_management.take_profit_pct)
    if bid.price < target:
        return None
    fill_price = bid.price - config.trade_management.slippage_buffer
    if fill_price <= Decimal("0") or fill_price >= Decimal("1"):
        return None
    exit_fee = kalshi_general_taker_fee(entry.quantity, fill_price)
    total_fees = entry.fee + exit_fee
    realized_pnl = (
        (fill_price - entry.price) * Decimal(entry.quantity) - total_fees
    )
    return PaperSimulatedExit(
        market_ticker=entry.market_ticker,
        side=entry.side,
        price=fill_price,
        quantity=entry.quantity,
        fee=exit_fee,
        realized_pnl=realized_pnl,
        total_fees=total_fees,
        reason="take_profit",
        timestamp_ms=timed_book.timestamp_ms,
    )


def _buy_fill_price(ask: Decimal, config: AppConfig) -> Decimal | None:
    fill_price = ask + config.trade_management.slippage_buffer
    if fill_price <= Decimal("0") or fill_price >= Decimal("1"):
        return None
    return fill_price


def _candles(
    prices: tuple[_TimedPrice, ...],
    config: AppConfig,
) -> tuple[Candle, ...]:
    ticks = tuple(
        CFBenchmarkTick(
            index_ticker=config.paper_strategy.underlying_product_id,
            price=price.price,
            source_timestamp_ms=price.timestamp_ms,
            received_timestamp_ms=price.timestamp_ms,
        )
        for price in prices
    )
    return build_candles(
        ticks,
        interval_ms=config.paper_strategy.candle_interval_seconds * 1000,
    )


def _coinbase_prices(
    records: tuple[PaperFeedRecord, ...],
    product_id: str,
) -> tuple[_TimedPrice, ...]:
    prices: list[_TimedPrice] = []
    for record in records:
        if record.source != "coinbase":
            continue
        timestamp_ms = _message_timestamp_ms(
            record.message, record.received_timestamp_ms
        )
        events = record.message.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, Mapping):
                continue
            tickers = event.get("tickers")
            if not isinstance(tickers, list):
                continue
            for ticker in tickers:
                if not isinstance(ticker, Mapping) or ticker.get("product_id") != product_id:
                    continue
                price = _decimal(ticker.get("price"))
                if price is not None and price > Decimal("0"):
                    prices.append(_TimedPrice(timestamp_ms, price))
    return tuple(sorted(prices, key=lambda item: item.timestamp_ms))


def _kalshi_books(
    records: tuple[PaperFeedRecord, ...],
    market_ticker: str,
) -> tuple[_TimedBook, ...]:
    books: list[_TimedBook] = []
    for record in records:
        if record.source != "kalshi" or record.message.get("type") != "ticker":
            continue
        payload = record.message.get("msg")
        if not isinstance(payload, Mapping) or payload.get("market_ticker") != market_ticker:
            continue
        yes_ask = _decimal(payload.get("yes_ask_dollars"))
        yes_bid = _decimal(payload.get("yes_bid_dollars"))
        yes_ask_depth = _whole_depth(payload.get("yes_ask_size_fp"))
        yes_bid_depth = _whole_depth(payload.get("yes_bid_size_fp"))
        if yes_ask is None or yes_bid is None or yes_ask_depth < 1 or yes_bid_depth < 1:
            continue
        no_ask = Decimal("1") - yes_bid
        no_bid = Decimal("1") - yes_ask
        timestamp_ms = _message_timestamp_ms(payload, record.received_timestamp_ms)
        try:
            yes_bid_quote = BookQuote(yes_bid, yes_bid_depth, 0)
            no_bid_quote = BookQuote(no_bid, yes_ask_depth, 0)
            book = NormalizedOrderBook(
                market_ticker=market_ticker,
                yes_ask=BookQuote(yes_ask, yes_ask_depth, 0),
                no_ask=BookQuote(no_ask, yes_bid_depth, 0),
                source_timestamp_ms=timestamp_ms,
                received_timestamp_ms=timestamp_ms,
            )
        except ValueError:
            continue
        books.append(
            _TimedBook(
                timestamp_ms=timestamp_ms,
                book=book,
                yes_bid=yes_bid_quote,
                no_bid=no_bid_quote,
            )
        )
    return tuple(sorted(books, key=lambda item: item.timestamp_ms))


def _latest_price_at(
    prices: tuple[_TimedPrice, ...],
    timestamp_ms: int,
) -> Decimal | None:
    latest: Decimal | None = None
    for price in prices:
        if price.timestamp_ms > timestamp_ms:
            break
        latest = price.price
    return latest


def _latest_timed_price_at(
    prices: tuple[_TimedPrice, ...],
    timestamp_ms: int,
) -> _TimedPrice | None:
    latest: _TimedPrice | None = None
    for price in prices:
        if price.timestamp_ms > timestamp_ms:
            break
        latest = price
    return latest


def _book_at_or_after(
    books: tuple[_TimedBook, ...],
    timestamp_ms: int,
) -> _TimedBook | None:
    for book in books:
        if book.timestamp_ms >= timestamp_ms:
            return book
    return None


def _record_is_relevant(
    record: PaperFeedRecord,
    market: PaperMarket,
    config: AppConfig,
) -> bool:
    if record.source == "coinbase":
        return bool(
            _coinbase_prices(
                (record,),
                config.paper_strategy.underlying_product_id,
            )
        )
    if record.source == "kalshi":
        return bool(_kalshi_books((record,), market.ticker))
    return False


def _append_sampled_record(
    records: tuple[PaperFeedRecord, ...],
    record: PaperFeedRecord,
    config: AppConfig,
) -> tuple[PaperFeedRecord, ...]:
    if record.source != "coinbase":
        return (*records, record)
    current = _coinbase_prices(
        (record,),
        config.paper_strategy.underlying_product_id,
    )
    if not current:
        return records
    current_second = current[-1].timestamp_ms // 1000
    for previous_record in reversed(records):
        if previous_record.source != "coinbase":
            continue
        previous = _coinbase_prices(
            (previous_record,),
            config.paper_strategy.underlying_product_id,
        )
        if previous and previous[-1].timestamp_ms // 1000 == current_second:
            return records
        break
    return (*records, record)


def _message_timestamp_ms(message: Mapping[str, Any], fallback: int) -> int:
    for key in ("ts_ms", "timestamp_ms"):
        value = message.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    timestamp = message.get("timestamp") or message.get("time") or message.get("ts")
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return int(parsed.timestamp() * 1000)
        except ValueError:
            pass
    return fallback


def _whole_depth(value: object) -> int:
    parsed = _decimal(value)
    if parsed is None or parsed <= Decimal("0"):
        return 0
    return int(parsed)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
