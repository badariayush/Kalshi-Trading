from __future__ import annotations

import asyncio
from pathlib import Path
from time import time
from typing import Any

from mm_bot.config import AppConfig
from mm_bot.events import emit
from mm_bot.fills import apply_fill_to_inventory, conservative_fill_from_book, conservative_fill_from_trade
from mm_bot.kalshi import KalshiRestClient, Market
from mm_bot.orderbook import YesOrderBook, apply_ws_message
from mm_bot.strategy import Quote, generate_quotes, should_requote
from mm_bot.ws import stream_kalshi_markets


class PaperMarketMaker:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.books: dict[str, YesOrderBook] = {}
        self.inventory: dict[str, int] = {}
        self.active_quotes: dict[tuple[str, str], Quote] = {}
        self.stop = asyncio.Event()
        self._last_heartbeat = 0.0

    def discover(self) -> list[Market]:
        markets = KalshiRestClient(self.config.kalshi).discover_btc_15m_markets()
        for market in markets:
            emit("MARKET_DISCOVERED", ticker=market.ticker, title=market.title, close_time=market.close_time)
        return markets

    async def run(self, max_seconds: float | None = None) -> None:
        if self.config.runtime.mode != "paper":
            raise RuntimeError('Refusing to run unless runtime.mode = "paper"')
        markets = self.discover()
        tickers = [market.ticker for market in markets]
        ws_task = asyncio.create_task(stream_kalshi_markets(self.config, tickers, self._handle_ws_message, self.stop))
        quote_task = asyncio.create_task(self._quote_loop())
        try:
            if max_seconds is None:
                await quote_task
            else:
                await asyncio.wait_for(quote_task, timeout=max_seconds)
        except TimeoutError:
            emit("HEARTBEAT", status="max_seconds_reached", max_seconds=max_seconds)
        finally:
            self.stop.set()
            ws_task.cancel()
            quote_task.cancel()
            await asyncio.gather(ws_task, quote_task, return_exceptions=True)

    async def _handle_ws_message(self, payload: dict[str, Any]) -> None:
        msg_type = payload.get("type")
        if msg_type in {"orderbook_snapshot", "orderbook_delta"}:
            book = apply_ws_message(self.books, payload)
            if book is not None:
                emit(
                    "BOOK_UPDATE",
                    ticker=book.market_ticker,
                    best_bid=book.best_bid,
                    best_ask=book.best_ask,
                    midpoint=book.midpoint(),
                    bids=len(book.bids),
                    asks=len(book.asks),
                )
                self._check_book_cross_fills(book)
        elif msg_type == "trade":
            self._check_trade_fills(payload)

    async def _quote_loop(self) -> None:
        while not self.stop.is_set():
            if Path(self.config.runtime.kill_switch_file).exists():
                emit("RISK_BLOCK", reason="kill_switch_file_present", file=self.config.runtime.kill_switch_file)
                self.stop.set()
                return
            self._emit_heartbeat_if_due()
            for book in list(self.books.values()):
                self._refresh_quotes(book)
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=self.config.runtime.quote_refresh_seconds)
            except TimeoutError:
                pass

    def _refresh_quotes(self, book: YesOrderBook) -> None:
        ticker = book.market_ticker
        if book.stale(self.config.runtime.book_stale_seconds):
            self._cancel_market_quotes(ticker, "stale_book")
            emit("RISK_BLOCK", ticker=ticker, reason="stale_book")
            return
        inv = self.inventory.get(ticker, 0)
        decision = generate_quotes(book, inv, self.config.strategy, self.config.risk)
        for reason in decision.blocked_reasons:
            emit("RISK_BLOCK", ticker=ticker, reason=reason, inventory=inv, fair_value=decision.fair_value)
        wanted = {(quote.market_ticker, quote.side): quote for quote in decision.quotes}
        for key, old in list(self.active_quotes.items()):
            if key[0] == ticker and key not in wanted:
                self.active_quotes.pop(key)
                emit("WOULD_CANCEL", ticker=old.market_ticker, side=old.side, price=old.price, size=old.size, reason="quote_no_longer_valid")
        for key, new in wanted.items():
            old = self.active_quotes.get(key)
            if should_requote(old, new, self.config.strategy.requote_threshold):
                if old is not None:
                    emit("WOULD_CANCEL", ticker=old.market_ticker, side=old.side, price=old.price, size=old.size, reason="requote")
                self.active_quotes[key] = new
                emit("WOULD_PLACE", ticker=new.market_ticker, side=new.side, price=new.price, size=new.size, fair_value=decision.fair_value, inventory=inv)

    def _cancel_market_quotes(self, ticker: str, reason: str) -> None:
        for key, quote in list(self.active_quotes.items()):
            if key[0] == ticker:
                self.active_quotes.pop(key)
                emit("WOULD_CANCEL", ticker=quote.market_ticker, side=quote.side, price=quote.price, size=quote.size, reason=reason)

    def _check_book_cross_fills(self, book: YesOrderBook) -> None:
        for key, quote in list(self.active_quotes.items()):
            if key[0] != book.market_ticker:
                continue
            fill = conservative_fill_from_book(quote, book.best_bid, book.best_ask)
            if fill is not None:
                self._record_fill(fill.market_ticker, fill.side, fill.price, fill.size, fill.reason)
                self.active_quotes.pop(key, None)

    def _check_trade_fills(self, payload: dict[str, Any]) -> None:
        for key, quote in list(self.active_quotes.items()):
            fill = conservative_fill_from_trade(quote, payload)
            if fill is not None:
                self._record_fill(fill.market_ticker, fill.side, fill.price, fill.size, fill.reason)
                self.active_quotes.pop(key, None)

    def _record_fill(self, ticker: str, side: str, price, size: int, reason: str) -> None:
        from mm_bot.fills import PaperFill

        fill = PaperFill(ticker, side, price, size, reason)
        new_inventory = apply_fill_to_inventory(self.inventory.get(ticker, 0), fill)
        self.inventory[ticker] = new_inventory
        emit("PAPER_FILL", ticker=ticker, side=side, price=price, size=size, reason=reason, inventory=new_inventory)

    def _emit_heartbeat_if_due(self) -> None:
        now = time()
        if now - self._last_heartbeat >= self.config.runtime.heartbeat_seconds:
            self._last_heartbeat = now
            emit("HEARTBEAT", markets=len(self.books), active_quotes=len(self.active_quotes), inventory=sum(abs(v) for v in self.inventory.values()))
