"""Kalshi crypto live-data trading system core package.

The package contains live Kalshi/Coinbase WebSocket capture, readiness checks,
audit storage, and live-data reporting. Real account order submission remains
disabled until that final execution boundary is explicitly approved.
"""

__all__ = [
    "__version__",
]

__version__ = "0.1.0"
