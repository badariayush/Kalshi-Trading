from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
import asyncio
import json
import os


@dataclass(slots=True)
class KalshiCredentials:
    api_key_id: str
    private_key_path: str
    environment: str


class KalshiClient:
    def __init__(self, credentials: KalshiCredentials | None = None):
        self.credentials = credentials
        self._private_key: Any | None = None

    @staticmethod
    def from_env() -> "KalshiClient":
        key_id = os.getenv("KALSHI_API_KEY_ID", "")
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
        environment = os.getenv("KALSHI_ENV", "prod")
        credentials = KalshiCredentials(key_id, key_path, environment)
        return KalshiClient(credentials=credentials)

    async def stream_events(
        self,
        reconnect: bool = False,
        initial_delay_seconds: float = 5.0,
        max_delay_seconds: float = 60.0,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.credentials or not self.credentials.api_key_id:
            raise RuntimeError("Kalshi credentials are required for live streaming.")
        if not self.credentials.private_key_path:
            raise RuntimeError("KALSHI_PRIVATE_KEY_PATH is required for live streaming.")
        import websockets

        uri = self._ws_uri()
        delay = initial_delay_seconds
        while True:
            try:
                yield {
                    "type": "connection_status",
                    "msg": {"status": "connecting"},
                    "received_at": datetime.now(UTC).isoformat(),
                }
                async with websockets.connect(
                    uri,
                    ping_interval=10,
                    ping_timeout=10,
                    additional_headers=self._auth_headers("GET", "/trade-api/ws/v2"),
                ) as websocket:
                    await websocket.send(json.dumps(self._subscription_message()))
                    delay = initial_delay_seconds
                    yield {
                        "type": "connection_status",
                        "msg": {"status": "connected"},
                        "received_at": datetime.now(UTC).isoformat(),
                    }
                    async for raw in websocket:
                        payload = json.loads(raw)
                        payload.setdefault("received_at", datetime.now(UTC).isoformat())
                        yield payload
            except Exception as exc:
                if not reconnect:
                    raise
                yield {
                    "type": "connection_status",
                    "msg": {
                        "status": "reconnecting",
                        "error": exc.__class__.__name__,
                        "delay_seconds": delay,
                    },
                    "received_at": datetime.now(UTC).isoformat(),
                }
                await asyncio.sleep(delay)
                delay = min(max_delay_seconds, delay * 2)

    async def backfill_markets(self) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        return []

    def _ws_uri(self) -> str:
        env = (self.credentials.environment or "prod").lower()
        if env == "demo":
            return "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
        return "wss://external-api-ws.kalshi.com/trade-api/ws/v2"

    def _subscription_message(self) -> dict[str, Any]:
        return {
            "id": 1,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker", "trade"],
            },
        }

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        timestamp_ms = str(int(datetime.now(UTC).timestamp() * 1000))
        message = timestamp_ms + method.upper() + path.split("?", 1)[0]
        signature = self._sign_pss_text(message)
        return {
            "KALSHI-ACCESS-KEY": self.credentials.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }

    def _sign_pss_text(self, text: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = self._load_private_key()
        signature = private_key.sign(
            text.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return b64encode(signature).decode("utf-8")

    def _load_private_key(self) -> Any:
        from cryptography.hazmat.primitives import serialization

        if self._private_key is not None:
            return self._private_key
        with open(self.credentials.private_key_path, "rb") as key_file:
            self._private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
            )
        return self._private_key
