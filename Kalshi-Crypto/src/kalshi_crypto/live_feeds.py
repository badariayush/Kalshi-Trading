from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class KalshiWebSocketSubscription:
    market_tickers: tuple[str, ...]
    channels: tuple[str, ...]
    command_id: int = 1

    def __post_init__(self) -> None:
        _require_non_empty_strings("market_tickers", self.market_tickers)
        _require_non_empty_strings("channels", self.channels)
        if self.command_id <= 0:
            raise ValueError("command_id must be positive")

    def messages(self) -> tuple[dict[str, Any], ...]:
        return (
            {
                "id": self.command_id,
                "cmd": "subscribe",
                "params": {
                    "channels": list(self.channels),
                    "market_tickers": list(self.market_tickers),
                },
            },
        )


@dataclass(frozen=True, slots=True)
class CoinbaseWebSocketSubscription:
    product_ids: tuple[str, ...]
    channels: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty_strings("product_ids", self.product_ids)
        _require_non_empty_strings("channels", self.channels)

    def messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "type": "subscribe",
                "product_ids": list(self.product_ids),
                "channel": channel,
            }
            for channel in self.channels
        )


def _require_non_empty_strings(field_name: str, values: tuple[str, ...]) -> None:
    if not values or any(not value for value in values):
        raise ValueError(f"{field_name} must not be empty")
