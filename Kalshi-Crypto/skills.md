# skills.md

Repo-wide persistent memory for the autonomous Kalshi crypto-market trading system.

## Project Overview

This project will build an autonomous system for Kalshi short-duration BTC and ETH crypto markets. It watches fixed windows, scores directional probability, enters one side when risk allows, and manages the position through take-profit, true arbitrage, asymmetric partial hedge, or settlement. The system must default to paper trading, enforce hard risk limits, and log enough data to reconstruct every trade. It must never imply that a partial hedge guarantees profit; true arbitrage is opportunistic and may never become executable.

## Tech Stack

Planned stack:

- Python 3.11+
- `Decimal` for all price, fee, P&L, and margin math
- `unittest` plus property-based tests once implementation begins
- `requests` or `httpx` for REST
- `websockets` for streaming feeds
- `cryptography` for Kalshi RSA-PSS signing
- SQLite for initial paper/audit state
- TOML or YAML configs, validated at startup

Existing local experiments already use Python 3.11, `unittest`, `Decimal`, `requests`, `websockets`, `cryptography`, TOML configs, paper mode, and structured JSON logs.

## Architecture Summary

Worker boundaries:

- Market Monitor Worker: tracks BTC/ETH Kalshi windows, strikes, close times, and lifecycle events.
- Signal Worker: computes probability, confidence, and reasoning; never sizes or trades.
- Risk & Position-Sizing Worker: decides entry permission, size, kill-switch state, circuit breakers, time-aware fallback actions, and asymmetric partial-hedge sizing from configured max-loss targets.
- Arbitrage/Hedge Margin Worker: evaluates the true-arbitrage formula and is the only authority for Mechanism 1 authorization.
- Execution Worker: places, cancels, modifies, and reconciles Kalshi orders after authorization.
- Logging/Audit Worker: records every event needed for replay, reporting, and review.

Initial communication mechanism: one Python process with an in-process async event bus and immutable typed event payloads. The boundaries must remain clean enough to split into separate processes later.

## Commands

Current safe implementation state:

```bash
git status --short
git diff -- ARCHITECTURE.md PLAN.md AGENTS.md skills.md workers src tests configs pyproject.toml
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m kalshi_crypto.cli doctor --config configs/paper.example.toml
PYTHONPATH=src python -m kalshi_crypto.cli data-only --config configs/paper.example.toml --replay-file <local.jsonl> --audit-db <tmp.sqlite3>
PYTHONPATH=src python -m kalshi_crypto.cli paper --config configs/paper.example.toml --audit-db <tmp.sqlite3> --max-seconds 600
PYTHONPATH=src python -m kalshi_crypto.cli report --audit-db <tmp.sqlite3>
```

Planned commands not yet implemented:

```bash
python -m kalshi_crypto.cli backtest --config configs/backtest.example.toml
python -m kalshi_crypto.cli paper --config configs/paper.example.toml
python -m kalshi_crypto.cli paper-demo --config configs/demo.example.toml
python -m kalshi_crypto.cli report --since 7d
```

Do not invent or run live commands until they exist and the operator explicitly approves live mode.

## Editing Rules

- Read this file and the relevant worker `skills.md` before editing.
- Make the smallest change that satisfies the task.
- Do not mix unrelated refactors into safety-critical trading changes.
- Do not add a dependency without documenting why a standard-library or existing dependency is insufficient.
- Keep formulas, risk thresholds, provider URLs, and mode flags in validated config, not hardcoded in business logic.
- Use structured parsers and typed models for provider responses.
- Preserve paper/demo/live safety boundaries.
- Update this file or a worker `skills.md` after learning a durable lesson.

## Dangerous Areas

Changes to these areas require security/risk review:

- True-arbitrage formula: `Locked_in_cost(t) <= 1.00 - min_arb_margin`.
- Asymmetric partial-hedge trigger, max-loss target, and integer contract sizing.
- Fee model and fee rounding.
- Kalshi RSA-PSS auth and API credentials.
- API key file paths and environment variables.
- Kill switch and circuit-breaker logic.
- Live/paper/demo mode flag. Default must remain paper/simulated.
- Order submission, cancellation, time-in-force, `buy_max_cost`, and fill reconciliation.
- Settlement-source mapping to CF Benchmarks.
- Feed freshness and stale-book checks.
- BTC/ETH correlated exposure caps.

## Self-Improvement Rule

Whenever any agent fixes a bug, makes an architectural decision, learns something about Kalshi's API/fee behavior, or gets corrected by the operator, it must update the relevant `skills.md` file before considering the task done. Lessons that apply repo-wide go in the root `skills.md`. Lessons specific to one worker go in that worker's directory-level `skills.md`. Do not leave important knowledge only in the chat transcript - the repo is the memory, not the conversation.

## Recent Lessons

- The true-arbitrage watcher and asymmetric partial hedge are separate mechanisms. Losing on the original side does not create arbitrage on demand; it often makes the opposing side more expensive. The Arbitrage/Hedge Margin Worker owns only the true-arbitrage locked-cost check, while the Risk Worker owns partial-hedge trigger and max-loss sizing.
- Arbitrage boundary tests must treat `Locked_in_cost(t) == 1.00 - min_arb_margin` as authorized and `Locked_in_cost(t) > 1.00 - min_arb_margin` as rejected. Recompute fees at the actual opposing ask in each scenario; do not reuse a fee from another example price.
- Root config loading now uses TOML via the standard library. Commit only `*.example.toml`; keep operator-specific `configs/paper.toml`, `configs/demo.toml`, `configs/live.toml`, and `configs/*.local.toml` ignored.
- Kalshi order book normalization is local and pure: YES ask is derived from the best NO bid as `1 - no_bid`, and NO ask is derived from the best YES bid as `1 - yes_bid`. Missing opposite bid depth means the corresponding executable ask is unavailable, not zero.
- The `data-only` CLI supports local JSONL replay into SQLite audit storage and intentionally does not open live Kalshi REST/WebSocket connections. Live data readiness belongs to `doctor-live-data` and the live feed/orchestration path, while final account order submission remains disabled.
- Local replay supports raw `kalshi_orderbook` records and already-normalized audit events. Raw orderbook replay should emit `OrderBookSnapshotNormalized` and `FeedHealthEvaluated` audit events before the run summary.
- Local replay supports raw `kalshi_market` records for lifecycle testing. These emit `WindowDiscovered` plus the matching lifecycle event such as `WindowOpened`, `WindowClosingSoon`, `WindowClosed`, or `SettlementPending`.
- Local `data-only` replay now projects market lifecycle events into an in-memory market-window registry and prints `current_windows` / `next_windows` ticker summaries. These summaries are reconstructed from local replay data only and do not imply live discovery is enabled.
- Local replay supports raw `cf_benchmark_tick` records for settlement-aligned price data. These emit `CFBenchmarkTickIngested`, `FeedHealthEvaluated`, and locally aggregated `CFBenchmarkCandleClosed` audit events; this is replay-only and does not enable live CF Benchmarks ingestion.
- Signal Worker v1 is informational only. It emits feature snapshots, `probability_yes`, confidence, and skip reasons; it must not emit side, size, entry, order intent, or execution instructions.
- Live data/order integration should be enabled for testable paper operation: committed configs document Kalshi/Coinbase endpoints with `live_data.enable_live_network = true` and `order_api.enable_order_api = true`, but keep `order_api.allow_order_submission = false`. The `doctor-live-data` command validates readiness without opening sockets, calling REST, reading credentials, or submitting orders.
- Kalshi request signing uses `KALSHI-ACCESS-*` header metadata and signs `timestamp + HTTP method + path`; signing is injected for tests so private key material is never needed in unit tests or committed fixtures.
- The testable algorithm should replace only the final real order API call with paper execution. Paper execution prints order/fill/P&L lines and emits audit events while preserving fee-inclusive execution math; all upstream workers should still behave like the real system.
- The `report` CLI summarizes SQLite audit stores with event counts, feed-unhealthy counts, execution failures, closed paper positions, profitable/losing/flat position counts, total realized P&L, and a status of `profit`, `loss`, `warning`, `error`, `flat`, or `no_trades`.
- The `paper` CLI currently runs the worker chain against deterministic paper fixture data: market lifecycle, order book normalization, CF candle signal, entry risk authorization, paper execution, audit storage, and reportable P&L. It is the end-to-end harness for report checks until live WebSocket transport is plugged into the same worker chain.
- The `live-data` CLI audits Kalshi/Coinbase WebSocket-shaped messages into SQLite with feed-health records and can attempt a guarded WebSocket capture when credentials are supplied. It must keep `execution=not_attempted` and `order_submission=disabled`; live WebSocket data is enabled, final order submission is not.
- Current Kalshi WebSocket configs should prefer `wss://external-api-ws.kalshi.com/trade-api/ws/v2`; the old `api.elections` WebSocket host is legacy compatibility, not the committed default.
- The `paper` CLI supports `--live-input-file` for local Kalshi/Coinbase WebSocket-shaped message files. This path derives a Kalshi market window, normalized Kalshi book, and Coinbase candle inputs, then runs the same signal, risk, paper execution, audit, and report path with `network=live_message_file` and `order_submission=disabled`.
- Use `live-data --output-file <capture.jsonl>` to preserve captured provider messages before feeding them to `paper --live-input-file`. Inspect the live-data report first; only continue to paper if feed health and execution failures are clean.
