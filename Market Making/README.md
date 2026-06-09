# Kalshi Market Making

Phase 1 is a WebSocket-driven paper market maker for Kalshi BTC 15-minute markets.
It uses real Kalshi market data, computes quotes from the live order book midpoint,
and prints structured JSON events instead of placing orders.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Kalshi WebSockets require authenticated connection headers, even for public market data.
Set:

```bash
export KALSHI_API_KEY_ID="your-api-key-id"
export KALSHI_PRIVATE_KEY_PATH="/absolute/path/to/ignored/private-key.pem"
```

Do not commit keys. The parent repo ignores `.env`, `*.pem`, `*.key`, `key.txt`, and `secrets/`.

## Commands

```bash
python -m mm_bot.cli discover --config configs/config.example.toml
python -m mm_bot.cli paper --config configs/config.example.toml --max-seconds 300
```

The paper command prints JSON lines such as `MARKET_DISCOVERED`, `WS_CONNECTED`,
`BOOK_UPDATE`, `WOULD_PLACE`, `WOULD_CANCEL`, `PAPER_FILL`, `RISK_BLOCK`, and `HEARTBEAT`.
Phase 1 never sends live order-create or cancel requests.

## Tests

```bash
python -m unittest discover -s tests
```
