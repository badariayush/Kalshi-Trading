# skills.md

Repo-wide persistent memory for the autonomous Kalshi crypto-market trading system.

## Project Overview

This project will build an autonomous system for Kalshi short-duration BTC and ETH crypto markets. The current operator-facing product captures and audits real Kalshi/Coinbase live WebSocket data only. It prints real-time simulated entries and exits and reports testing-only PnL from live quotes and actual Kalshi outcomes. Account order submission remains disabled until a separate execution-capable implementation is explicitly approved.

## Tech Stack

Planned stack:

- Python 3.11+
- `Decimal` for all price, fee, P&L, and margin math
- `unittest` plus property-based tests once implementation begins
- `requests` or `httpx` for REST
- `websockets` for streaming feeds
- `cryptography` for Kalshi RSA-PSS signing
- SQLite for initial live-data audit state
- TOML or YAML configs, validated at startup

Existing local experiments already use Python 3.11, `unittest`, `Decimal`, `requests`, `websockets`, `cryptography`, TOML configs, and structured JSON logs.

## Architecture Summary

Worker boundaries:

- Market Monitor Worker: tracks BTC/ETH Kalshi windows, strikes, close times, and lifecycle events.
- Signal Worker: computes probability, confidence, and reasoning; never sizes or trades.
- Risk & Position-Sizing Worker: decides entry permission, size, kill-switch state, circuit breakers, time-aware fallback actions, and asymmetric partial-hedge sizing from configured max-loss targets.
- Arbitrage/Hedge Margin Worker: evaluates the true-arbitrage formula and is the only authority for Mechanism 1 authorization.
- Execution Worker: places, cancels, modifies, and reconciles Kalshi orders after authorization.
- Logging/Audit Worker: records every event needed for reporting and review.

Initial communication mechanism: one Python process with an in-process async event bus and immutable typed event payloads. The boundaries must remain clean enough to split into separate processes later.

## Commands

Current safe implementation state:

```bash
git status --short
git diff -- ARCHITECTURE.md PLAN.md AGENTS.md skills.md workers src tests configs pyproject.toml
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m kalshi_crypto.cli doctor-live-data --config configs/live.example.toml
PYTHONPATH=src python -m kalshi_crypto.cli live-data --config configs/live.example.toml --kalshi-market-ticker <current-real-kalshi-market> --audit-db <tmp.sqlite3> --max-seconds 600
PYTHONPATH=src python -m kalshi_crypto.cli report --audit-db <tmp.sqlite3>
```

Planned commands not yet implemented:

```bash
python -m kalshi_crypto.cli report --since 7d
```

Do not reintroduce non-live operator runs.
Operator-facing run/report commands must use live WebSocket data only.

## Editing Rules

- Read this file and the relevant worker `skills.md` before editing.
- Make the smallest change that satisfies the task.
- Do not mix unrelated refactors into safety-critical trading changes.
- Do not add a dependency without documenting why a standard-library or existing dependency is insufficient.
- Keep formulas, risk thresholds, provider URLs, and mode flags in validated config, not hardcoded in business logic.
- Use structured parsers and typed models for provider responses.
- Preserve live-data versus execution-capable safety boundaries.
- Update this file or a worker `skills.md` after learning a durable lesson.

## Dangerous Areas

Changes to these areas require security/risk review:

- True-arbitrage formula: `Locked_in_cost(t) <= 1.00 - min_arb_margin`.
- Asymmetric partial-hedge trigger, max-loss target, and integer contract sizing.
- Fee model and fee rounding.
- Kalshi RSA-PSS auth and API credentials.
- API key file paths and environment variables.
- Kill switch and circuit-breaker logic.
- Live-data/execution mode flag. Default must remain live data with order submission disabled.
- Order submission, cancellation, time-in-force, `buy_max_cost`, and fill reconciliation.
- Settlement-source mapping to CF Benchmarks.
- Feed freshness and stale-book checks.
- BTC/ETH correlated exposure caps.

## Self-Improvement Rule

Whenever any agent fixes a bug, makes an architectural decision, learns something about Kalshi's API/fee behavior, or gets corrected by the operator, it must update the relevant `skills.md` file before considering the task done. Lessons that apply repo-wide go in the root `skills.md`. Lessons specific to one worker go in that worker's directory-level `skills.md`. Do not leave important knowledge only in the chat transcript - the repo is the memory, not the conversation.

## Recent Lessons

- The true-arbitrage watcher and asymmetric partial hedge are separate mechanisms. Losing on the original side does not create arbitrage on demand; it often makes the opposing side more expensive. The Arbitrage/Hedge Margin Worker owns only the true-arbitrage locked-cost check, while the Risk Worker owns partial-hedge trigger and max-loss sizing.
- Arbitrage boundary tests must treat `Locked_in_cost(t) == 1.00 - min_arb_margin` as authorized and `Locked_in_cost(t) > 1.00 - min_arb_margin` as rejected. Recompute fees at the actual opposing ask in each scenario; do not reuse a fee from another example price.
- Root config loading now uses TOML via the standard library. Commit only `*.example.toml`; keep operator-specific `configs/live.toml` and `configs/*.local.toml` ignored.
- Kalshi order book normalization is local and pure: YES ask is derived from the best NO bid as `1 - no_bid`, and NO ask is derived from the best YES bid as `1 - yes_bid`. Missing opposite bid depth means the corresponding executable ask is unavailable, not zero.
- Signal Worker v1 is informational only. It emits feature snapshots, `probability_yes`, confidence, and skip reasons; it must not emit side, size, entry, order intent, or execution instructions. It now includes live-data-supported market-structure and order-flow concepts: trend, BOS-style structure break, liquidity sweep, VWAP proxy, volume-profile POC proxy, and signed order-flow delta proxy. Open interest, funding, liquidation maps, footprint/DOM, and on-chain metrics require real live providers before they can influence probability; do not synthesize them.
- Live data/order readiness should be enabled without execution: committed configs document Kalshi/Coinbase endpoints with `live_data.enable_live_network = true` and `order_api.enable_order_api = true`, but keep `order_api.allow_order_submission = false`. The `doctor-live-data` command validates readiness without opening sockets, calling REST, reading credentials, or submitting orders.
- Kalshi request signing uses `KALSHI-ACCESS-*` header metadata and signs `timestamp + HTTP method + path`; signing is injected for tests so private key material is never needed in unit tests.
- Kalshi live auth should match the other crypto algorithm's file-path export style: use `KALSHI_API_KEY_ID` for the key id and `KALSHI_PRIVATE_KEY_PATH` for the local PEM/key file path. `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY_PEM` may remain as fallback compatibility only, not the primary run instructions.
- The `report` CLI summarizes only live-data SQLite audit stores with event counts, feed-unhealthy counts, execution failures, simulated order-placement counts, simulated closed-position counts, simulated testing PnL, and a status. During testing, the auto BTC 15m runner may append simulated `PositionClosed` events after Kalshi posts YES/NO results; this must be removed or replaced before real execution is enabled.
- The operator-facing CLI is live-only. The `live-data` command requires an actual Kalshi market ticker and captures live Kalshi/Coinbase WebSocket messages.
- The `report` CLI only accepts SQLite audit stores produced by `live-data`; it refuses non-live audit stores.
- The `live-data` CLI audits Kalshi/Coinbase WebSocket-shaped messages into SQLite with feed-health records and can attempt a guarded WebSocket capture when credentials are supplied. It may print and audit `SimulatedOrderPlaced` when live feed data provides an executable Kalshi price and Coinbase crypto price. It must keep `execution=not_attempted` and `order_submission=disabled`; live WebSocket data is enabled, final account order submission is not.
- The auto BTC 15m runner must skip the currently active Kalshi market at startup, discover the next BTC 15m ticker from Kalshi public metadata, wait for that next market, and prepare the following ticker before/while the current target is handled. It must not start a simulated order attempt when a market has 10 minutes or less to resolution. A market may have zero, one, or two simulated entries: leg 1 is one signal/risk-authorized YES or NO entry; leg 2 is optional, must be the opposite side, and requires true-arbitrage or partial-hedge authorization. Never place the same side twice or create a third leg. During testing only, poll public Kalshi result metadata and settle all legs together for simulated PnL. BTC market discovery must parse strike from either top-level `floor_strike`/`functional_strike` or nested `custom_strike`; if strike is missing, print `auto_paper_strategy_disabled=... reason=missing_market_strike` instead of silently running capture without simulated decisions.
- BTC auto paper strategy inputs must filter Coinbase to `BTC-USD` even if other products are present in a provider payload. Do not select the most recently received Coinbase product without matching it to the Kalshi market underlying.
- Current Kalshi WebSocket configs should prefer `wss://external-api-ws.kalshi.com/trade-api/ws/v2`; the old `api.elections` WebSocket host is legacy compatibility, not the committed default.
- Use `live-data --output-file <capture.jsonl>` only to preserve actual live captured provider messages for debugging and audit review.
- Paper decisions run incrementally as healthy WebSocket ticker updates arrive;
  never wait until the capture finishes to print an entry or exit. Persist each
  simulated action exactly once so restart recovery cannot duplicate PnL.
- New entries stop at ten minutes to resolution, while positions entered earlier
  remain managed through the rest of the market. The auto runner should monitor
  the full 15-minute window (`--market-capture-seconds 900`).
- Paper fills are conservative models over real quotes: buys use executable ask
  plus slippage, sells use executable bid minus slippage, and both sides include
  modeled fees. This is not proof of actual fillability.
- Feed-health and clock-skew checks gate real-time strategy input when the
  configured circuit breaker is enabled. Unhealthy messages remain audited.
- Kalshi WebSocket provider timestamps can legitimately arrive slightly ahead of
  local receive timestamps; one observed live ticker was 1,106 ms in the future.
  Treat this as provider/local jitter, not failed feed health, when it is within
  validated `circuit_breakers.future_clock_skew_tolerance_ms` (currently 1,500
  ms). Do not disable stale-feed protection; genuinely larger future skew still
  rejects the record before it can enter real-time paper strategy state.
- Kalshi ticker `ts` can arrive as Unix seconds, while other timestamp fields are
  milliseconds. Normalize numeric `ts` values below `10_000_000_000` to
  milliseconds before feed-health evaluation; otherwise every ticker appears
  decades stale and the real-time strategy receives no Kalshi books.
- Restart recovery reconstructs unresolved paper positions only from persisted
  simulated orders carrying market event/open/close/strike metadata, and excludes
  any market already represented by a `PositionClosed` event.
