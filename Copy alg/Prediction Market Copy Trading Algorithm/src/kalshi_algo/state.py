from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .models import OrderbookState, TradeEvent, VirtualPosition


@dataclass(slots=True)
class MarketState:
    orderbooks: dict[str, OrderbookState] = field(default_factory=dict)
    trades_by_market_side: dict[tuple[str, str], deque[TradeEvent]] = field(
        default_factory=lambda: defaultdict(deque)
    )
    positions: dict[str, VirtualPosition] = field(default_factory=dict)
    entries_by_market: dict[str, list[datetime]] = field(default_factory=lambda: defaultdict(list))
    loss_streaks_by_market: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cooldown_until_by_market: dict[str, datetime] = field(default_factory=dict)

    def get_orderbook(self, market_ticker: str) -> OrderbookState:
        return self.orderbooks.setdefault(market_ticker, OrderbookState(market_ticker=market_ticker))

    def ingest_trade(self, trade: TradeEvent, window_seconds: int) -> int:
        key = (trade.market_ticker, trade.side)
        window = self.trades_by_market_side[key]
        window.append(trade)
        cutoff = trade.timestamp - timedelta(seconds=window_seconds)
        while window and window[0].timestamp < cutoff:
            window.popleft()
        return len(window)

    def cluster_count(self, market_ticker: str, side: str) -> int:
        return len(self.trades_by_market_side[(market_ticker, side)])

    def record_entry(self, market_ticker: str, timestamp: datetime) -> None:
        self.entries_by_market[market_ticker].append(timestamp)

    def recent_entries(self, market_ticker: str, since: datetime) -> list[datetime]:
        return [timestamp for timestamp in self.entries_by_market[market_ticker] if timestamp >= since]

    def record_market_exit(
        self,
        market_ticker: str,
        timestamp: datetime,
        pnl: float,
        loss_threshold: float,
        cooldown_loss_count: int,
        cooldown_seconds: int,
    ) -> None:
        if pnl <= -loss_threshold:
            self.loss_streaks_by_market[market_ticker] += 1
            if self.loss_streaks_by_market[market_ticker] >= cooldown_loss_count:
                self.cooldown_until_by_market[market_ticker] = timestamp + timedelta(
                    seconds=cooldown_seconds
                )
            return
        self.loss_streaks_by_market[market_ticker] = 0
        self.cooldown_until_by_market.pop(market_ticker, None)

    def market_cooldown_active(self, market_ticker: str, timestamp: datetime) -> bool:
        cooldown_until = self.cooldown_until_by_market.get(market_ticker)
        if cooldown_until is None:
            return False
        if timestamp < cooldown_until:
            return True
        self.cooldown_until_by_market.pop(market_ticker, None)
        return False
