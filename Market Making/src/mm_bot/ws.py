from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from mm_bot.auth import websocket_headers_from_env
from mm_bot.config import AppConfig
from mm_bot.events import emit

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def stream_kalshi_markets(config: AppConfig, tickers: list[str], handler: MessageHandler, stop: asyncio.Event) -> None:
    if not tickers:
        emit("RISK_BLOCK", reason="no_tickers_to_subscribe")
        return
    import websockets

    delay = config.runtime.reconnect_initial_delay_seconds
    while not stop.is_set():
        try:
            headers = websocket_headers_from_env()
            emit("WS_CONNECTING", url=config.kalshi.ws_url, tickers=tickers)
            async with websockets.connect(
                config.kalshi.ws_url,
                additional_headers=headers,
                ping_interval=10,
                ping_timeout=10,
            ) as websocket:
                await websocket.send(json.dumps(_subscription(tickers)))
                emit("WS_CONNECTED", url=config.kalshi.ws_url, tickers=tickers)
                delay = config.runtime.reconnect_initial_delay_seconds
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    except TimeoutError:
                        continue
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        emit("RISK_BLOCK", reason="invalid_ws_json", raw=str(raw)[:300])
                        continue
                    await handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            emit("RISK_BLOCK", reason="websocket_reconnect", error=repr(exc), delay_seconds=delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(config.runtime.reconnect_max_delay_seconds, delay * 2)


def _subscription(tickers: list[str]) -> dict[str, Any]:
    return {
        "id": 1,
        "cmd": "subscribe",
        "params": {
            "channels": ["orderbook_delta", "ticker", "trade"],
            "market_tickers": tickers,
            "use_yes_price": True,
        },
    }
