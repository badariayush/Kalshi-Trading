from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import json
from typing import Any

RuntimeLogger = Callable[[str, str, str, dict[str, object] | None], None]


async def monitor_polymarket_market_ws(
    asset_ids: list[str],
    log: RuntimeLogger,
    stop: asyncio.Event,
    initial_delay_seconds: float = 2.0,
    max_delay_seconds: float = 30.0,
) -> None:
    if not asset_ids:
        log("websocket", "info", "polymarket websocket skipped: no asset ids", None)
        return
    await _reconnecting_ws(
        name="polymarket",
        uri="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        subscription={"assets_ids": asset_ids, "type": "market"},
        headers=None,
        log=log,
        stop=stop,
        initial_delay_seconds=initial_delay_seconds,
        max_delay_seconds=max_delay_seconds,
    )


async def monitor_kalshi_market_ws(
    tickers: list[str],
    log: RuntimeLogger,
    stop: asyncio.Event,
    headers_factory: Callable[[], dict[str, str]] | None = None,
    initial_delay_seconds: float = 2.0,
    max_delay_seconds: float = 30.0,
) -> None:
    if not tickers:
        log("websocket", "info", "kalshi websocket skipped: no tickers", None)
        return
    if headers_factory is None:
        log("websocket", "warning", "kalshi websocket skipped: auth headers not configured", {"tickers": tickers[:10]})
        return
    await _reconnecting_ws(
        name="kalshi",
        uri="wss://external-api-ws.kalshi.com/trade-api/ws/v2",
        subscription={
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["orderbook_delta", "ticker"],
                "market_tickers": tickers,
            },
        },
        headers=headers_factory,
        log=log,
        stop=stop,
        initial_delay_seconds=initial_delay_seconds,
        max_delay_seconds=max_delay_seconds,
    )


async def _reconnecting_ws(
    name: str,
    uri: str,
    subscription: dict[str, object],
    headers: Callable[[], dict[str, str]] | None,
    log: RuntimeLogger,
    stop: asyncio.Event,
    initial_delay_seconds: float,
    max_delay_seconds: float,
) -> None:
    import websockets

    delay = initial_delay_seconds
    while not stop.is_set():
        try:
            log("websocket", "info", f"{name} websocket connecting", None)
            connect_kwargs: dict[str, Any] = {"ping_interval": 10, "ping_timeout": 10}
            if headers is not None:
                connect_kwargs["additional_headers"] = headers()
            async with websockets.connect(uri, **connect_kwargs) as websocket:
                await websocket.send(json.dumps(subscription))
                log("websocket", "info", f"{name} websocket connected", {"subscription": _safe_subscription(subscription)})
                delay = initial_delay_seconds
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    except TimeoutError:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        payload = {"raw": str(raw)[:500]}
                    log("websocket_message", "debug", f"{name} websocket message", {"payload": _truncate_payload(payload)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(
                "websocket",
                "warning",
                f"{name} websocket reconnecting after {exc.__class__.__name__}",
                {"delay_seconds": delay, "error": str(exc)[:300]},
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(max_delay_seconds, delay * 2)


def _truncate_payload(payload: object) -> object:
    text = json.dumps(payload, sort_keys=True, default=str)
    if len(text) <= 1000:
        return payload
    return {"truncated_json": text[:1000]}


def _safe_subscription(subscription: dict[str, object]) -> dict[str, object]:
    safe = dict(subscription)
    params = safe.get("params")
    if isinstance(params, dict) and isinstance(params.get("market_tickers"), list):
        safe["params"] = {**params, "market_tickers": params["market_tickers"][:10]}
    if isinstance(safe.get("assets_ids"), list):
        safe["assets_ids"] = safe["assets_ids"][:10]
    return safe
