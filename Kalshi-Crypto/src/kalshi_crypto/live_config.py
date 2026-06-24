from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class LiveDataConfig:
    enable_live_network: bool = True
    kalshi_ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    coinbase_ws_url: str = "wss://advanced-trade-ws.coinbase.com"
    kalshi_channels: tuple[str, ...] = (
        "orderbook_delta",
        "ticker",
        "market_lifecycle_v2",
    )
    coinbase_channels: tuple[str, ...] = ("ticker", "level2")
    coinbase_product_ids: tuple[str, ...] = ("BTC-USD", "ETH-USD")

    def __post_init__(self) -> None:
        if not self.kalshi_ws_url.startswith("wss://"):
            raise ValueError("kalshi_ws_url must use wss://")
        if not self.coinbase_ws_url.startswith("wss://"):
            raise ValueError("coinbase_ws_url must use wss://")
        _require_non_empty_tuple("kalshi_channels", self.kalshi_channels)
        _require_non_empty_tuple("coinbase_channels", self.coinbase_channels)
        _require_non_empty_tuple("coinbase_product_ids", self.coinbase_product_ids)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LiveDataConfig":
        live_data = data.get("live_data", data)
        if not isinstance(live_data, Mapping):
            raise ValueError("live_data config must be a mapping")
        return cls(
            enable_live_network=_bool_value(
                live_data.get("enable_live_network", True)
            ),
            kalshi_ws_url=str(
                live_data.get(
                    "kalshi_ws_url",
                    "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
                )
            ),
            coinbase_ws_url=str(
                live_data.get(
                    "coinbase_ws_url",
                    "wss://advanced-trade-ws.coinbase.com",
                )
            ),
            kalshi_channels=_str_tuple(
                live_data.get(
                    "kalshi_channels",
                    ("orderbook_delta", "ticker", "market_lifecycle_v2"),
                ),
                "kalshi_channels",
            ),
            coinbase_channels=_str_tuple(
                live_data.get("coinbase_channels", ("ticker", "level2")),
                "coinbase_channels",
            ),
            coinbase_product_ids=_str_tuple(
                live_data.get("coinbase_product_ids", ("BTC-USD", "ETH-USD")),
                "coinbase_product_ids",
            ),
        )


@dataclass(frozen=True, slots=True)
class OrderApiConfig:
    enable_order_api: bool = True
    allow_order_submission: bool = False
    kalshi_rest_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    def __post_init__(self) -> None:
        if not self.kalshi_rest_url.startswith("https://"):
            raise ValueError("kalshi_rest_url must use https://")
        if self.allow_order_submission and not self.enable_order_api:
            raise ValueError("allow_order_submission requires enable_order_api")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "OrderApiConfig":
        order_api = data.get("order_api", data)
        if not isinstance(order_api, Mapping):
            raise ValueError("order_api config must be a mapping")
        return cls(
            enable_order_api=_bool_value(order_api.get("enable_order_api", True)),
            allow_order_submission=_bool_value(
                order_api.get("allow_order_submission", False)
            ),
            kalshi_rest_url=str(
                order_api.get(
                    "kalshi_rest_url",
                    "https://api.elections.kalshi.com/trade-api/v2",
                )
            ),
        )


def _str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, list | tuple):
        items = tuple(str(item) for item in value)
    else:
        raise ValueError(f"{field_name} must be a string list")
    _require_non_empty_tuple(field_name, items)
    return items


def _require_non_empty_tuple(field_name: str, values: tuple[str, ...]) -> None:
    if not values or any(not value for value in values):
        raise ValueError(f"{field_name} must not be empty")


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"expected boolean value, got {value!r}")
