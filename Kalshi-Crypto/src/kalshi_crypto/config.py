from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
import tomllib
from typing import Any, Mapping

from kalshi_crypto.live_config import LiveDataConfig, OrderApiConfig


class ConfigError(ValueError):
    pass


class RuntimeMode(str, Enum):
    PAPER_SIMULATED = "paper_simulated"
    PAPER_DEMO = "paper_demo"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    mode: RuntimeMode = RuntimeMode.PAPER_SIMULATED
    confirm_live: bool = False
    allow_trade_mcp: bool = False

    def __post_init__(self) -> None:
        if self.mode is RuntimeMode.LIVE and not self.confirm_live:
            raise ConfigError("live mode requires confirm_live")
        if self.allow_trade_mcp:
            raise ConfigError("trade execution MCP requires a separate security/risk review")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RuntimeConfig":
        runtime = data.get("runtime", data)
        if not isinstance(runtime, Mapping):
            raise ConfigError("runtime config must be a mapping")

        return cls(
            mode=_runtime_mode(runtime.get("mode", RuntimeMode.PAPER_SIMULATED.value)),
            confirm_live=_bool_value(runtime.get("confirm_live", False)),
            allow_trade_mcp=_bool_value(runtime.get("allow_trade_mcp", False)),
        )


@dataclass(frozen=True, slots=True)
class TradeManagementConfig:
    take_profit_pct: Decimal = Decimal("0.20")
    min_arb_margin: Decimal = Decimal("0.0200")
    slippage_buffer: Decimal = Decimal("0.0050")
    partial_hedge_enabled: bool = True
    partial_hedge_opposing_ask_trigger: Decimal = Decimal("0.5000")
    partial_hedge_max_loss_pct_of_original_risk: Decimal | None = Decimal("0.50")
    partial_hedge_max_loss_usd: Decimal | None = None
    partial_hedge_max_contracts_pct_of_original_size: Decimal = Decimal("1.00")
    max_entry_book_age_ms: int = 1500
    max_true_arb_book_age_ms: int = 750
    max_partial_hedge_book_age_ms: int = 750
    min_depth_contracts: int = 1

    def __post_init__(self) -> None:
        _require_decimal_between_zero_and_one("take_profit_pct", self.take_profit_pct)
        _require_positive_decimal("min_arb_margin", self.min_arb_margin)
        _require_non_negative_decimal("slippage_buffer", self.slippage_buffer)
        _require_decimal_between_zero_and_one(
            "partial_hedge_opposing_ask_trigger",
            self.partial_hedge_opposing_ask_trigger,
        )
        if self.partial_hedge_max_loss_pct_of_original_risk is not None:
            _require_decimal_between_zero_and_one(
                "partial_hedge_max_loss_pct_of_original_risk",
                self.partial_hedge_max_loss_pct_of_original_risk,
            )
        if self.partial_hedge_max_loss_usd is not None:
            _require_positive_decimal(
                "partial_hedge_max_loss_usd",
                self.partial_hedge_max_loss_usd,
            )
        if (
            self.partial_hedge_max_loss_pct_of_original_risk is None
            and self.partial_hedge_max_loss_usd is None
        ):
            raise ConfigError("partial hedge requires a percent or USD max-loss target")
        _require_decimal_between_zero_and_one(
            "partial_hedge_max_contracts_pct_of_original_size",
            self.partial_hedge_max_contracts_pct_of_original_size,
            allow_one=True,
        )
        _require_positive_int("max_entry_book_age_ms", self.max_entry_book_age_ms)
        _require_positive_int("max_true_arb_book_age_ms", self.max_true_arb_book_age_ms)
        _require_positive_int(
            "max_partial_hedge_book_age_ms",
            self.max_partial_hedge_book_age_ms,
        )
        _require_positive_int("min_depth_contracts", self.min_depth_contracts)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TradeManagementConfig":
        trade = data.get("trade_management", data)
        if not isinstance(trade, Mapping):
            raise ConfigError("trade_management config must be a mapping")

        return cls(
            take_profit_pct=_decimal(trade.get("take_profit_pct", "0.20")),
            min_arb_margin=_decimal(trade.get("min_arb_margin", "0.0200")),
            slippage_buffer=_decimal(trade.get("slippage_buffer", "0.0050")),
            partial_hedge_enabled=_bool_value(
                trade.get("partial_hedge_enabled", True)
            ),
            partial_hedge_opposing_ask_trigger=_decimal(
                trade.get("partial_hedge_opposing_ask_trigger", "0.5000")
            ),
            partial_hedge_max_loss_pct_of_original_risk=_optional_decimal(
                trade.get("partial_hedge_max_loss_pct_of_original_risk", "0.50")
            ),
            partial_hedge_max_loss_usd=_optional_decimal(
                trade.get("partial_hedge_max_loss_usd")
            ),
            partial_hedge_max_contracts_pct_of_original_size=_decimal(
                trade.get("partial_hedge_max_contracts_pct_of_original_size", "1.00")
            ),
            max_entry_book_age_ms=int(trade.get("max_entry_book_age_ms", 1500)),
            max_true_arb_book_age_ms=int(trade.get("max_true_arb_book_age_ms", 750)),
            max_partial_hedge_book_age_ms=int(
                trade.get("max_partial_hedge_book_age_ms", 750)
            ),
            min_depth_contracts=int(trade.get("min_depth_contracts", 1)),
        )

    def partial_hedge_max_loss_for_original_risk(
        self,
        original_risk: Decimal,
    ) -> Decimal:
        _require_positive_decimal("original_risk", original_risk)
        candidates: list[Decimal] = []
        if self.partial_hedge_max_loss_pct_of_original_risk is not None:
            candidates.append(
                original_risk * self.partial_hedge_max_loss_pct_of_original_risk
            )
        if self.partial_hedge_max_loss_usd is not None:
            candidates.append(self.partial_hedge_max_loss_usd)
        return min(candidates)


@dataclass(frozen=True, slots=True)
class CircuitBreakerConfig:
    data_feed_stale_ms: int = 2_500
    halt_new_entries_on_feed_unhealthy: bool = True

    def __post_init__(self) -> None:
        _require_positive_int("data_feed_stale_ms", self.data_feed_stale_ms)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CircuitBreakerConfig":
        circuit = data.get("circuit_breakers", data)
        if not isinstance(circuit, Mapping):
            raise ConfigError("circuit_breakers config must be a mapping")

        if "data_feed_stale_ms" in circuit:
            stale_ms = int(circuit["data_feed_stale_ms"])
        else:
            stale_seconds = _decimal(circuit.get("data_feed_stale_seconds", "2.5"))
            stale_ms = int(stale_seconds * Decimal("1000"))

        return cls(
            data_feed_stale_ms=stale_ms,
            halt_new_entries_on_feed_unhealthy=_bool_value(
                circuit.get("halt_new_entries_on_feed_unhealthy", True)
            ),
        )


@dataclass(frozen=True, slots=True)
class AppConfig:
    runtime: RuntimeConfig = RuntimeConfig()
    trade_management: TradeManagementConfig = field(
        default_factory=TradeManagementConfig
    )
    circuit_breakers: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    live_data: LiveDataConfig = field(default_factory=LiveDataConfig)
    order_api: OrderApiConfig = field(default_factory=OrderApiConfig)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AppConfig":
        if not isinstance(data, Mapping):
            raise ConfigError("app config must be a mapping")
        try:
            live_data = LiveDataConfig.from_mapping(data)
            order_api = OrderApiConfig.from_mapping(data)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        return cls(
            runtime=RuntimeConfig.from_mapping(data),
            trade_management=TradeManagementConfig.from_mapping(data),
            circuit_breakers=CircuitBreakerConfig.from_mapping(data),
            live_data=live_data,
            order_api=order_api,
        )


def load_app_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc

    try:
        data = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in config: {config_path}") from exc

    return AppConfig.from_mapping(data)


def _runtime_mode(value: object) -> RuntimeMode:
    if isinstance(value, RuntimeMode):
        return value
    try:
        return RuntimeMode(str(value))
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in RuntimeMode)
        raise ConfigError(f"invalid runtime mode {value!r}; expected one of: {allowed}") from exc


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ConfigError(f"expected boolean value, got {value!r}")


def _decimal(value: object) -> Decimal:
    if value is None:
        raise ConfigError("expected decimal value, got null")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ConfigError(f"expected decimal value, got {value!r}") from exc


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return _decimal(value)


def _require_positive_decimal(name: str, value: Decimal) -> None:
    if value <= Decimal("0"):
        raise ConfigError(f"{name} must be positive")


def _require_non_negative_decimal(name: str, value: Decimal) -> None:
    if value < Decimal("0"):
        raise ConfigError(f"{name} must be non-negative")


def _require_decimal_between_zero_and_one(
    name: str,
    value: Decimal,
    allow_one: bool = False,
) -> None:
    upper_ok = value <= Decimal("1") if allow_one else value < Decimal("1")
    if value <= Decimal("0") or not upper_ok:
        range_text = "between 0 and 1 inclusive" if allow_one else "between 0 and 1"
        raise ConfigError(f"{name} must be {range_text}")


def _require_positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
