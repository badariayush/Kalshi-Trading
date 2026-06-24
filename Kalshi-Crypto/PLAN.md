# Build Plan: Autonomous Kalshi Crypto Markets Trading System

Status: planning draft. This plan intentionally stops before application scaffolding until the operator signs off.

## Delivery Principles

- Paper trading comes before anything that touches real money.
- Every milestone must be independently testable.
- True-arbitrage math and partial-hedge max-loss sizing are safety-critical and receive property-based tests.
- Live trading is not a date-based milestone; it is gated on evidence.
- Any worker contract change updates `ARCHITECTURE.md`, this plan, and the relevant `skills.md`.

## Milestone 1: Research Spike And Final Architecture Lock

Goal: turn the planning draft into an implementation-ready spec.

Tasks:

- Verify Kalshi Developer Agreement/API terms for automated trading.
- Verify current fee schedule, maker-fee applicability for target crypto markets, and direct-member vs non-direct fee rounding.
- Verify exact BTC/ETH market series tickers and settlement fields for 15-minute markets.
- Test access to Kalshi demo credentials and confirm demo REST/WebSocket endpoints.
- Confirm CF Benchmarks index IDs for BTC and ETH, including `BRTI` and `ETHUSD_RTI`.
- Choose on-chain data providers for the pilot and label each metric by expected freshness.
- Decide whether to build an internal read-only MCP adapter for Kalshi research or defer MCP until after direct API implementation.

Acceptance criteria:

- `ARCHITECTURE.md` source links and assumptions are updated.
- Every unresolved item is in one consolidated open-question list.
- The operator has explicitly approved stack, paper mode, provider shortlist, and risk-default philosophy.
- No application code has been written.

Validation:

```bash
git status --short
git diff -- ARCHITECTURE.md PLAN.md AGENTS.md skills.md workers
```

## Milestone 2: Data Pipeline, No Trading Logic

Goal: ingest and persist live data without making trading decisions.

Planned deliverables:

- `market_monitor` worker skeleton.
- Kalshi REST market discovery for BTC/ETH windows.
- Kalshi WebSocket ingestion for order book, ticker, trade, lifecycle, and CF Benchmarks value feed.
- Optional Coinbase/Binance secondary feed adapter for exchange-spot sanity checks.
- On-chain provider adapter for selected secondary metrics.
- SQLite schema for raw events and normalized snapshots.
- Feed freshness and clock-skew monitors.

Acceptance criteria:

- The system can run for at least 8 hours in data-only mode with no order endpoints called.
- Current and next BTC/ETH windows are visible in persisted state.
- Order book snapshots include normalized YES and NO executable asks.
- CF Benchmarks ticks are persisted with source timestamps.
- Feed-health circuit breaker emits `feed_unhealthy` events when a source stalls.
- Unit tests cover normalization of Kalshi order book YES/NO scales.

Validation:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli data-only --config configs/paper.example.toml --max-seconds 300
```

## Milestone 3: Backtesting Harness And Signal Engine v1

Goal: prove that candidate signals are testable before any order simulation.

Planned deliverables:

- Shared feature pipeline used by backtest and live paper mode.
- Candle builder from CF Benchmarks ticks.
- Signal Worker v1 with limited indicators: EMA slope/cross, momentum, realized volatility, distance from strike, Kalshi implied-probability cross-check, and secondary on-chain confirmation.
- Backtest replay runner.
- Historical/proxy data loader with clear labels when full-depth Kalshi history is unavailable.
- Report showing per-signal isolated contribution and blended strategy results.

Acceptance criteria:

- Each signal feature can be enabled/disabled independently.
- Backtest output includes sample size, fees, slippage assumptions, spread/depth filters, and outcome taxonomy.
- No signal can be promoted to live paper unless it has a documented edge or a documented reason to remain as a risk veto only.
- Backtest and live signal generation use the same code path.

Validation:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli backtest --config configs/backtest.example.toml
```

## Milestone 4: Arbitrage Math And Risk Engine

Goal: isolate and prove the safety-critical decision logic before execution.

Planned deliverables:

- Risk & Position-Sizing Worker.
- Arbitrage/Hedge Margin Worker.
- Fee model for Kalshi taker/maker fees and rounding.
- Config schema for risk limits, true-arbitrage margin, partial-hedge trigger, and max-loss target.
- Property-based tests for the true-arbitrage trigger.
- Property-based tests for asymmetric partial-hedge quantity solving.
- Time-aware fallback policy implementation.

Acceptance criteria:

- Tests prove the Arbitrage/Hedge Margin Worker never authorizes Mechanism 1 when `Locked_in_cost > 1.00 - min_arb_margin`.
- Tests cover single-contract fee rounding, 100-contract fee rounding, partial depth, stale books, and time-to-expiry fallbacks for Mechanism 1.
- Tests prove the Risk Worker computes the smallest partial-hedge quantity that caps wrong-side loss at the configured target across trigger prices and opposing ask prices.
- Tests prove Mechanism 2 is inactive until the configured opposing-ask trigger is crossed.
- Tests prove Mechanism 2 is logged as reduced loss, not profit, when the original thesis loses.
- Risk Worker can veto entry for every configured limit.
- Kill switch halts new entries while preserving management of existing positions.
- BTC/ETH correlated exposure cap is enforced.

Validation:

```bash
python -m unittest discover -s tests
```

## Milestone 5: Paper Trading With Simulated Execution

Goal: run the full decision loop against live data, with simulated fills only.

Planned deliverables:

- Execution Worker simulation backend.
- Full event bus integration across all workers.
- Simulated entry, take-profit exit, true arbitrage, partial hedge, and settlement classification.
- Dashboard/report for win/true-arbitrage/reduced-loss/loss paths.
- Structured audit log and replayable event store.

Acceptance criteria:

- Runs continuously for at least 7 calendar days or 300 eligible windows, whichever is longer.
- No production order endpoint is called.
- Every simulated trade has a complete audit chain from window to signal to risk to execution to outcome.
- Feed outages halt new entries within the configured stale threshold.
- Paper results include expected vs simulated slippage and fee assumptions.

Validation:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli paper --config configs/paper.example.toml
python -m kalshi_crypto.cli report --since 7d
```

## Milestone 6: Kalshi Demo Environment

Goal: prove real API order lifecycle in mock-fund demo before production.

Planned deliverables:

- Demo Kalshi execution backend.
- Demo-only API key configuration.
- Order lifecycle handling: submit, cancel, amend/decrease if needed, fill reconciliation, and fee reconciliation.
- Idempotent `client_order_id` generation.
- Rate-limit/backoff handling.

Acceptance criteria:

- Demo orders are placed only against Kalshi demo endpoints.
- Production base URLs are blocked in demo mode.
- Fill reports reconcile actual fees and fill prices.
- Partial fill handling is deterministic and audited.
- Kill switch cancels or blocks orders according to policy.

Validation:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli paper-demo --config configs/demo.example.toml --max-seconds 3600
```

## Milestone 7: Risk Hardening And Security Review

Goal: make the system boring to operate before any live pilot.

Planned deliverables:

- Secret redaction tests.
- Config validation tests.
- Startup checks for mode, endpoint, key scope, and kill switch.
- Operational runbook.
- Security/risk review of all execution-capable paths.
- Alerting for feed staleness, repeated losses, repeated true-arbitrage paths, repeated reduced-loss paths, and API errors.

Acceptance criteria:

- Default mode is still `paper_simulated`.
- No agent or config migration can flip to `live` without explicit operator edit.
- Logs contain no private key material, signatures, or raw secrets.
- Security/risk review has no open CRITICAL or HIGH findings.
- Runbook includes emergency halt and recovery procedures.

Validation:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli doctor --config configs/paper.example.toml
python -m pip-audit
```

## Milestone 8: Live Trading Minimum-Size Pilot

Goal: run with real money only after evidence and explicit approval.

Prerequisites:

- At least 300 eligible paper/demo windows across BTC and ETH.
- At least 14 calendar days of continuous paper/demo operation.
- Feed uptime above 99 percent during active windows.
- No unexplained order lifecycle failures in demo.
- Win/true-arbitrage/reduced-loss/loss breakdown is stable enough to estimate worst-case loss frequency.
- Operator explicitly confirms account size, absolute risk caps, and production credentials.

Acceptance criteria:

- Starts at minimum practical size.
- Daily loss, hourly loss, and consecutive-loss breakers are active.
- Human can halt via kill-switch file without code changes.
- Every live order has a matching audit record and fee reconciliation.
- Live results are compared against paper expectations after each trading day.

Validation:

```bash
python -m kalshi_crypto.cli doctor --config configs/live.example.toml
python -m kalshi_crypto.cli live --config configs/live.example.toml --confirm-live
```

## Milestone 9: Scale-Up

Goal: increase size only when live behavior matches paper expectations.

Scale-up gates:

- Minimum 100 live eligible windows at current size.
- Actual slippage within 150 percent of paper-mode assumption.
- Actual fee reconciliation within modeled bounds.
- Loss-path frequency not materially worse than paper expectation.
- No HIGH security/risk findings.
- Operator approves each risk-cap increase.

Acceptance criteria:

- Position size increases are config-only and versioned.
- Regression reports compare old vs new risk settings.
- Scale-up can be reverted by config change and kill switch.

## Consolidated Open Questions

1. Assumed: primary price source is Kalshi CF Benchmarks feed. Confirm or override:
2. Assumed: on-chain signals remain secondary and can veto/reduce confidence but not create trades alone. Confirm or override:
3. Assumed: initial paper track record requires at least 300 eligible windows and 14 days. Confirm or override:
4. Assumed: live pilot starts at minimum practical size. Confirm size:
5. Assumed: max capital at risk is configured as account-equity percentages, not fixed dollars. Confirm account context:
6. Assumed: community Kalshi MCP servers are research-only until audited. Confirm or override:
7. Assumed: true sub-second arbitrage should not be promised; Mechanism 1 is opportunistic and latency-limited. Confirm expectation:
8. Assumed: asymmetric partial hedges may intentionally spend premium to reduce loss, but must be solved from the configured max-loss target and never described as arbitrage. Confirm or override:
9. Assumed: true-arbitrage opportunities are book dislocations and may not appear during normal adverse price movement; Mechanism 2 is the separate loss-management path. Confirm this matches your intended strategy:
