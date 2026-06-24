from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeedHealthStatus:
    source: str
    healthy: bool
    reason: str
    age_ms: int


@dataclass(frozen=True, slots=True)
class FeedHealthMonitor:
    max_stale_ms: int

    def __post_init__(self) -> None:
        if self.max_stale_ms <= 0:
            raise ValueError("max_stale_ms must be positive")

    def evaluate(
        self,
        source: str,
        source_timestamp_ms: int,
        received_timestamp_ms: int,
    ) -> FeedHealthStatus:
        if not source:
            raise ValueError("source is required")
        if source_timestamp_ms < 0:
            raise ValueError("source_timestamp_ms must be non-negative")
        if received_timestamp_ms < source_timestamp_ms:
            raise ValueError("received_timestamp_ms must be at or after source_timestamp_ms")

        age_ms = received_timestamp_ms - source_timestamp_ms
        healthy = age_ms <= self.max_stale_ms
        return FeedHealthStatus(
            source=source,
            healthy=healthy,
            reason="fresh" if healthy else "stale",
            age_ms=age_ms,
        )
