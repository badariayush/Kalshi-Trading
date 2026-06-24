from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from kalshi_crypto.events import AuditEvent

_LIFECYCLE_STATUSES: Mapping[str, str] = MappingProxyType(
    {
        "WindowDiscovered": "discovered",
        "WindowOpened": "open",
        "WindowClosingSoon": "closing",
        "WindowClosed": "closed",
        "SettlementPending": "settlement_pending",
    }
)
_CURRENT_STATUSES = frozenset({"open", "closing"})
_TERMINAL_STATUSES = frozenset({"closed", "settlement_pending"})


@dataclass(frozen=True, slots=True)
class MarketWindow:
    market_ticker: str
    series_ticker: str
    underlying: str
    strike: str
    open_timestamp_ms: int
    close_timestamp_ms: int
    status: str
    last_event_timestamp_ms: int

    def __post_init__(self) -> None:
        if not self.market_ticker:
            raise ValueError("market_ticker is required")
        if not self.series_ticker:
            raise ValueError("series_ticker is required")
        if not self.underlying:
            raise ValueError("underlying is required")
        if not self.strike:
            raise ValueError("strike is required")
        if self.open_timestamp_ms < 0:
            raise ValueError("open_timestamp_ms must be non-negative")
        if self.close_timestamp_ms <= self.open_timestamp_ms:
            raise ValueError("close_timestamp_ms must be after open_timestamp_ms")
        if not self.status:
            raise ValueError("status is required")
        if self.last_event_timestamp_ms < 0:
            raise ValueError("last_event_timestamp_ms must be non-negative")

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def is_current(self, underlying: str, now_ms: int) -> bool:
        return (
            self.underlying == underlying.strip().upper()
            and self.status in _CURRENT_STATUSES
            and self.open_timestamp_ms <= now_ms < self.close_timestamp_ms
        )

    def is_next_candidate(self, underlying: str, now_ms: int) -> bool:
        return (
            self.underlying == underlying.strip().upper()
            and not self.is_terminal
            and self.open_timestamp_ms > now_ms
        )


@dataclass(frozen=True, slots=True)
class MarketWindowRegistry:
    _windows: Mapping[str, MarketWindow]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_windows", MappingProxyType(dict(self._windows)))

    @classmethod
    def empty(cls) -> "MarketWindowRegistry":
        return cls({})

    def apply_all(self, events: Iterable[AuditEvent]) -> "MarketWindowRegistry":
        registry = self
        for event in events:
            registry = registry.apply(event)
        return registry

    def apply(self, event: AuditEvent) -> "MarketWindowRegistry":
        status = _LIFECYCLE_STATUSES.get(event.event_type)
        if status is None:
            return self

        window = _window_from_event(event, status)
        windows = dict(self._windows)
        windows[window.market_ticker] = window
        return MarketWindowRegistry(windows)

    def window(self, market_ticker: str) -> MarketWindow | None:
        return self._windows.get(market_ticker)

    def windows(self) -> tuple[MarketWindow, ...]:
        return tuple(
            sorted(
                self._windows.values(),
                key=lambda window: (
                    window.underlying,
                    window.open_timestamp_ms,
                    window.close_timestamp_ms,
                    window.market_ticker,
                ),
            )
        )

    def underlyings(self) -> tuple[str, ...]:
        return tuple(sorted({window.underlying for window in self._windows.values()}))

    def current_windows(self, underlying: str, now_ms: int) -> tuple[MarketWindow, ...]:
        _validate_timestamp("now_ms", now_ms)
        return tuple(
            sorted(
                (
                    window
                    for window in self._windows.values()
                    if window.is_current(underlying, now_ms)
                ),
                key=lambda window: (
                    window.close_timestamp_ms,
                    window.open_timestamp_ms,
                    window.market_ticker,
                ),
            )
        )

    def next_window(self, underlying: str, now_ms: int) -> MarketWindow | None:
        _validate_timestamp("now_ms", now_ms)
        candidates = sorted(
            (
                window
                for window in self._windows.values()
                if window.is_next_candidate(underlying, now_ms)
            ),
            key=lambda window: (
                window.open_timestamp_ms,
                window.close_timestamp_ms,
                window.market_ticker,
            ),
        )
        return candidates[0] if candidates else None


def _window_from_event(event: AuditEvent, status: str) -> MarketWindow:
    payload = event.payload
    return MarketWindow(
        market_ticker=_required_str(payload, "market_ticker"),
        series_ticker=_required_str(payload, "series_ticker"),
        underlying=_required_str(payload, "underlying").upper(),
        strike=_required_str(payload, "strike"),
        open_timestamp_ms=_required_int(payload, "open_timestamp_ms"),
        close_timestamp_ms=_required_int(payload, "close_timestamp_ms"),
        status=status,
        last_event_timestamp_ms=event.timestamp_ms,
    )


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} is required")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} is required")
    _validate_timestamp(key, value)
    return value


def _validate_timestamp(key: str, value: int) -> None:
    if value < 0:
        raise ValueError(f"{key} must be non-negative")
