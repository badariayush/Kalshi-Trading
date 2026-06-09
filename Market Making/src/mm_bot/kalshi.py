from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from mm_bot.config import KalshiConfig


@dataclass(frozen=True, slots=True)
class Market:
    ticker: str
    title: str
    close_time: str | None = None


class KalshiRestClient:
    def __init__(self, config: KalshiConfig, timeout_seconds: float = 10.0) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def discover_btc_15m_markets(self) -> list[Market]:
        response = self.session.get(
            f"{self.config.rest_url}/markets",
            params={
                "limit": self.config.discovery_limit,
                "status": "open",
                "series_ticker": self.config.series_ticker,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("markets", []) if isinstance(payload, dict) else []
        markets: list[Market] = []
        for row in rows:
            parsed = _parse_market(row)
            if parsed is not None:
                markets.append(parsed)
        return markets


def _parse_market(row: Any) -> Market | None:
    if not isinstance(row, dict):
        return None
    ticker = row.get("ticker")
    if not ticker:
        return None
    return Market(
        ticker=str(ticker),
        title=str(row.get("title") or row.get("subtitle") or ticker),
        close_time=str(row.get("close_time") or row.get("expected_expiration_time") or "") or None,
    )
