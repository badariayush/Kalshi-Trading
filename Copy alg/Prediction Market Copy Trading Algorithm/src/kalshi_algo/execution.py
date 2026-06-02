from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig
from .models import ActionEvent, SignalCandidate, VirtualPosition, utc_now


@dataclass(slots=True)
class EntryDecision:
    accepted: bool
    position: VirtualPosition | None
    event: ActionEvent


class VirtualExecutionEngine:
    def __init__(self, config: AppConfig):
        self.config = config

    def determine_position_size(self, candidate: SignalCandidate, bankroll: float | None = None) -> int:
        if candidate.price <= 0:
            return 0
        probability = {
            "moderate": self.config.execution.moderate_p,
            "strong": self.config.execution.strong_p,
            "very_strong": self.config.execution.very_strong_p,
        }[candidate.signal_strength]
        b = (1 - candidate.price) / candidate.price
        q = 1 - probability
        kelly = ((b * probability) - q) / b if b > 0 else 0.0
        if kelly <= 0:
            return 0
        available_bankroll = self.config.portfolio.starting_cash if bankroll is None else bankroll
        allocation = max(0.0, available_bankroll) * kelly * self.config.execution.kelly_fraction
        return int(allocation / candidate.price)

    def enter(self, candidate: SignalCandidate, bankroll: float | None = None, size: int | None = None) -> EntryDecision:
        size = self.determine_position_size(candidate, bankroll) if size is None else size
        if size <= 0:
            return EntryDecision(
                accepted=False,
                position=None,
                event=ActionEvent(
                    event_type="REJECT",
                    timestamp=utc_now(),
                    market_ticker=candidate.market_ticker,
                    side=candidate.side,
                    price=candidate.price,
                    size=0,
                    signal_strength=candidate.signal_strength,
                    confidence_level=candidate.confidence_level,
                    reason="non_positive_kelly",
                ),
            )
        stop_distance = self._stop_distance(candidate.signal_strength)
        take_profit_distance = self._take_profit_distance(candidate.price, candidate.signal_strength)
        stop_price = candidate.price * (1 - stop_distance)
        take_profit_price = min(
            self.config.execution.take_profit_hard_ceiling,
            candidate.price * (1 + take_profit_distance),
        )
        position = VirtualPosition.create(
            market_ticker=candidate.market_ticker,
            side=candidate.side,
            entry_price=candidate.price,
            size=size,
            signal_strength=candidate.signal_strength,
            confidence_level=candidate.confidence_level,
            entry_time=candidate.timestamp,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            reason=candidate.reason,
        )
        event = ActionEvent(
            event_type="ENTER",
            timestamp=utc_now(),
            market_ticker=candidate.market_ticker,
            side=candidate.side,
            price=candidate.price,
            size=size,
            signal_strength=candidate.signal_strength,
            confidence_level=candidate.confidence_level,
            reason=candidate.reason,
            metadata={
                "position_id": position.position_id,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price,
            },
        )
        return EntryDecision(True, position, event)

    def update_position(self, position: VirtualPosition, price: float) -> None:
        position.mark_price(price)
        if not self.config.execution.enable_stop_losses:
            return
        trailing_distance = self._stop_distance(position.signal_strength)
        new_stop = price * (1 - trailing_distance)
        if new_stop > position.stop_price:
            position.stop_price = new_stop

    def apply_counter_signal(self, position: VirtualPosition, counter_count: int) -> None:
        if not self.config.execution.enable_stop_losses:
            return
        key = position.signal_strength
        if counter_count >= self.config.strategy.cluster_baseline_count:
            position.stop_price = position.entry_price
            return
        if counter_count == 2:
            tightened = self.config.execution.counter_signal_double[key]
        elif counter_count == 1:
            tightened = self.config.execution.counter_signal_single[key]
        else:
            return
        candidate_stop = position.highest_price * (1 - tightened)
        if candidate_stop > position.stop_price:
            position.stop_price = candidate_stop

    def should_exit(self, position: VirtualPosition, price: float) -> str | None:
        if price >= position.take_profit_price:
            return "take_profit"
        if self.config.execution.enable_stop_losses and price <= position.stop_price:
            return "stop_loss"
        return None

    def exit(self, position: VirtualPosition, price: float, reason: str) -> ActionEvent:
        position.exit_price = price
        position.exit_time = utc_now()
        position.exit_reason = reason
        return ActionEvent(
            event_type="EXIT",
            timestamp=position.exit_time,
            market_ticker=position.market_ticker,
            side=position.side,
            price=price,
            size=position.size,
            signal_strength=position.signal_strength,
            confidence_level=position.confidence_level,
            reason=reason,
            metadata={
                "position_id": position.position_id,
                "entry_price": position.entry_price,
                "pnl": position.pnl(),
            },
        )

    def _take_profit_distance(self, price: float, signal_strength: str) -> float:
        if price <= self.config.strategy.lower_zone_max:
            base = self.config.execution.take_profit_lower_base
        elif price <= self.config.strategy.middle_zone_max:
            base = self.config.execution.take_profit_middle_base
        else:
            base = self.config.execution.take_profit_upper_base
        modifier = self.config.execution.take_profit_signal_adjustment
        if signal_strength == "moderate":
            return base * (1 - modifier)
        if signal_strength == "very_strong":
            return base * (1 + modifier)
        return base

    def _stop_distance(self, signal_strength: str) -> float:
        return {
            "moderate": self.config.execution.stop_moderate,
            "strong": self.config.execution.stop_strong,
            "very_strong": self.config.execution.stop_very_strong,
        }[signal_strength]
