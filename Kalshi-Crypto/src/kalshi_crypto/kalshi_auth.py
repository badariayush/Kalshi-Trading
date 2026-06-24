from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


SignatureProvider = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class KalshiAuthConfig:
    key_id_env: str = "KALSHI_KEY_ID"
    private_key_pem_env: str = "KALSHI_PRIVATE_KEY_PEM"

    def __post_init__(self) -> None:
        if not self.key_id_env:
            raise ValueError("key_id_env is required")
        if not self.private_key_pem_env:
            raise ValueError("private_key_pem_env is required")

    def required_env_vars(self) -> tuple[str, str]:
        return self.key_id_env, self.private_key_pem_env


@dataclass(frozen=True, slots=True)
class KalshiAuthHeaders:
    key_id: str
    timestamp_ms: int
    signature: str

    def as_mapping(self) -> Mapping[str, str]:
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(self.timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": self.signature,
        }


def kalshi_signature_message(
    *,
    timestamp_ms: int,
    method: str,
    path: str,
) -> str:
    if timestamp_ms < 0:
        raise ValueError("timestamp_ms must be non-negative")
    if not method:
        raise ValueError("method is required")
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    return f"{timestamp_ms}{method.upper()}{path}"


def build_kalshi_auth_headers(
    *,
    key_id: str,
    timestamp_ms: int,
    method: str,
    path: str,
    signer: SignatureProvider,
) -> KalshiAuthHeaders:
    if not key_id:
        raise ValueError("key_id is required")
    message = kalshi_signature_message(
        timestamp_ms=timestamp_ms,
        method=method,
        path=path,
    )
    signature = signer(message)
    if not signature:
        raise ValueError("signature provider returned an empty signature")
    return KalshiAuthHeaders(
        key_id=key_id,
        timestamp_ms=timestamp_ms,
        signature=signature,
    )
