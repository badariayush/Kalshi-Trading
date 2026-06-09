from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mm_bot.strategy import Quote


@dataclass(frozen=True, slots=True)
class PaperFill:
    market_ticker: str
    side: str
    price: Decimal
    size: int
    reason: str


def conservative_fill_from_book(quote: Quote, best_bid: Decimal | None, best_ask: Decimal | None) -> PaperFill | None:
    if quote.side == "bid" and best_ask is not None and best_ask <= quote.price:
        return PaperFill(quote.market_ticker, quote.side, quote.price, quote.size, "opposite_ask_crossed_bid")
    if quote.side == "ask" and best_bid is not None and best_bid >= quote.price:
        return PaperFill(quote.market_ticker, quote.side, quote.price, quote.size, "opposite_bid_crossed_ask")
    return None


def conservative_fill_from_trade(quote: Quote, payload: dict[str, Any]) -> PaperFill | None:
    msg = payload.get("msg", {})
    if not isinstance(msg, dict):
        return None
    ticker = msg.get("market_ticker") or msg.get("ticker")
    if ticker != quote.market_ticker:
        return None
    price = _decimal_or_none(msg.get("yes_price_dollars") or msg.get("price_dollars") or msg.get("price"))
    if price is None:
        return None
    taker_side = str(msg.get("taker_book_side") or msg.get("book_side") or msg.get("taker_outcome_side") or "").lower()
    if quote.side == "bid" and taker_side in {"ask", "no"} and price <= quote.price:
        return PaperFill(quote.market_ticker, quote.side, quote.price, quote.size, "trade_crossed_bid")
    if quote.side == "ask" and taker_side in {"bid", "yes"} and price >= quote.price:
        return PaperFill(quote.market_ticker, quote.side, quote.price, quote.size, "trade_crossed_ask")
    return None


def apply_fill_to_inventory(inventory: int, fill: PaperFill) -> int:
    if fill.side == "bid":
        return inventory + fill.size
    return inventory - fill.size


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
