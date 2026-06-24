from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from kalshi_crypto.config import RuntimeMode


class SafetyError(RuntimeError):
    pass


class ExecutionBackend(str, Enum):
    LIVE_DATA = "live_data"
    DIRECT_KALSHI = "direct_kalshi"
    TRADE_MCP = "trade_mcp"


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    mode: RuntimeMode
    backend: ExecutionBackend
    confirm_live: bool = False


def validate_execution_intent(intent: ExecutionIntent) -> None:
    if intent.backend is ExecutionBackend.TRADE_MCP:
        raise SafetyError(
            "trade execution MCP is disabled pending security/risk review and operator approval"
        )

    if intent.mode is RuntimeMode.LIVE and not intent.confirm_live:
        raise SafetyError("live execution requires confirm_live")
