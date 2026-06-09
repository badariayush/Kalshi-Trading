from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from time import time
from typing import Any


@dataclass(slots=True)
class YesOrderBook:
    market_ticker: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_update_ts: float = field(default_factory=time)

    def apply_snapshot(self, msg: dict[str, Any]) -> None:
        self.bids = _levels_to_map(msg.get("yes") or msg.get("bids") or [])
        self.asks = _levels_to_map(msg.get("no") or msg.get("asks") or [])
        self.last_update_ts = time()

    def apply_delta(self, msg: dict[str, Any]) -> None:
        side = _normalize_side(msg)
        price = _decimal_or_none(msg.get("price_dollars") or msg.get("price") or msg.get("yes_price_dollars"))
        delta = _decimal_or_none(msg.get("delta") or msg.get("change") or msg.get("size_delta"))
        if side is None or price is None or delta is None:
            return
        levels = self.bids if side == "bid" else self.asks
        new_size = levels.get(price, Decimal("0")) + delta
        if new_size <= 0:
            levels.pop(price, None)
        else:
            levels[price] = new_size
        self.last_update_ts = time()

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    def midpoint(self) -> Decimal | None:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None or bid >= ask:
            return None
        return (bid + ask) / Decimal("2")

    def stale(self, max_age_seconds: float) -> bool:
        return time() - self.last_update_ts > max_age_seconds


def apply_ws_message(books: dict[str, YesOrderBook], payload: dict[str, Any]) -> YesOrderBook | None:
    msg = payload.get("msg", {})
    if not isinstance(msg, dict):
        return None
    ticker = msg.get("market_ticker") or msg.get("ticker")
    if not ticker:
        return None
    book = books.setdefault(str(ticker), YesOrderBook(str(ticker)))
    msg_type = payload.get("type")
    if msg_type == "orderbook_snapshot":
        book.apply_snapshot(msg)
    elif msg_type == "orderbook_delta":
        book.apply_delta(msg)
    else:
        return None
    return book


def _levels_to_map(levels: Any) -> dict[Decimal, Decimal]:
    parsed: dict[Decimal, Decimal] = {}
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        price: Any
        size: Any
        if isinstance(level, dict):
            price = level.get("price_dollars") or level.get("price")
            size = level.get("size") or level.get("quantity") or level.get("count")
        elif isinstance(level, list | tuple) and len(level) >= 2:
            price, size = level[0], level[1]
        else:
            continue
        p = _decimal_or_none(price)
        s = _decimal_or_none(size)
        if p is not None and s is not None and s > 0:
            parsed[p] = s
    return parsed


def _normalize_side(msg: dict[str, Any]) -> str | None:
    raw = str(msg.get("book_side") or msg.get("side") or msg.get("outcome_side") or "").lower()
    if raw in {"bid", "yes"}:
        return "bid"
    if raw in {"ask", "no"}:
        return "ask"
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except Exception:
        return None
    if decimal > 1 and decimal <= 100:
        return decimal / Decimal("100")
    return decimal
