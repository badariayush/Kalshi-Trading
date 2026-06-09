from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import json
from typing import Any


def emit(event_type: str, **payload: Any) -> None:
    row = {"ts": datetime.now(UTC).isoformat(), "event": event_type, **_jsonable(payload)}
    print(json.dumps(row, sort_keys=True), flush=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value
