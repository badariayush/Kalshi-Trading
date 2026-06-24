from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from uuid import uuid4

JSONValue = str | int | float | bool | None | Mapping[str, "JSONValue"] | tuple["JSONValue", ...]
EventHandler = Callable[["AuditEvent"], None]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: str
    event_type: str
    worker: str
    timestamp_ms: int
    causality_id: str
    payload: Mapping[str, JSONValue]

    @classmethod
    def create(
        cls,
        event_type: str,
        worker: str,
        payload: Mapping[str, Any],
        causality_id: str,
        timestamp_ms: int,
        event_id: str | None = None,
    ) -> "AuditEvent":
        if not event_type:
            raise ValueError("event_type is required")
        if not worker:
            raise ValueError("worker is required")
        if not causality_id:
            raise ValueError("causality_id is required")
        if timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")

        return cls(
            event_id=event_id or str(uuid4()),
            event_type=event_type,
            worker=worker,
            timestamp_ms=timestamp_ms,
            causality_id=causality_id,
            payload=_freeze_mapping(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "worker": self.worker,
            "timestamp_ms": self.timestamp_ms,
            "causality_id": self.causality_id,
            "payload": _thaw(self.payload),
        }


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if not event_type:
            raise ValueError("event_type is required")
        self._handlers[event_type].append(handler)

    def publish(self, event: AuditEvent) -> int:
        handlers = tuple(self._handlers.get(event.event_type, ()))
        for handler in handlers:
            handler(event)
        return len(handlers)


def _freeze_mapping(payload: Mapping[str, Any]) -> Mapping[str, JSONValue]:
    frozen: dict[str, JSONValue] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError("payload keys must be strings")
        frozen[key] = _freeze_value(value)
    return MappingProxyType(frozen)


def _freeze_value(value: Any) -> JSONValue:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise ValueError(f"payload value is not JSON-compatible: {type(value).__name__}")


def _thaw(value: JSONValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
