from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .config import AppConfig
from .models import ActionEvent, SessionStats, SignalCandidate, VirtualPosition, utc_now
from .state import MarketState


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    reason: str


class RiskEngine:
    def __init__(self, config: AppConfig, stats: SessionStats):
        self.config = config
        self.stats = stats
        self.halted = False
        self.halt_reason: str | None = None

    def evaluate_entry(
        self,
        candidate: SignalCandidate,
        state: MarketState,
        expected_exit_price: float | None,
        entry_spread: float | None = None,
        proposed_size: int = 1,
    ) -> RiskDecision:
        if self.halted:
            return RiskDecision(False, self.halt_reason or "halted")
        if len(state.positions) >= self.config.portfolio.max_open_positions:
            return RiskDecision(False, "max_open_positions")
        if state.market_cooldown_active(candidate.market_ticker, candidate.timestamp):
            return RiskDecision(False, "market_loss_cooldown")
        if self._is_btc_related(candidate.market_ticker):
            btc_positions = sum(
                1
                for position in state.positions.values()
                if self._is_btc_related(position.market_ticker)
            )
            if btc_positions >= self.config.portfolio.max_open_btc_related_positions:
                return RiskDecision(False, "btc_exposure_limit")
        recent_window = candidate.timestamp - timedelta(
            seconds=self.config.portfolio.min_seconds_between_entries
        )
        recent_entries = state.recent_entries(candidate.market_ticker, recent_window)
        if recent_entries and len(state.entries_by_market[candidate.market_ticker]) >= self.config.portfolio.max_entries_per_market:
            return RiskDecision(False, "max_entries_per_market")
        if recent_entries:
            return RiskDecision(False, "entry_cooldown")
        if expected_exit_price is None:
            return RiskDecision(False, "insufficient_liquidity")
        if entry_spread is not None and entry_spread > self.config.strategy.max_entry_spread:
            return RiskDecision(False, "spread_too_wide")
        slippage = abs(candidate.price - expected_exit_price)
        if slippage > self.config.strategy.expected_exit_slippage_warn:
            return RiskDecision(False, "exit_slippage_too_high")
        exposure = sum(position.entry_price * position.size for position in state.positions.values())
        proposed = exposure + candidate.price * proposed_size
        if proposed > self.stats.cash * self.config.portfolio.max_portfolio_exposure_ratio:
            return RiskDecision(False, "portfolio_exposure_limit")
        if self.stats.peak_equity <= 0:
            return RiskDecision(True, "ok")
        drawdown = (self.stats.peak_equity - (self.stats.starting_cash + self.stats.realized_pnl)) / self.stats.peak_equity
        if drawdown >= self.config.portfolio.drawdown_halt_ratio:
            self.halt("drawdown_limit")
            return RiskDecision(False, "drawdown_limit")
        return RiskDecision(True, "ok")

    @staticmethod
    def _is_btc_related(market_ticker: str) -> bool:
        return market_ticker.upper().startswith("KXBTC")

    def update_after_exit(self, position: VirtualPosition) -> list[ActionEvent]:
        events: list[ActionEvent] = []
        pnl = position.pnl()
        self.stats.realized_pnl += pnl
        self.stats.cash = self.stats.starting_cash + self.stats.realized_pnl
        self.stats.peak_equity = max(self.stats.peak_equity, self.stats.cash)
        if pnl <= -self.config.portfolio.loss_count_threshold:
            self.stats.daily_losses += 1
            self.stats.consecutive_losses += 1
        else:
            self.stats.consecutive_losses = 0
        if self.stats.consecutive_losses >= self.config.portfolio.consecutive_loss_halt_count:
            self.halt("consecutive_loss_limit")
            events.append(self.halt_event("consecutive_loss_limit"))
        loss_frequency_halt_allowed = (
            not self.config.portfolio.require_negative_pnl_for_loss_frequency_halt
            or self.stats.realized_pnl < 0
        )
        if self.stats.daily_losses >= self.config.portfolio.daily_loss_frequency_limit and loss_frequency_halt_allowed:
            self.halt("daily_loss_frequency_limit")
            events.append(self.halt_event("daily_loss_frequency_limit"))
        if self.stats.cash <= self.stats.starting_cash * (1 - self.config.portfolio.daily_loss_halt_ratio):
            self.halt("daily_loss_limit")
            events.append(self.halt_event("daily_loss_limit"))
        return events

    def halt(self, reason: str) -> None:
        self.halted = True
        self.halt_reason = reason

    def halt_event(self, reason: str) -> ActionEvent:
        return ActionEvent(
            event_type="HALT",
            timestamp=utc_now(),
            market_ticker=None,
            side=None,
            price=None,
            size=None,
            signal_strength=None,
            confidence_level=None,
            reason=reason,
        )
