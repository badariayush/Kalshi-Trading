from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from kalshi_crypto.execution import SafetyError


BTC_15M_SERIES_TICKER = "KXBTC15M"
KALSHI_PUBLIC_REST_URL = "https://external-api.kalshi.com/trade-api/v2"
EASTERN = ZoneInfo("America/New_York")
JsonFetcher = Callable[[str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class KalshiMarketWindow:
    ticker: str
    event_ticker: str
    status: str
    result: str | None
    open_time_ms: int
    close_time_ms: int
    strike: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Btc15mDiscovery:
    current_market: KalshiMarketWindow | None
    next_market: KalshiMarketWindow


def discover_next_btc_15m_market(
    *,
    now_ms: int,
    fetch_json: JsonFetcher = None,
) -> Btc15mDiscovery:
    fetcher = fetch_json or fetch_public_json
    current_market = _current_active_btc_15m_market(fetcher)
    if current_market is not None and current_market.close_time_ms > now_ms:
        next_market = _verified_btc_15m_market_after(current_market, fetcher)
        return Btc15mDiscovery(
            current_market=current_market,
            next_market=next_market,
        )

    next_market = _nearest_future_btc_15m_market(now_ms, fetcher)
    return Btc15mDiscovery(current_market=current_market, next_market=next_market)


def fetch_public_json(url: str) -> Mapping[str, Any]:
    with urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise SafetyError("Kalshi public API returned a non-object payload")
    return dict(payload)


def fetch_market_result(
    *,
    market: KalshiMarketWindow,
    fetch_json: JsonFetcher = None,
) -> str | None:
    fetcher = fetch_json or fetch_public_json
    url = (
        f"{KALSHI_PUBLIC_REST_URL}/events/"
        f"{quote(market.event_ticker, safe='')}"
        "?with_nested_markets=true"
    )
    for candidate in _markets_from_event_payload(fetcher(url)):
        if candidate.ticker != market.ticker:
            continue
        result = candidate.result
        if result in {"yes", "no"}:
            return result
    return None


def _current_active_btc_15m_market(
    fetch_json: JsonFetcher,
) -> KalshiMarketWindow | None:
    url = (
        f"{KALSHI_PUBLIC_REST_URL}/events"
        f"?series_ticker={BTC_15M_SERIES_TICKER}"
        "&status=open&with_nested_markets=true&limit=10"
    )
    markets = _markets_from_events_payload(fetch_json(url))
    active_markets = tuple(market for market in markets if market.status == "active")
    if not active_markets:
        return None
    return sorted(active_markets, key=lambda market: market.close_time_ms)[0]


def _nearest_future_btc_15m_market(
    now_ms: int,
    fetch_json: JsonFetcher,
) -> KalshiMarketWindow:
    url = (
        f"{KALSHI_PUBLIC_REST_URL}/events"
        f"?series_ticker={BTC_15M_SERIES_TICKER}"
        "&with_nested_markets=true&limit=200"
    )
    markets = _markets_from_events_payload(fetch_json(url))
    future_markets = tuple(
        market
        for market in markets
        if market.open_time_ms > now_ms and market.status in {"initialized", "active"}
    )
    if not future_markets:
        raise SafetyError("could not find a future BTC 15m Kalshi market")
    return sorted(future_markets, key=lambda market: market.open_time_ms)[0]


def _verified_btc_15m_market_after(
    market: KalshiMarketWindow,
    fetch_json: JsonFetcher,
) -> KalshiMarketWindow:
    next_close_time = datetime.fromtimestamp(
        market.close_time_ms / 1000,
        tz=timezone.utc,
    ) + timedelta(minutes=15)
    event_ticker = btc_15m_event_ticker_for_close(next_close_time)
    url = (
        f"{KALSHI_PUBLIC_REST_URL}/events/"
        f"{quote(event_ticker, safe='')}"
        "?with_nested_markets=true"
    )
    markets = _markets_from_event_payload(fetch_json(url))
    if not markets:
        raise SafetyError(f"Kalshi event {event_ticker} has no BTC 15m market")
    return sorted(markets, key=lambda item: item.close_time_ms)[0]


def btc_15m_event_ticker_for_close(close_time: datetime) -> str:
    eastern_close = close_time.astimezone(EASTERN)
    return f"{BTC_15M_SERIES_TICKER}-{eastern_close:%y%b%d%H%M}".upper()


def _markets_from_events_payload(
    payload: Mapping[str, Any],
) -> tuple[KalshiMarketWindow, ...]:
    events = payload.get("events")
    if not isinstance(events, list):
        return ()
    markets: list[KalshiMarketWindow] = []
    for event in events:
        if isinstance(event, Mapping):
            markets.extend(_markets_from_event_mapping(event))
    return tuple(markets)


def _markets_from_event_payload(
    payload: Mapping[str, Any],
) -> tuple[KalshiMarketWindow, ...]:
    event = payload.get("event")
    if not isinstance(event, Mapping):
        return ()
    return tuple(_markets_from_event_mapping(event))


def _markets_from_event_mapping(event: Mapping[str, Any]) -> list[KalshiMarketWindow]:
    raw_markets = event.get("markets")
    if not isinstance(raw_markets, list):
        return []
    markets: list[KalshiMarketWindow] = []
    for raw_market in raw_markets:
        if not isinstance(raw_market, Mapping):
            continue
        parsed = _market_from_mapping(raw_market)
        if parsed is not None:
            markets.append(parsed)
    return markets


def _market_from_mapping(market: Mapping[str, Any]) -> KalshiMarketWindow | None:
    ticker = _str_value(market.get("ticker"))
    event_ticker = _str_value(market.get("event_ticker"))
    status = _str_value(market.get("status"))
    result = _normalized_result(market.get("result"))
    open_time_ms = _timestamp_ms(market.get("open_time"))
    close_time_ms = _timestamp_ms(market.get("close_time"))
    strike = _strike_value(market)
    if (
        ticker is None
        or event_ticker is None
        or status is None
        or open_time_ms is None
        or close_time_ms is None
    ):
        return None
    return KalshiMarketWindow(
        ticker=ticker,
        event_ticker=event_ticker,
        status=status,
        result=result,
        open_time_ms=open_time_ms,
        close_time_ms=close_time_ms,
        strike=strike,
    )


def _timestamp_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)


def _str_value(value: object) -> str | None:
    if value is None:
        return None
    parsed = str(value)
    if not parsed:
        return None
    return parsed


def _normalized_result(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"yes", "y"}:
        return "yes"
    if normalized in {"no", "n"}:
        return "no"
    return None


def _strike_value(market: Mapping[str, Any]) -> Decimal | None:
    for key in ("floor_strike", "functional_strike"):
        strike = _decimal_strike_value(market.get(key))
        if strike is not None:
            return strike
    custom_strike = market.get("custom_strike")
    if isinstance(custom_strike, Mapping):
        for key in ("floor_strike", "functional_strike"):
            strike = _decimal_strike_value(custom_strike.get(key))
            if strike is not None:
                return strike
    return None


def _decimal_strike_value(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        strike = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return strike if strike > Decimal("0") else None
