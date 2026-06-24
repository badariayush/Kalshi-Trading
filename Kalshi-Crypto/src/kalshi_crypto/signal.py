from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kalshi_crypto.candles import Candle
from kalshi_crypto.market_state import MarketWindow
from kalshi_crypto.models import Side
from kalshi_crypto.orderbook import NormalizedOrderBook

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HALF = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class SignalConfig:
    short_ema_period: int = 3
    long_ema_period: int = 8
    max_book_age_ms: int = 2_500
    max_probability_adjustment: Decimal = Decimal("0.20")

    def __post_init__(self) -> None:
        if self.short_ema_period <= 0:
            raise ValueError("short_ema_period must be positive")
        if self.long_ema_period < self.short_ema_period:
            raise ValueError("long_ema_period must be at least short_ema_period")
        if self.max_book_age_ms < 0:
            raise ValueError("max_book_age_ms must be non-negative")
        if self.max_probability_adjustment <= _ZERO or self.max_probability_adjustment > _HALF:
            raise ValueError("max_probability_adjustment must be in (0, 0.5]")


@dataclass(frozen=True, slots=True)
class SignalFeatures:
    latest_close: Decimal
    short_ema: Decimal
    long_ema: Decimal
    ema_spread: Decimal
    momentum: Decimal
    realized_volatility: Decimal
    distance_from_strike: Decimal
    kalshi_yes_ask: Decimal | None
    kalshi_no_ask: Decimal | None
    kalshi_mid_yes_probability: Decimal | None
    latest_source_timestamp_ms: int
    latest_received_timestamp_ms: int


@dataclass(frozen=True, slots=True)
class SignalReady:
    market_ticker: str
    probability_yes: Decimal
    confidence: Decimal
    features: SignalFeatures
    reason: str


@dataclass(frozen=True, slots=True)
class SignalSkipped:
    market_ticker: str
    reason: str


SignalResult = SignalReady | SignalSkipped


def generate_signal(
    *,
    window: MarketWindow,
    candles: tuple[Candle, ...],
    orderbook: NormalizedOrderBook,
    config: SignalConfig,
    now_ms: int,
) -> SignalResult:
    if now_ms < 0:
        raise ValueError("now_ms must be non-negative")
    if orderbook.market_ticker != window.market_ticker:
        raise ValueError("orderbook market_ticker must match window")
    if now_ms - orderbook.received_timestamp_ms > config.max_book_age_ms:
        return SignalSkipped(market_ticker=window.market_ticker, reason="stale_orderbook")
    if len(candles) < config.long_ema_period:
        return SignalSkipped(market_ticker=window.market_ticker, reason="insufficient_candles")

    features = _features(window, candles, orderbook, config)
    probability_yes = _probability_from_features(features, config)
    confidence = _confidence_from_features(features)
    return SignalReady(
        market_ticker=window.market_ticker,
        probability_yes=probability_yes,
        confidence=confidence,
        features=features,
        reason="feature_snapshot",
    )


def _features(
    window: MarketWindow,
    candles: tuple[Candle, ...],
    orderbook: NormalizedOrderBook,
    config: SignalConfig,
) -> SignalFeatures:
    closes = tuple(candle.close_price for candle in candles)
    latest = candles[-1]
    short_ema = _ema(closes[-config.short_ema_period :], config.short_ema_period)
    long_ema = _ema(closes[-config.long_ema_period :], config.long_ema_period)
    latest_close = latest.close_price
    momentum = _safe_ratio(latest_close - candles[-2].close_price, candles[-2].close_price)
    strike = Decimal(window.strike)
    yes_quote = orderbook.ask_for(Side.YES)
    no_quote = orderbook.ask_for(Side.NO)

    return SignalFeatures(
        latest_close=latest_close,
        short_ema=short_ema,
        long_ema=long_ema,
        ema_spread=_safe_ratio(short_ema - long_ema, latest_close),
        momentum=momentum,
        realized_volatility=_realized_volatility(closes[-config.long_ema_period :]),
        distance_from_strike=_safe_ratio(latest_close - strike, strike),
        kalshi_yes_ask=yes_quote.price if yes_quote is not None else None,
        kalshi_no_ask=no_quote.price if no_quote is not None else None,
        kalshi_mid_yes_probability=_kalshi_mid_yes_probability(orderbook),
        latest_source_timestamp_ms=latest.source_timestamp_ms,
        latest_received_timestamp_ms=latest.received_timestamp_ms,
    )


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if not values:
        raise ValueError("values are required")
    alpha = Decimal("2") / Decimal(period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * alpha + current * (_ONE - alpha)
    return current


def _realized_volatility(closes: tuple[Decimal, ...]) -> Decimal:
    returns = tuple(
        abs(_safe_ratio(closes[index] - closes[index - 1], closes[index - 1]))
        for index in range(1, len(closes))
    )
    if not returns:
        return _ZERO
    return sum(returns, _ZERO) / Decimal(len(returns))


def _kalshi_mid_yes_probability(orderbook: NormalizedOrderBook) -> Decimal | None:
    yes_quote = orderbook.ask_for(Side.YES)
    no_quote = orderbook.ask_for(Side.NO)
    if yes_quote is None or no_quote is None:
        return None
    implied_no_bid = _ONE - yes_quote.price
    implied_yes_bid = _ONE - no_quote.price
    return (implied_yes_bid + yes_quote.price) / Decimal("2")


def _probability_from_features(
    features: SignalFeatures,
    config: SignalConfig,
) -> Decimal:
    directional_score = (
        features.ema_spread * Decimal("3")
        + features.momentum * Decimal("4")
        + features.distance_from_strike * Decimal("1.5")
    )
    if features.kalshi_mid_yes_probability is not None:
        directional_score += (features.kalshi_mid_yes_probability - _HALF) * Decimal("0.75")
    adjustment = _clamp(
        directional_score,
        -config.max_probability_adjustment,
        config.max_probability_adjustment,
    )
    return _clamp(_HALF + adjustment, _ZERO, _ONE)


def _confidence_from_features(features: SignalFeatures) -> Decimal:
    directional_strength = abs(features.ema_spread) + abs(features.momentum)
    volatility_penalty = min(features.realized_volatility * Decimal("2"), Decimal("0.5"))
    liquidity_bonus = _liquidity_bonus(features)
    return _clamp(directional_strength * Decimal("12") + liquidity_bonus - volatility_penalty, _ZERO, _ONE)


def _liquidity_bonus(features: SignalFeatures) -> Decimal:
    if features.kalshi_yes_ask is None or features.kalshi_no_ask is None:
        return _ZERO
    spread_cost = features.kalshi_yes_ask + features.kalshi_no_ask - _ONE
    return _clamp(Decimal("0.25") - spread_cost, _ZERO, Decimal("0.25"))


def _safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == _ZERO:
        return _ZERO
    return numerator / denominator


def _clamp(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    return max(minimum, min(maximum, value))
