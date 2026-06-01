from __future__ import annotations

from decimal import Decimal
import json
from time import time
from typing import Any

import requests

from arb_bot.models import BookLevel, OrderBook, Venue, VenueMarket


class PolymarketConnector:
    """Public Polymarket market discovery and CLOB snapshot client."""

    gamma_base_url = "https://gamma-api.polymarket.com"
    clob_base_url = "https://clob.polymarket.com"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def discover_markets(self, limit: int = 100, tag: str | None = None) -> list[VenueMarket]:
        markets: list[VenueMarket] = []
        page_size = min(100, limit)
        max_pages = 8
        for page_index, offset in enumerate(range(0, max(limit, page_size * max_pages), page_size)):
            if page_index >= max_pages:
                break
            params: dict[str, object] = {
                "active": "true",
                "closed": "false",
                "archived": "false",
                "limit": page_size,
                "offset": offset,
            }
            response = self.session.get(
                f"{self.gamma_base_url}/markets",
                params=params,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            rows = response.json()
            if isinstance(rows, dict):
                rows = rows.get("markets", rows.get("data", []))
            parsed_page = 0
            for row in rows if isinstance(rows, list) else []:
                market = self._parse_market(row)
                if market is None:
                    continue
                parsed_page += 1
                if _matches_query(market, tag):
                    markets.append(market)
            if parsed_page == 0 or len(markets) >= limit:
                break
        return markets

    def fetch_order_book(self, market: VenueMarket) -> OrderBook:
        if not market.yes_token_id or not market.no_token_id:
            raise ValueError(f"Polymarket market {market.market_id} is missing YES/NO token ids")
        yes_asks = self._fetch_token_asks(market.yes_token_id)
        no_asks = self._fetch_token_asks(market.no_token_id)
        return OrderBook(
            venue=Venue.POLYMARKET,
            market_id=market.market_id,
            yes_asks=yes_asks,
            no_asks=no_asks,
            timestamp=time(),
        )

    def _fetch_token_asks(self, token_id: str) -> list[BookLevel]:
        response = self.session.get(
            f"{self.clob_base_url}/book",
            params={"token_id": token_id},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        asks = data.get("asks", []) if isinstance(data, dict) else []
        return [
            BookLevel(price=Decimal(str(level["price"])), size=Decimal(str(level["size"])))
            for level in asks
            if isinstance(level, dict) and Decimal(str(level.get("size", "0"))) > 0
        ]

    def _parse_market(self, row: dict[str, Any]) -> VenueMarket | None:
        token_ids = _coerce_list(row.get("clobTokenIds") or row.get("clob_token_ids"))
        outcomes = [str(item).lower() for item in _coerce_list(row.get("outcomes"))]
        yes_token_id: str | None = None
        no_token_id: str | None = None
        if len(token_ids) >= 2:
            if "yes" in outcomes and "no" in outcomes:
                yes_token_id = str(token_ids[outcomes.index("yes")])
                no_token_id = str(token_ids[outcomes.index("no")])
            else:
                yes_token_id = str(token_ids[0])
                no_token_id = str(token_ids[1])
        if not yes_token_id or not no_token_id:
            return None
        title = str(row.get("question") or row.get("title") or row.get("slug") or row.get("id"))
        return VenueMarket(
            venue=Venue.POLYMARKET,
            market_id=str(row.get("id") or row.get("conditionId") or title),
            title=title,
            category=_category_from_text(title, str(row.get("category", ""))),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            close_time=str(row.get("endDate") or row.get("end_date") or "") or None,
            raw={"slug": row.get("slug"), "condition_id": row.get("conditionId")},
        )


def _coerce_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


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
    text = f"{market.title} {market.category}".lower()
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
    return bool(words & {"btc", "bitcoin", "eth", "ethereum", "crypto", "sol", "solana", "xrp"})


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
        "stanley cup",
        "world cup",
        "points",
        "runs",
        "goals",
        "assists",
        "rebounds",
        "wins by over",
        "points scored",
        "runs scored",
        "goals scored",
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
