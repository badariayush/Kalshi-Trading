from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class KalshiConfig:
    environment: str = "production"
    rest_url: str = "https://external-api.kalshi.com/trade-api/v2"
    ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    series_ticker: str = "KXBTC15M"
    discovery_limit: int = 4


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    mode: str = "paper"
    quote_refresh_seconds: float = 1.0
    heartbeat_seconds: float = 10.0
    book_stale_seconds: float = 5.0
    reconnect_initial_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 30.0
    kill_switch_file: str = "STOP_TRADING"


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    quote_size: int = 1
    min_spread: Decimal = Decimal("0.04")
    min_price: Decimal = Decimal("0.01")
    max_price: Decimal = Decimal("0.99")
    inventory_skew: Decimal = Decimal("0.05")
    requote_threshold: Decimal = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_abs_inventory: int = 3


@dataclass(frozen=True, slots=True)
class AppConfig:
    kalshi: KalshiConfig = KalshiConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()


def load_config(path: str | Path) -> AppConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    kalshi = data.get("kalshi", {})
    runtime = data.get("runtime", {})
    strategy = data.get("strategy", {})
    risk = data.get("risk", {})
    return AppConfig(
        kalshi=KalshiConfig(
            environment=str(kalshi.get("environment", "production")),
            rest_url=str(kalshi.get("rest_url", "https://external-api.kalshi.com/trade-api/v2")).rstrip("/"),
            ws_url=str(kalshi.get("ws_url", "wss://external-api-ws.kalshi.com/trade-api/ws/v2")),
            series_ticker=str(kalshi.get("series_ticker", "KXBTC15M")),
            discovery_limit=int(kalshi.get("discovery_limit", 4)),
        ),
        runtime=RuntimeConfig(
            mode=str(runtime.get("mode", "paper")),
            quote_refresh_seconds=float(runtime.get("quote_refresh_seconds", 1.0)),
            heartbeat_seconds=float(runtime.get("heartbeat_seconds", 10.0)),
            book_stale_seconds=float(runtime.get("book_stale_seconds", 5.0)),
            reconnect_initial_delay_seconds=float(runtime.get("reconnect_initial_delay_seconds", 1.0)),
            reconnect_max_delay_seconds=float(runtime.get("reconnect_max_delay_seconds", 30.0)),
            kill_switch_file=str(runtime.get("kill_switch_file", "STOP_TRADING")),
        ),
        strategy=StrategyConfig(
            quote_size=int(strategy.get("quote_size", 1)),
            min_spread=_decimal(strategy.get("min_spread", "0.04")),
            min_price=_decimal(strategy.get("min_price", "0.01")),
            max_price=_decimal(strategy.get("max_price", "0.99")),
            inventory_skew=_decimal(strategy.get("inventory_skew", "0.05")),
            requote_threshold=_decimal(strategy.get("requote_threshold", "0.01")),
        ),
        risk=RiskConfig(max_abs_inventory=int(risk.get("max_abs_inventory", 3))),
    )


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))
