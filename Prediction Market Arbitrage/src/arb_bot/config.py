from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    mode: str = "paper"
    book_freshness_seconds: float = 2.5
    min_pair_confidence: Decimal = Decimal("0.90")
    poll_interval_seconds: float = 5.0
    max_pairs: int = 25
    discovery_limit: int = 50
    rediscovery_interval_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    min_net_edge: Decimal = Decimal("0.02")
    slippage_buffer: Decimal = Decimal("0.005")
    min_trade_size: Decimal = Decimal("1")


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_notional_per_leg: Decimal = Decimal("25")
    max_total_exposure: Decimal = Decimal("100")
    max_pair_exposure: Decimal = Decimal("25")
    max_venue_exposure: Decimal = Decimal("75")
    daily_loss_limit: Decimal = Decimal("25")
    max_consecutive_losses: int = 3
    kill_switch_file: str = "STOP_TRADING"


@dataclass(frozen=True, slots=True)
class AppConfig:
    runtime: RuntimeConfig = RuntimeConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    kalshi_key_file: str = "../key.txt"
    polymarket_key_file: str = "../polykey.txt"


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def load_config(path: str | Path) -> AppConfig:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    runtime_data = data.get("runtime", {})
    strategy_data = data.get("strategy", {})
    risk_data = data.get("risk", {})
    return AppConfig(
        runtime=RuntimeConfig(
            mode=runtime_data.get("mode", "paper"),
            book_freshness_seconds=float(runtime_data.get("book_freshness_seconds", 2.5)),
            min_pair_confidence=_decimal(runtime_data.get("min_pair_confidence", "0.90")),
            poll_interval_seconds=float(runtime_data.get("poll_interval_seconds", 5.0)),
            max_pairs=int(runtime_data.get("max_pairs", 25)),
            discovery_limit=int(runtime_data.get("discovery_limit", 50)),
            rediscovery_interval_seconds=float(runtime_data.get("rediscovery_interval_seconds", 300.0)),
        ),
        strategy=StrategyConfig(
            min_net_edge=_decimal(strategy_data.get("min_net_edge", "0.02")),
            slippage_buffer=_decimal(strategy_data.get("slippage_buffer", "0.005")),
            min_trade_size=_decimal(strategy_data.get("min_trade_size", "1")),
        ),
        risk=RiskConfig(
            max_notional_per_leg=_decimal(risk_data.get("max_notional_per_leg", "25")),
            max_total_exposure=_decimal(risk_data.get("max_total_exposure", "100")),
            max_pair_exposure=_decimal(risk_data.get("max_pair_exposure", "25")),
            max_venue_exposure=_decimal(risk_data.get("max_venue_exposure", "75")),
            daily_loss_limit=_decimal(risk_data.get("daily_loss_limit", "25")),
            max_consecutive_losses=int(risk_data.get("max_consecutive_losses", 3)),
            kill_switch_file=risk_data.get("kill_switch_file", "STOP_TRADING"),
        ),
        kalshi_key_file=data.get("kalshi", {}).get("key_file", "../key.txt"),
        polymarket_key_file=data.get("polymarket", {}).get("key_file", "../polykey.txt"),
    )
