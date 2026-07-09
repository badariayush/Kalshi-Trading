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
    market_trend: str
    structure_break: str | None
    liquidity_sweep: str | None
    vwap_proxy: Decimal
    distance_from_vwap: Decimal
    volume_profile_poc_proxy: Decimal
    distance_from_volume_poc: Decimal
    order_flow_delta_proxy: Decimal
    kalshi_yes_ask: Decimal | None
    kalshi_no_ask: Decimal | None
    kalshi_mid_yes_probability: Decimal | None
    latest_source_timestamp_ms: int
    latest_received_timestamp_ms: int
    unavailable_context: tuple[str, ...]


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
    vwap_proxy = _vwap_proxy(candles)
    volume_profile_poc_proxy = _volume_profile_poc_proxy(candles)

    return SignalFeatures(
        latest_close=latest_close,
        short_ema=short_ema,
        long_ema=long_ema,
        ema_spread=_safe_ratio(short_ema - long_ema, latest_close),
        momentum=momentum,
        realized_volatility=_realized_volatility(closes[-config.long_ema_period :]),
        distance_from_strike=_safe_ratio(latest_close - strike, strike),
        market_trend=_market_trend(candles),
        structure_break=_structure_break(candles),
        liquidity_sweep=_liquidity_sweep(candles),
        vwap_proxy=vwap_proxy,
        distance_from_vwap=_safe_ratio(latest_close - vwap_proxy, vwap_proxy),
        volume_profile_poc_proxy=volume_profile_poc_proxy,
        distance_from_volume_poc=_safe_ratio(
            latest_close - volume_profile_poc_proxy,
            volume_profile_poc_proxy,
        ),
        order_flow_delta_proxy=_order_flow_delta_proxy(candles),
        kalshi_yes_ask=yes_quote.price if yes_quote is not None else None,
        kalshi_no_ask=no_quote.price if no_quote is not None else None,
        kalshi_mid_yes_probability=_kalshi_mid_yes_probability(orderbook),
        latest_source_timestamp_ms=latest.source_timestamp_ms,
        latest_received_timestamp_ms=latest.received_timestamp_ms,
        unavailable_context=(
            "dom_depth_ladder",
            "footprint_chart",
            "open_interest",
            "funding",
            "liquidation_map",
            "on_chain_metrics",
        ),
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


def _market_trend(candles: tuple[Candle, ...]) -> str:
    if len(candles) < 2:
        return "range"
    higher_close = candles[-1].close_price > candles[0].close_price
    lower_close = candles[-1].close_price < candles[0].close_price
    higher_high = candles[-1].high_price >= max(candle.high_price for candle in candles[:-1])
    lower_low = candles[-1].low_price <= min(candle.low_price for candle in candles[:-1])
    if higher_close and higher_high:
        return "bullish"
    if lower_close and lower_low:
        return "bearish"
    return "range"


def _structure_break(candles: tuple[Candle, ...]) -> str | None:
    if len(candles) < 2:
        return None
    previous = candles[:-1]
    latest = candles[-1]
    if latest.close_price > max(candle.high_price for candle in previous):
        return "bullish_bos"
    if latest.close_price < min(candle.low_price for candle in previous):
        return "bearish_bos"
    return None


def _liquidity_sweep(candles: tuple[Candle, ...]) -> str | None:
    if len(candles) < 2:
        return None
    previous = candles[:-1]
    latest = candles[-1]
    previous_high = max(candle.high_price for candle in previous)
    previous_low = min(candle.low_price for candle in previous)
    if latest.low_price < previous_low and latest.close_price > previous_low:
        return "sell_side_liquidity_sweep"
    if latest.high_price > previous_high and latest.close_price < previous_high:
        return "buy_side_liquidity_sweep"
    return None


def _vwap_proxy(candles: tuple[Candle, ...]) -> Decimal:
    weighted_sum = _ZERO
    total_weight = 0
    for candle in candles:
        typical_price = (
            candle.high_price + candle.low_price + candle.close_price
        ) / Decimal("3")
        weighted_sum += typical_price * Decimal(candle.tick_count)
        total_weight += candle.tick_count
    if total_weight <= 0:
        return candles[-1].close_price
    return weighted_sum / Decimal(total_weight)


def _volume_profile_poc_proxy(candles: tuple[Candle, ...]) -> Decimal:
    highest_activity = max(candle.tick_count for candle in candles)
    active_candles = tuple(candle for candle in candles if candle.tick_count == highest_activity)
    return active_candles[-1].close_price


def _order_flow_delta_proxy(candles: tuple[Candle, ...]) -> Decimal:
    signed_activity = _ZERO
    total_activity = 0
    for candle in candles:
        total_activity += candle.tick_count
        if candle.close_price > candle.open_price:
            signed_activity += Decimal(candle.tick_count)
        elif candle.close_price < candle.open_price:
            signed_activity -= Decimal(candle.tick_count)
    if total_activity <= 0:
        return _ZERO
    return signed_activity / Decimal(total_activity)


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
        + features.distance_from_vwap * Decimal("1")
        + features.distance_from_volume_poc * Decimal("0.5")
        + features.order_flow_delta_proxy * Decimal("0.05")
        + _structure_score(features) * Decimal("0.02")
    )
    if features.kalshi_mid_yes_probability is not None:
        directional_score += (features.kalshi_mid_yes_probability - _HALF) * Decimal("0.75")
    adjustment = _clamp(
        directional_score,
        -config.max_probability_adjustment,
        config.max_probability_adjustment,
    )
    return _clamp(_HALF + adjustment, _ZERO, _ONE)


def _structure_score(features: SignalFeatures) -> Decimal:
    score = _ZERO
    if features.market_trend == "bullish":
        score += Decimal("1")
    elif features.market_trend == "bearish":
        score -= Decimal("1")
    if features.structure_break == "bullish_bos":
        score += Decimal("1")
    elif features.structure_break == "bearish_bos":
        score -= Decimal("1")
    if features.liquidity_sweep == "sell_side_liquidity_sweep":
        score += Decimal("0.5")
    elif features.liquidity_sweep == "buy_side_liquidity_sweep":
        score -= Decimal("0.5")
    return score


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
