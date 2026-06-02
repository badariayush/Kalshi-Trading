from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(slots=True)
class EnvironmentConfig:
    name: str
    max_orderbook_staleness_seconds: int
    max_trade_age_seconds: int
    disconnect_halt_seconds: int
    reconnect_initial_delay_seconds: float
    reconnect_max_delay_seconds: float


@dataclass(slots=True)
class PortfolioConfig:
    starting_cash: float
    max_open_positions: int
    max_open_btc_related_positions: int
    max_entries_per_market: int
    min_seconds_between_entries: int
    market_loss_cooldown_count: int
    market_loss_cooldown_seconds: int
    max_portfolio_exposure_ratio: float
    drawdown_halt_ratio: float
    daily_loss_halt_ratio: float
    loss_count_threshold: float
    require_negative_pnl_for_loss_frequency_halt: bool
    consecutive_loss_halt_count: int
    daily_loss_frequency_limit: int


@dataclass(slots=True)
class StrategyConfig:
    allowed_categories: list[str]
    min_time_to_resolution_seconds: dict[str, int]
    min_market_volume: dict[str, float]
    price_min: float
    price_max: float
    min_no_price: float
    block_moderate_price_min: float
    block_moderate_price_max: float
    lower_zone_max: float
    middle_zone_max: float
    upper_zone_max: float
    cluster_window_seconds: int
    cluster_baseline_count: int
    cluster_elevated_count: int
    cluster_strong_count: int
    min_trade_size_ratio: float
    elevated_trade_size_ratio: float
    strong_trade_size_ratio: float
    moderate_drift_limit: float
    strong_drift_limit: float
    very_strong_drift_limit: float
    hard_drift_limit: float
    expected_exit_slippage_good: float
    expected_exit_slippage_warn: float
    max_entry_spread: float


@dataclass(slots=True)
class ExecutionConfig:
    moderate_p: float
    strong_p: float
    very_strong_p: float
    kelly_fraction: float
    enable_stop_losses: bool
    take_profit_lower_base: float
    take_profit_middle_base: float
    take_profit_upper_base: float
    take_profit_signal_adjustment: float
    take_profit_hard_ceiling: float
    stop_moderate: float
    stop_strong: float
    stop_very_strong: float
    counter_signal_single: dict[str, float]
    counter_signal_double: dict[str, float]


@dataclass(slots=True)
class FeaturesConfig:
    enable_research_aggression_proxy: bool
    enable_related_market_inference: bool
    enable_passive_persistence_tracking: bool


@dataclass(slots=True)
class AppConfig:
    environment: EnvironmentConfig
    portfolio: PortfolioConfig
    strategy: StrategyConfig
    execution: ExecutionConfig
    features: FeaturesConfig


def _section(data: dict[str, Any], key: str, cls: type[Any]) -> Any:
    return cls(**data[key])


def load_config(path: str | Path) -> AppConfig:
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    return AppConfig(
        environment=_section(raw, "environment", EnvironmentConfig),
        portfolio=_section(raw, "portfolio", PortfolioConfig),
        strategy=_section(raw, "strategy", StrategyConfig),
        execution=_section(raw, "execution", ExecutionConfig),
        features=_section(raw, "features", FeaturesConfig),
    )
