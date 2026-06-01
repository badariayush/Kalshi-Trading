from __future__ import annotations

from decimal import Decimal
import time as time_module
from time import time
from typing import Any

import requests

from arb_bot.models import BookLevel, OrderBook, Venue, VenueMarket


class KalshiConnector:
    """Kalshi public market discovery and order-book snapshot client."""

    base_url = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def discover_markets(self, limit: int = 100, query: str | None = None) -> list[VenueMarket]:
        if _is_crypto_query(query):
            return self._discover_crypto_series(limit=limit, query=query)
        markets: list[VenueMarket] = []
        cursor: str | None = None
        page_size = min(100, max(1, limit))
        pages_seen = 0
        max_pages = 8
        while len(markets) < limit:
            pages_seen += 1
            params: dict[str, object] = {
                "limit": page_size,
                "status": "open",
            }
            if cursor:
                params["cursor"] = cursor
            response = self._get(f"{self.base_url}/markets", params=params)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("markets", payload if isinstance(payload, list) else [])
            parsed_page = 0
            for row in rows if isinstance(rows, list) else []:
                ticker = row.get("ticker")
                if not ticker:
                    continue
                parsed_page += 1
                title = str(row.get("title") or row.get("subtitle") or ticker)
                market = VenueMarket(
                    venue=Venue.KALSHI,
                    market_id=str(ticker),
                    title=title,
                    category=_category_from_text(title, str(row.get("category", ""))),
                    close_time=str(row.get("close_time") or row.get("expected_expiration_time") or "") or None,
                    raw={"event_ticker": row.get("event_ticker"), "market_type": row.get("market_type")},
                )
                if _matches_query(market, query):
                    markets.append(market)
                    if len(markets) >= limit:
                        break
            cursor = payload.get("cursor") if isinstance(payload, dict) else None
            if not cursor or parsed_page == 0 or pages_seen >= max_pages:
                break
        return markets

    def _discover_crypto_series(self, limit: int, query: str | None) -> list[VenueMarket]:
        series = _crypto_series_for_query(query)
        markets: list[VenueMarket] = []
        per_series_limit = max(1, min(100, limit))
        for series_ticker in series:
            cursor: str | None = None
            pages_seen = 0
            while len(markets) < limit and pages_seen < 3:
                pages_seen += 1
                params: dict[str, object] = {
                    "limit": per_series_limit,
                    "status": "open",
                    "series_ticker": series_ticker,
                }
                if cursor:
                    params["cursor"] = cursor
                response = self._get(f"{self.base_url}/markets", params=params)
                response.raise_for_status()
                payload = response.json()
                rows = payload.get("markets", [])
                for row in rows:
                    ticker = row.get("ticker")
                    if not ticker:
                        continue
                    title = str(row.get("title") or row.get("subtitle") or ticker)
                    markets.append(
                        VenueMarket(
                            venue=Venue.KALSHI,
                            market_id=str(ticker),
                            title=title,
                            category="crypto",
                            close_time=str(row.get("close_time") or row.get("expected_expiration_time") or "") or None,
                            raw={"event_ticker": row.get("event_ticker"), "market_type": row.get("market_type"), "series_ticker": series_ticker},
                        )
                    )
                    if len(markets) >= limit:
                        break
                cursor = payload.get("cursor")
                if not cursor or not rows:
                    break
            if len(markets) >= limit:
                break
        return markets

    def fetch_order_book(self, market: VenueMarket) -> OrderBook:
        response = self._get(f"{self.base_url}/markets/{market.market_id}/orderbook")
        response.raise_for_status()
        payload = response.json()
        orderbook = payload.get("orderbook", payload)
        yes_levels = _parse_levels(orderbook.get("yes", []))
        no_levels = _parse_levels(orderbook.get("no", []))
        return OrderBook(
            venue=Venue.KALSHI,
            market_id=market.market_id,
            yes_asks=yes_levels,
            no_asks=no_levels,
            timestamp=time(),
        )

    def _get(self, url: str, params: dict[str, object] | None = None) -> requests.Response:
        delay = 1.0
        for attempt in range(4):
            response = self.session.get(url, params=params, timeout=self.timeout_seconds)
            if response.status_code != 429:
                return response
            retry_after = response.headers.get("Retry-After")
            sleep_for = float(retry_after) if retry_after and retry_after.isdigit() else delay
            time_module.sleep(sleep_for)
            delay *= 2
        return response


def _parse_levels(levels: object) -> list[BookLevel]:
    parsed: list[BookLevel] = []
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        if isinstance(level, dict):
            price = level.get("price")
            size = level.get("size") or level.get("quantity") or level.get("count")
        elif isinstance(level, list | tuple) and len(level) >= 2:
            price, size = level[0], level[1]
        else:
            continue
        price_decimal = Decimal(str(price))
        if price_decimal > 1:
            price_decimal = price_decimal / Decimal("100")
        size_decimal = Decimal(str(size))
        if size_decimal > 0:
            parsed.append(BookLevel(price=price_decimal, size=size_decimal))
    return sorted(parsed, key=lambda item: item.price)


def _category_from_text(title: str, category: str) -> str:
    text = f"{title} {category}".lower()
    if _is_sports_text(text):
        return "sports"
    if _is_crypto_text(text):
        return "crypto"
    if any(term in text for term in ("temperature", "weather", "rain", "snow", "hurricane")):
        return "weather"
    return category.lower().strip() or "unknown"


def _matches_query(market: VenueMarket, query: str | None) -> bool:
    if not query:
        return True
    normalized = query.lower().strip()
    text = f"{market.title} {market.category} {market.market_id}".lower()
    if normalized in {"all", "*"}:
        return True
    if normalized == "crypto":
        return market.category == "crypto" or _is_crypto_text(text)
    if normalized == "sports":
        return market.category == "sports" or _is_sports_text(text)
    if normalized == "weather":
        return market.category == "weather"
    return normalized in text


def _is_crypto_text(text: str) -> bool:
    words = set(text.replace("$", " ").replace("/", " ").replace("-", " ").split())
    return bool(words & {"btc", "bitcoin", "eth", "ethereum", "crypto", "sol", "solana", "xrp"}) or any(
        token.startswith(("kxbtc", "kxeth", "kxsol", "kxxrp")) for token in words
    )


def _is_sports_text(text: str) -> bool:
    sports_terms = (
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "soccer",
        "football",
        "basketball",
        "baseball",
        "tennis",
        "ufc",
        "mma",
        "points",
        "runs",
        "goals",
        "assists",
        "rebounds",
        "wins by over",
        "points scored",
        "runs scored",
        "goals scored",
        "over 5.5",
        "over 7.5",
        "over 8.5",
        "golden knights",
        "hurricanes",
        "yankees",
        "mets",
        "dodgers",
        "celtics",
        "knicks",
        "thunder",
        "spurs",
    )
    return any(term in text for term in sports_terms)


def _is_crypto_query(query: str | None) -> bool:
    return (query or "").lower().strip() in {"crypto", "btc", "bitcoin", "eth", "ethereum"}


def _crypto_series_for_query(query: str | None) -> list[str]:
    normalized = (query or "crypto").lower().strip()
    if normalized in {"btc", "bitcoin"}:
        return ["KXBTC15M", "KXBTCD"]
    if normalized in {"eth", "ethereum"}:
        return ["KXETH15M", "KXETHD"]
    return ["KXBTC15M", "KXBTCD", "KXETH15M", "KXETHD"]
