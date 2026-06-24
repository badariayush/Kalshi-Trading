from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kalshi_crypto.events import AuditEvent

REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "private_key",
    "secret",
    "signature",
    "password",
    "token",
)


class JsonlAuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = _redact(event.to_dict())
        with self.path.open("a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            audit_file.write("\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as audit_file:
            for line in audit_file:
                if line.strip():
                    records.append(json.loads(line))
        return records


def redacted_event_dict(event: AuditEvent) -> dict[str, Any]:
    return _redact(event.to_dict())


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)
