from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .config import AppConfig
from .models import OrderbookState, SignalCandidate, TradeEvent
from .state import MarketState


@dataclass(slots=True)
class SignalDecision:
    candidate: SignalCandidate | None
    rejection_reason: str | None = None


class SignalEngine:
    def __init__(self, config: AppConfig):
        self.config = config

    def process_trade(
        self,
        trade: TradeEvent,
        orderbook: OrderbookState,
        state: MarketState,
    ) -> SignalDecision:
        strategy = self.config.strategy
        if trade.category not in strategy.allowed_categories:
            return SignalDecision(candidate=None, rejection_reason="category_not_allowed")
        if trade.market_volume < strategy.min_market_volume.get(trade.category, float("inf")):
            return SignalDecision(candidate=None, rejection_reason="volume_below_threshold")
        seconds_to_close = int((trade.close_time - trade.timestamp).total_seconds())
        if seconds_to_close < strategy.min_time_to_resolution_seconds.get(trade.category, 0):
            return SignalDecision(candidate=None, rejection_reason="too_close_to_resolution")
        if not strategy.price_min <= trade.price <= strategy.price_max:
            return SignalDecision(candidate=None, rejection_reason="price_out_of_range")
        if trade.side == "NO" and trade.price < strategy.min_no_price:
            return SignalDecision(candidate=None, rejection_reason="no_price_too_low")
        if trade.size_ratio < strategy.min_trade_size_ratio:
            return SignalDecision(candidate=None, rejection_reason="trade_size_too_small")
        if orderbook.updated_at is None:
            return SignalDecision(candidate=None, rejection_reason="missing_orderbook")
        if orderbook.updated_at < trade.timestamp - timedelta(
            seconds=self.config.environment.max_orderbook_staleness_seconds
        ):
            return SignalDecision(candidate=None, rejection_reason="stale_orderbook")

        cluster_count = state.ingest_trade(trade, strategy.cluster_window_seconds)
        if cluster_count < strategy.cluster_baseline_count:
            return SignalDecision(candidate=None, rejection_reason="cluster_below_threshold")

        signal_strength = self._signal_strength(trade.size_ratio, cluster_count)
        if (
            signal_strength == "moderate"
            and strategy.block_moderate_price_min <= trade.price <= strategy.block_moderate_price_max
        ):
            return SignalDecision(candidate=None, rejection_reason="moderate_mid_price_block")
        confidence = "tradable"
        candidate = SignalCandidate(
            market_ticker=trade.market_ticker,
            side=trade.side,
            signal_strength=signal_strength,
            confidence_level=confidence,
            price=trade.price,
            size_ratio=trade.size_ratio,
            cluster_count=cluster_count,
            timestamp=trade.timestamp,
            reason="qualified_signal",
            metadata={
                "category": trade.category,
                "market_volume": trade.market_volume,
            },
        )
        return SignalDecision(candidate=candidate)

    def _signal_strength(self, size_ratio: float, cluster_count: int) -> str:
        strategy = self.config.strategy
        elevated_factors = 0
        if size_ratio >= strategy.elevated_trade_size_ratio:
            elevated_factors += 1
        if cluster_count >= strategy.cluster_elevated_count:
            elevated_factors += 1
        if size_ratio >= strategy.strong_trade_size_ratio or cluster_count >= strategy.cluster_strong_count:
            elevated_factors += 1
        if elevated_factors >= 3:
            return "very_strong"
        if elevated_factors >= 2:
            return "strong"
        return "moderate"
