from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime
from pathlib import Path
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

WS_SIGNING_PATH = "/trade-api/ws/v2"


def websocket_headers_from_env(now_ms: str | None = None) -> dict[str, str]:
    key_id = os.getenv("KALSHI_API_KEY_ID")
    private_key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH")
    if not key_id:
        raise RuntimeError("KALSHI_API_KEY_ID is required for Kalshi WebSocket authentication")
    if not private_key_path:
        raise RuntimeError("KALSHI_PRIVATE_KEY_PATH is required for Kalshi WebSocket authentication")
    return websocket_headers(key_id, Path(private_key_path).expanduser(), now_ms=now_ms)


def websocket_headers(key_id: str, private_key_path: Path, now_ms: str | None = None) -> dict[str, str]:
    timestamp = now_ms or str(int(datetime.now(UTC).timestamp() * 1000))
    message = timestamp + "GET" + WS_SIGNING_PATH
    signature = sign_pss_text(private_key_path, message)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


def sign_pss_text(private_key_path: Path, text: str) -> str:
    private_key = serialization.load_pem_private_key(private_key_path.read_bytes(), password=None)
    signature = private_key.sign(
        text.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return b64encode(signature).decode("utf-8")
