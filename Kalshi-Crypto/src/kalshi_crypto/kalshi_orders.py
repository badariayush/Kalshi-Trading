from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class KalshiOrderRequest:
    market_ticker: str
    client_order_id: str
    action: str
    side: str
    order_type: str
    count: int
    yes_price_cents: int | None = None
    no_price_cents: int | None = None
    buy_max_cost_cents: int | None = None
    post_only: bool = False
    reduce_only: bool = False

    def __post_init__(self) -> None:
        if not self.market_ticker:
            raise ValueError("market_ticker is required")
        if not self.client_order_id:
            raise ValueError("client_order_id is required")
        if self.action not in {"buy", "sell"}:
            raise ValueError("action must be buy or sell")
        if self.side not in {"yes", "no"}:
            raise ValueError("side must be yes or no")
        if self.order_type not in {"limit", "market"}:
            raise ValueError("order_type must be limit or market")
        if self.count <= 0:
            raise ValueError("count must be positive")
        if self.yes_price_cents is None and self.no_price_cents is None:
            raise ValueError("yes_price_cents or no_price_cents is required")
        if self.yes_price_cents is not None:
            _validate_cents("yes_price_cents", self.yes_price_cents)
        if self.no_price_cents is not None:
            _validate_cents("no_price_cents", self.no_price_cents)
        if self.action == "buy" and self.buy_max_cost_cents is None:
            raise ValueError("buy_max_cost_cents is required for buy orders")
        if self.buy_max_cost_cents is not None and self.buy_max_cost_cents <= 0:
            raise ValueError("buy_max_cost_cents must be positive")

    def path(self) -> str:
        return "/portfolio/orders"

    def payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ticker": self.market_ticker,
            "client_order_id": self.client_order_id,
            "action": self.action,
            "side": self.side,
            "type": self.order_type,
            "count": self.count,
        }
        if self.yes_price_cents is not None:
            payload["yes_price"] = self.yes_price_cents
        if self.no_price_cents is not None:
            payload["no_price"] = self.no_price_cents
        if self.buy_max_cost_cents is not None:
            payload["buy_max_cost"] = self.buy_max_cost_cents
        payload["post_only"] = self.post_only
        payload["reduce_only"] = self.reduce_only
        return payload


def _validate_cents(field_name: str, value: int) -> None:
    if value <= 0 or value >= 100:
        raise ValueError(f"{field_name} must be between 1 and 99")
