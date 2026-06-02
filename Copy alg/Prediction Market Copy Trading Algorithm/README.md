# Kalshi Virtual Trader

Python event-driven strategy engine for monitoring Kalshi market data and
running a copy-trading style strategy in virtual execution mode.

## Features

- Live or replay event ingestion
- Config-driven strategy thresholds
- Virtual position lifecycle with stops and take-profit
- SQLite event persistence
- Session reporting
- Deterministic tests built with `unittest`

## Quick Start

1. Install dependencies:

```bash
python3 -m pip install -e .
```

2. Copy or edit the default config:

```bash
cp config/default.toml config/local.toml
```

3. Run in replay mode:

```bash
python3 -m kalshi_algo.cli replay-session --config config/default.toml --db session.db --input sample_events.jsonl
```

4. Run the live market-data engine:

```bash
export KALSHI_ENV=prod
export KALSHI_API_KEY_ID=your_key_id
export KALSHI_PRIVATE_KEY_PATH=/path/to/private_key.pem
python3 -m kalshi_algo.cli run-demo-trader --config config/default.toml --db session.db
```

The v1 engine never places live orders. It only emits and persists virtual
actions such as `ENTER`, `EXIT`, and `HALT`.
