# Autonomous Kalshi Crypto Markets Trading System Architecture

Status: implementation started. The current codebase is moving toward a fully testable paper product: live data readiness for Kalshi/Coinbase, config validation, monetary math, true-arbitrage and partial-hedge decision logic, order book normalization, signal features, paper execution, an in-process event bus, and audit logging. The intended test mode should run all workers like production, with only the final real account order submission replaced by paper execution print/audit events.

This document describes a proposed architecture for an autonomous system that trades Kalshi short-duration BTC and ETH crypto strike markets. It is informational engineering planning, not investment advice, and it assumes the operator is responsible for capital allocation, legal/compliance review, and live-trading approval.

## ECC And Local Repo Inspection

The local ECC install at `/Users/ayushbadari/Developer/ECC` was inspected before drafting. Useful conventions found:

- ECC organizes persistent guidance into `agents/`, `skills/`, `commands/`, `hooks/`, `rules/`, and `scripts/`.
- Agent files use YAML frontmatter with `name`, `description`, `tools`, and `model`, followed by a scoped role, workflow, output format, and red flags.
- Skill files use `SKILL.md` with frontmatter, "When to Use", workflow guidance, checklists, and compact examples.
- Commands are legacy slash-entry shims; ECC states that `skills/` is the canonical workflow surface.
- Hooks enforce safety and quality in Claude Code, including secret checks, dev-server reminders, pre-commit quality checks, and lifecycle memory persistence. Codex does not have equivalent hooks, so this repo must encode the same safety floors in `AGENTS.md` and `skills.md`.
- Existing local trading experiments use Python 3.11, `unittest`, `Decimal`, `requests`, `websockets`, TOML configs, Kalshi RSA-PSS auth, WebSocket order books, structured JSON events, and paper mode by default.

Local repo note: the root contains ignored secret-looking files (`key.txt`, `polykey.txt`). Future agents must not open, print, move, or copy them unless the operator explicitly requests scoped credential work.

## Scope

The system trades fixed-window Kalshi crypto event contracts, starting with BTC and ETH 15-minute markets. It:

- Monitors the current and next windows.
- Builds a probability estimate for the current window.
- Enters at most one directional position per market window.
- Manages the position through take-profit, true arbitrage, asymmetric partial hedge, or settlement.
- Defaults to paper trading until explicitly promoted.
- Logs enough evidence to reconstruct every signal, risk decision, order, fill, true-arbitrage check, partial-hedge check, and final outcome.

Non-goals for the first implementation:

- No live money until paper trading has passed acceptance gates in `PLAN.md`.
- No discretionary stop-loss disguised as true arbitrage.
- No agent-autonomous switch from paper/demo to production.
- No reliance on chat transcript memory for durable project knowledge.

## Market Mechanics

Kalshi crypto markets are binary contracts. A contract settles at either `$1.00` or `$0.00`. A YES position wins if the event condition resolves true; a NO position wins otherwise. Prices are probability-like, but the executable trading math must use dollar prices, depth, fees, and slippage.

Kalshi's REST order book returns bid levels for both YES and NO sides; no asks are returned directly because a YES bid at `X` is equivalent to a NO ask at `1 - X`, and vice versa. The runtime must normalize both sides into executable YES and NO asks before doing any arbitrage math. For WebSocket order books, the subscription must explicitly set `use_yes_price: true` until Kalshi completes its migration to unified pricing.

### YES/NO Same-Market Pairing

The safe same-market pair condition is:

```text
best_ask(YES) + best_ask(NO) + fees + slippage_buffer < 1.00
```

Because fees are material, the fee-free condition is only a pre-filter. Kalshi's current general trading fee schedule, effective February 5, 2026, gives taker trading fees as:

```text
fee = round_up_to_cent(0.07 * C * P * (1 - P))
```

where `C` is contract count and `P` is contract price in dollars. Maker fees may apply to resting orders in markets covered by the maker-fee table, using:

```text
maker_fee = round_up_to_cent(0.0175 * C * P * (1 - P))
```

Fees also have per-fill rounding behavior. The system must compute expected fees before order placement and reconcile actual fees from fill reports after execution.

Worked example, not enough after fees:

- Buy 100 YES at `$0.46`, buy 100 NO at `$0.51`.
- Gross paired cost: `$97.00`.
- YES taker fee: `ceil_cent(0.07 * 100 * 0.46 * 0.54) = $1.74`.
- NO taker fee: `ceil_cent(0.07 * 100 * 0.51 * 0.49) = $1.75`.
- Total cost: `$100.49`.
- Guaranteed payout: `$100.00`.
- Result: this is a guaranteed loss of `$0.49`, not arbitrage.

Worked example, valid after fees:

- Buy 100 YES at `$0.45`, buy 100 NO at `$0.49`.
- Gross paired cost: `$94.00`.
- YES taker fee: `ceil_cent(0.07 * 100 * 0.45 * 0.55) = $1.74`.
- NO taker fee: `ceil_cent(0.07 * 100 * 0.49 * 0.51) = $1.75`.
- Total cost: `$97.49`.
- Guaranteed payout: `$100.00`.
- Locked gross profit: `$2.51`, or `$0.0251` per matched pair before any unmodeled slippage.

## Entry Logic

The Signal Worker produces a probability estimate for the active window. The Risk Worker decides whether to enter and how much to risk. The Execution Worker places the order only after authorization.

Initial signal blend:

- Settlement-aligned price action from Kalshi's CF Benchmarks value feed, transformed into 1-second and 1-minute candles.
- Simple, testable indicators only: short EMA slope/cross, last 3-5 minute momentum, realized volatility, distance from strike, and time remaining.
- On-chain or flow metrics as secondary confirmation only.
- Kalshi implied probability from normalized YES/NO pricing as a required cross-check.

The system enters one side, once, per market window. It does not buy both sides at entry. A later opposing-side buy can happen for one of two independent reasons: a true arbitrage condition becomes executable, or the Risk Worker authorizes an asymmetric partial hedge to cap loss after the original thesis weakens.

Entry example:

- Strike: BTC above `$102,500` at window close.
- CF Benchmarks RTI is `$102,540`; momentum is positive; realized volatility is moderate.
- Kalshi normalized YES ask is `$0.58`; NO ask is `$0.45`.
- Model estimates YES probability at `0.66`; implied YES ask after fees and slippage requires at least `0.62`.
- Risk Worker authorizes 100 YES contracts if all portfolio limits pass.
- Execution Worker places a bounded limit order, records actual fills and fees, and emits an open-position event.

## Hedge And Arbitrage Mechanisms

The system has two independent opposing-side buy mechanisms. They must not share a trigger, event name, or outcome label.

### Mechanism 1: True Arbitrage Watcher

This is an unconditional, opportunistic same-market arbitrage watcher. It is not a loss-management tool and it may never trigger for a given trade.

For each open directional position, the Arbitrage/Hedge Margin Worker continuously evaluates:

```text
Locked_in_cost(t) =
  P_entry
  + Ask_other(t)
  + fee(original_leg)
  + fee(hedge_leg)
  + slippage_buffer

True arbitrage may fire only when:
Locked_in_cost(t) <= 1.00 - min_arb_margin
```

`min_arb_margin` must be greater than zero. The default planning assumption is `$0.015` per matched pair for paper trading and `$0.02` per matched pair before any live pilot. The operator can tune this only through risk config.

Worked true-arbitrage example:

- Original position: 100 YES contracts at `$0.60`.
- Opposing NO ask is later executable at `$0.34` because of a book dislocation or temporary repricing.
- Entry fee: `ceil_cent(0.07 * 100 * 0.60 * 0.40) = $1.68`, or `$0.0168` per pair.
- Hedge fee: `ceil_cent(0.07 * 100 * 0.34 * 0.66) = $1.58`, or `$0.0158` per pair.
- Slippage buffer: `$0.0050` per pair.
- Locked-in cost per pair: `0.60 + 0.34 + 0.0168 + 0.0158 + 0.0050 = 0.9776`.
- Locked profit per pair: `$0.0224`.
- If `min_arb_margin = $0.02`, the true arbitrage hedge is allowed. If the NO ask is `$0.37`, the rounded hedge fee becomes `$1.64`, locked-in cost becomes `$1.0082`, and the true arbitrage hedge is forbidden.

Operational rules:

- Recompute the condition on every relevant order book update while a position is open, regardless of whether the original position is winning or losing.
- Do not assume the opposing side gets cheaper when the original thesis is losing. In normal binary pricing, the opposing side often gets more expensive. Mechanism 1 only works when the actual executable opposing book is cheap enough after fees, usually because of spread, depth, timing, or dislocation.
- Use WebSocket order book updates as the primary trigger. A watchdog should verify the condition no less often than every 250 ms while fresh book data is available. REST polling is a fallback only, with a maximum 1-second cadence to avoid rate-limit waste.
- Do not evaluate stale books. Planning default: book age must be under 750 ms for true arbitrage execution and under 2.5 seconds for paper/backtest analysis.
- Require sufficient depth at or below the assumed opposing ask for the full matched size.
- Prefer fill-or-kill or immediate-or-cancel orders with a strict limit price and `buy_max_cost` where supported.
- If the hedge does not fully fill at the assumed price, cancel or let IOC/FOK expire, record the failure, reload the book, and re-evaluate from scratch.
- The Execution Worker never decides to enter true arbitrage. It only executes an instruction issued by the Arbitrage/Hedge Margin Worker.

### Mechanism 2: Asymmetric Partial Hedge

This is a conditional loss-cap mechanism owned by the Risk And Position-Sizing Worker. It is not arbitrage, it costs real money, and in a losing settlement it must be logged only as reduced loss.

The mechanism is inactive until the opposing side's ask crosses a configurable trigger level. Example: if the original position is YES, the risk config may activate partial-hedge evaluation only when the NO ask crosses `$0.50`. The trigger must be configurable per market and tunable through backtesting; it must not be hardcoded.

When the trigger is active, the Risk Worker computes the smallest opposing-side quantity that satisfies the configured max-loss-per-trade target:

```text
wrong_side_loss(q) =
  original_cost
  + q * Ask_other(t)
  + fee(hedge_leg, q)
  + slippage_buffer(q)
  - q * 1.00

Authorize the smallest q where:
wrong_side_loss(q) <= max_loss_per_trade
```

Because Kalshi contracts are discrete and fees round per fill, the implementation should solve over integer contract quantities using `Decimal`, not a floating-point closed form. The default approach is the max-loss solve above. A fixed fraction such as "always hedge 40 percent" is not a valid default unless backtesting later proves it performs comparably and the operator explicitly configures it.

The partial hedge should normally be capped at the original position size. Any over-hedge that creates net exposure to the opposite side requires a separate explicit config flag and security/risk review.

Partial hedges may only be authorized against fresh opposing books with enough depth at or below the assumed ask for the solved quantity. If the configured max-loss target cannot be reached within depth, size, and freshness constraints, the Risk Worker must reject the partial hedge and log the unmet target.

Worked asymmetric partial-hedge example, fee-free for clarity:

- Entry: 100 YES contracts at `$0.60`, so original premium is `$60`.
- Market moves against the position; NO ask rises to `$0.55`, crossing the configured trigger.
- If the system buys 40 NO contracts at `$0.55`, hedge cost is about `$22`.
- If YES still wins: payout `$100` on YES, NO hedge expires worthless. Net is `$100 - $60 - $22 = +$18`, a smaller profit than the unhedged `+$40`.
- If NO wins: YES expires worthless, NO pays `$40` against `$22` cost. Net is `-$60 + ($40 - $22) = -$42`, a smaller loss than the unhedged `-$60`.

That 40-contract illustration reduces loss, but it does not satisfy a `$30` max-loss target. With the same simplified prices, a `$30` max-loss target requires solving `60 - (1.00 - 0.55) * q <= 30`, so `q >= 66.67`; an integer implementation would need about 67 contracts before fees, slippage, and depth checks. This is why the risk engine must solve for the configured max-loss target instead of using a fixed fraction.

There is no hedge size that produces profit in both outcomes from Mechanism 2 alone. Profit in both outcomes would require the same executable YES+NO cost condition as Mechanism 1. Mechanism 2 strictly reduces loss when the original thesis is wrong while preserving some upside if the original thesis later wins.

Residual loss case:

Mechanism 1 may never occur, and Mechanism 2 may be unavailable, too expensive, too shallow, or insufficient to reach the configured loss cap. If the original thesis is wrong and no valid action is available, the position can ride to settlement and lose the original premium plus fees. This must be tracked as a first-class outcome, not hidden as an exception.

Time-aware fallback:

- With more than 90 seconds remaining, keep evaluating take-profit, true arbitrage, and the configured partial-hedge trigger.
- From 90 to 30 seconds remaining, tighten exit logic: if a net-positive exit on the original side is available below the full take-profit target, the Risk Worker may authorize reducing exposure.
- Under 30 seconds remaining, no new entry is allowed. For open positions, the Risk Worker chooses between: net-positive exit, true arbitrage, valid partial hedge, or hold to settlement. It must not mislabel a loss-cap hedge as arbitrage.
- Under 10 seconds remaining, only reduce-only exits or already-authorized true-arbitrage or partial-hedge orders with fresh books are allowed.

## Take-Profit Exit

Default take-profit is defined as a net executable gain target:

```text
held_side_best_bid >= entry_price * (1 + take_profit_pct)
```

with `take_profit_pct = 0.20` as the default planning assumption, but execution must also pass a net-profit check after expected exit fees and slippage. A naive 20 percent quote gain can still be a weak trade if fees and spread consume it.

Worked example:

- Buy YES at `$0.50`.
- Gross target with a 20 percent gain is `$0.60`.
- The Execution Worker may attempt a sell only if executable bid depth exists at or above `$0.60` for the intended size and expected net proceeds after fees/slippage remain positive.
- As expiry approaches, the Risk Worker may accept a lower net-positive exit target if configured time decay says binary settlement risk is rising faster than expected edge.

If liquidity is insufficient, the Execution Worker can use an IOC partial reduce order only when the Risk Worker has authorized partial exit behavior. Any remaining position returns to normal management.

## Outcome Taxonomy

Every closed position must be classified as exactly one of:

1. `win_path`: the thesis was directionally correct enough to exit at a configured profit target or settle profitably. If a partial hedge fired and the original side still wins, classify the trade as `win_path` and report hedge drag separately.
2. `true_arb_path`: Mechanism 1 fired because the executable opposing side was cheap enough to lock guaranteed profit or breakeven after fees, slippage, and `min_arb_margin`.
3. `reduced_loss_path`: Mechanism 2 fired, the original thesis was wrong, and the partial hedge reduced the loss. This must never be logged as arbitrage or as profit.
4. `loss_path`: the thesis was wrong and no valid take-profit, true arbitrage, or partial hedge prevented the full loss.

The dashboard and audit logs must report these counts separately by market, time of day, signal version, and feed-health state.

## Data Sources And MCP Policy

MCP is preferred when it is mature, maintained, low-latency enough, and supports the required operation. Direct APIs are preferred when no adequate MCP exists or when an MCP would add execution risk.

| Domain | Runtime choice | MCP assessment | Notes |
| --- | --- | --- | --- |
| Kalshi market data | Direct Kalshi REST/WebSocket initially, wrapped behind an internal gateway | Community Kalshi MCP servers exist, but no official Kalshi MCP was found. Some claim trading support; maturity and safety need audit. | Direct API keeps latency and order-safety under project control. Build or fork an internal MCP later for research/read-only workflows. |
| Kalshi execution | Direct Kalshi API only in the first real implementation | Do not route live orders through unaudited third-party MCP servers. | Execution-capable MCP can be introduced only after security/risk review and demo-only proving. |
| Settlement-aligned BTC/ETH values | Kalshi CF Benchmarks WebSocket and REST passthrough | No separate MCP needed at first because Kalshi exposes CF Benchmarks directly. | This should be the primary price source because it matches settlement methodology. |
| External candles | Coinbase Advanced Trade or Exchange API as secondary/backup | Coinbase/Binance community MCPs exist, but they are not needed if CF Benchmarks feed is available. | Useful for cross-checking exchange spot divergence, not primary settlement truth. |
| On-chain data | Start with Glassnode/CryptoQuant/Dune/Alchemy research connectors, then promote only useful metrics | Glassnode, Dune, and Alchemy have MCP surfaces. | Treat on-chain signals as secondary because many metrics are lagging or noisy at 15-minute horizons. |

Important inference: for a 15-minute Kalshi crypto strategy, the primary price feed should be Kalshi's CF Benchmarks value feed, not Coinbase/Binance spot candles. Exchange spot feeds are still useful to detect dislocations, exchange-specific flow, and feed outages.

Provider notes:

- Kalshi supports production and demo REST/WebSocket endpoints. Demo credentials are separate from production and use mock funds.
- Kalshi rate limits use read/write token buckets. Most requests cost 10 tokens, but order create currently costs 100 tokens and endpoint-specific costs should be read from `/account/endpoint_costs`.
- Kalshi WebSockets require authentication at handshake, even for public market data streams.
- Kalshi exposes a CF Benchmarks WebSocket channel with roughly once-per-second ticks, trailing 60-second averages, and a final-minute quarter-hour average field.
- Kalshi also exposes a CF Benchmarks REST passthrough that costs 50 read tokens per request and requires entitlement.
- Coinbase Advanced Trade supports `ONE_MINUTE`, `FIVE_MINUTE`, and `FIFTEEN_MINUTE` candles with a max 350 buckets per request.
- Coinbase WebSocket connections and unauthenticated messages are rate-limited at 8 per second per IP.
- Binance WebSocket API limits include 300 connections per 5 minutes per IP; query `exchangeInfo` for current limits before use.
- Glassnode API calls consume data credits; current public pricing text says API calls cost 1 credit for Bitcoin and 2 credits for altcoins.
- Dune's current API FAQ states 40 requests/minute on Free, 200/minute on Plus, and custom Enterprise limits.
- CryptoQuant exposes BTC and ETH exchange flows, mempool, miner, market, and network data, but exact API pricing/rate limits require account verification.

## Signal Engine

The Signal Worker outputs:

```text
{
  market_ticker,
  window_open,
  window_close,
  strike,
  side_recommendation,
  probability_yes,
  confidence,
  expected_edge_after_fees,
  reasoning,
  feature_snapshot,
  source_timestamps
}
```

Initial features:

- Distance from strike using CF Benchmarks RTI.
- 1-minute and 3-minute EMA slope.
- Last 3-minute return and last 5-minute return.
- Realized volatility over 5 and 15 minutes.
- Price acceleration in the final 5 minutes.
- Kalshi YES/NO spread, implied probability, and book depth.
- On-chain confirmation: exchange netflow, whale transfer count/value, BTC mempool pressure, ETH gas pressure, and stablecoin flow only if the provider delivers fresh enough data.

Signals must be independently backtestable. If a feature cannot show an isolated historical edge, it should not receive live weight.

## Risk Configuration Schema

The first implementation should define a schema equivalent to this, likely as `risk_config.yaml` once code begins. Absolute dollar defaults are intentionally omitted until the operator confirms account size.

```yaml
runtime:
  mode: paper_simulated  # paper_simulated | paper_demo | live
  kill_switch_file: STOP_TRADING
  no_new_entries_under_seconds: 30
  clock_skew_max_ms: 100

portfolio:
  max_capital_at_risk_per_trade_pct: 0.25
  max_capital_at_risk_per_hour_pct: 1.00
  max_account_capital_at_risk_pct: 2.00
  max_concurrent_positions: 2
  correlated_btc_eth_exposure_cap_pct: 1.25

trade_management:
  take_profit_pct: 0.20
  take_profit_time_decay_enabled: true
  min_arb_margin: "0.0200"
  slippage_buffer: "0.0050"
  partial_hedge_enabled: true
  partial_hedge_opposing_ask_trigger: "0.5000"
  partial_hedge_max_loss_pct_of_original_risk: 0.50
  partial_hedge_max_loss_usd: null
  partial_hedge_max_contracts_pct_of_original_size: 1.00
  max_entry_book_age_ms: 1500
  max_true_arb_book_age_ms: 750
  max_partial_hedge_book_age_ms: 750
  min_depth_contracts: 1

circuit_breakers:
  daily_loss_limit_pct: 2.00
  hourly_loss_limit_pct: 0.75
  max_consecutive_losses: 3
  max_consecutive_true_arb_paths: 5
  max_consecutive_reduced_loss_paths: 3
  data_feed_stale_seconds: 2.5
  halt_new_entries_on_feed_unhealthy: true

fees:
  model: kalshi_general_2026_02_05
  verify_on_startup: true
  require_fill_fee_reconciliation: true
```

## Worker Architecture

The first runtime can be one Python process with explicit worker modules and an in-process async event bus. That matches the 15-minute latency target, keeps debugging simple, and preserves clean boundaries for later process splitting. Use typed immutable event payloads and append-only audit records.

### Market Monitor Worker

Authority: market/window lifecycle.

Inputs:

- Kalshi market list, series tickers, lifecycle WebSocket events, system clock.

Outputs:

- `WindowDiscovered`, `WindowOpened`, `WindowClosingSoon`, `WindowClosed`, `SettlementPending`.

Responsibilities:

- Track current and next BTC/ETH windows.
- Persist market ticker, strike, open/close timestamps, and settlement source metadata.
- Keep next-window metadata ready before the current window closes.
- Validate local clock drift and halt new entries if drift exceeds config.

### Signal Worker

Authority: probability estimate only.

Inputs:

- Window events, CF Benchmarks values, derived candles, Kalshi book snapshots, on-chain metrics.

Outputs:

- `SignalReady` with probability, confidence, feature snapshot, and reasoning.

Responsibilities:

- Build candlestick/price-action features.
- Apply secondary on-chain confirmation.
- Cross-check Kalshi implied probability.
- Never size or place trades.

### Risk And Position-Sizing Worker

Authority: entry veto, sizing, partial-hedge loss caps, kill switches, portfolio limits, fallback decisions.

Inputs:

- `SignalReady`, position state, fill events, opposing ask snapshots, P&L, feed health, risk config.

Outputs:

- `EntryAuthorized`, `EntryVetoed`, `ExitAuthorized`, `ReduceAuthorized`, `PartialHedgeAuthorized`, `PartialHedgeRejected`, `TradingHalted`.

Responsibilities:

- Enforce per-trade, hourly, daily, and total risk caps.
- Enforce BTC/ETH correlated exposure cap.
- Decide whether to enter and how much to risk.
- Own Mechanism 2 by activating the configurable opposing-ask trigger and solving partial-hedge quantity from `max_loss_per_trade`.
- Decide time-aware fallback behavior for already-open positions.
- Never place orders directly.

### Arbitrage/Hedge Margin Worker

Authority: true-arbitrage trigger decision only.

Inputs:

- Open positions, opposing normalized order books, fee model, slippage config, time remaining.

Outputs:

- `ArbitrageConditionEvaluated`, `ArbitrageHedgeAuthorized`, `ArbitrageHedgeRejected`.

Responsibilities:

- Recompute `Locked_in_cost(t)` continuously.
- Emit audit events for evaluations that fire and evaluations that do not.
- Authorize Mechanism 1 only when net locked-in cost is at or below `$1.00 - min_arb_margin`.
- Never size or authorize the asymmetric partial hedge; that belongs to the Risk Worker.
- Never place the hedge order itself.

### Execution Worker

Authority: order placement, cancel/replace, fill handling.

Inputs:

- Authorized entry, exit, reduce, true-arbitrage hedge, and partial-hedge instructions.

Outputs:

- `OrderSubmitted`, `OrderRejected`, `OrderCanceled`, `FillRecorded`, `ExecutionFailed`.

Responsibilities:

- Own all Kalshi API/FIX/WebSocket order operations.
- Use idempotent `client_order_id`.
- Apply max slippage, time-in-force, FOK/IOC, `buy_max_cost`, `reduce_only`, and `cancel_order_on_pause` where appropriate.
- Reconcile expected vs actual fill price and fees.
- Never make independent trading decisions.

### Logging/Audit Worker

Authority: durable audit trail.

Inputs:

- All events.

Outputs:

- Append-only logs, SQLite/Postgres rows, dashboard-ready aggregates.

Responsibilities:

- Capture every signal, risk decision, true-arbitrage evaluation, partial-hedge evaluation, order, fill, feed-health event, and settlement outcome.
- Redact secrets and private key paths.
- Produce win/true-arbitrage/reduced-loss/loss breakdown.

## Stack Recommendation

Use Python 3.11+ for the first implementation.

Reasons:

- Existing repo experiments already use Python, `unittest`, `Decimal`, `requests`, `websockets`, and `cryptography` for Kalshi.
- Quant/backtesting work is simpler in Python.
- Kalshi publishes official Python SDKs (`kalshi_python_sync`, `kalshi_python_async`) and TypeScript SDKs, but recommends treating REST/OpenAPI and WebSocket/AsyncAPI specs as source of truth for production.
- `Decimal` should be mandatory for price, fee, and P&L math.

Recommended libraries once code begins:

- `pydantic` or dataclasses plus validators for config/events.
- `httpx` or `requests` for REST.
- `websockets` for WebSocket streams.
- `cryptography` for RSA-PSS signing.
- `sqlite` for local paper/audit state, with Postgres as the promotion path.
- `hypothesis` for property-based tests around true-arbitrage math and partial-hedge max-loss sizing.

## Persistence

State that must survive restart:

- Active market windows and close times.
- Open positions and matched quantities.
- Orders, client order IDs, order status, and fills.
- Actual fees and fee rounding/rebates from fills.
- Last known book snapshots with timestamps.
- Risk config version and strategy version.
- Kill-switch state.
- Audit log and outcome classification.

Use SQLite for the first paper-trading implementation. Move to Postgres only when multiple processes or dashboards need concurrent writes.

## Timing And Clock Discipline

- Use Kalshi market timestamps as authoritative for market open/close.
- Use CF Benchmarks final-minute average when available, not a generic BTC/ETH feed, for settlement-aligned signal features.
- Run NTP or platform clock sync and alert if local clock skew exceeds 100 ms.
- No new entries under 30 seconds to close.
- Re-evaluate open positions through settlement, but prevent late orders when exchange timing and local timing disagree.

## Backtesting

The backtester must replay the same signal, fee, risk, true-arbitrage, and partial-hedge code paths used live. It should not contain a separate "backtest-only" hedge implementation.

Required data:

- Historical Kalshi market metadata and trade/book snapshots where available.
- Historical CF Benchmarks values, ideally via Kalshi historical/pass-through or directly from CF Benchmarks if entitled.
- Historical on-chain metrics for candidate secondary features.
- Simulated fill model calibrated from actual paper/live book depth and spreads.

If historical Kalshi full-depth book data is incomplete, the backtest should be labeled as a proxy simulation and cannot authorize live trading by itself.

## Paper Trading

Use two paper modes:

- `paper_simulated`: live data, simulated fills, no order calls. This is the default.
- `paper_demo`: Kalshi demo environment with mock funds, using demo credentials and demo endpoints.

Promotion path:

1. Backtest only.
2. Live data with simulated fills.
3. Demo environment with mock orders.
4. Live production with minimum size after sign-off.

## Observability

Every event should be structured JSON with UTC timestamp, event type, market ticker, worker name, strategy version, config version, and causality IDs.

Minimum dashboards:

- Current open positions.
- Feed freshness by source.
- Recent signal probabilities vs Kalshi implied probabilities.
- Entry veto reasons.
- True-arbitrage evaluations and near-misses.
- Partial-hedge triggers, solved sizes, target loss caps, and realized reduced-loss outcomes.
- Slippage expected vs actual.
- Fee expected vs actual.
- Win/true-arbitrage/reduced-loss/loss breakdown.
- Consecutive losses, consecutive true-arbitrage paths, and consecutive reduced-loss paths.

## Security And Compliance

- Store credentials only in environment variables or ignored secret files. Do not commit keys.
- Use separate demo and production API keys.
- Require least-privilege keys where Kalshi supports scopes.
- Redact API key IDs, signatures, private key paths, and account identifiers in logs.
- Never let LLM-readable on-chain/social data modify execution instructions.
- Treat third-party MCP servers as untrusted until audited.
- Do not bypass venue rate limits, account limits, or terms.
- A human must explicitly approve any production order-capable implementation and any switch from paper/demo to live.

Open compliance items:

- Confirm Kalshi's current API Developer Agreement and exchange rules for automated trading/bot usage.
- Confirm whether the operator's account type has any automation, market-maker, subaccount, or direct-member fee implications.

## Open Design Questions

These assumptions were used to complete the draft:

1. Assumed: primary settlement-aligned price source is Kalshi CF Benchmarks WebSocket, with Coinbase/Binance only as secondary cross-checks. Confirm or override.
2. Assumed: paper trading starts with `paper_simulated`, then uses Kalshi demo with mock funds before live. Confirm or override.
3. Assumed: Python 3.11+ remains the implementation stack because local experiments already use it. Confirm or override.
4. Assumed: initial live pilot, if ever approved, uses minimum Kalshi size and relative account-risk caps rather than fixed dollar caps. Confirm account equity/risk limits later.
5. Assumed: correlated BTC/ETH exposure is capped jointly, not treated as independent. Confirm or override.
6. Assumed: separate consecutive-loss, consecutive-true-arbitrage, and consecutive-reduced-loss breakers are required in addition to daily loss limits. Confirm thresholds.
7. Assumed: community Kalshi MCP servers are not trusted for production execution until audited. Confirm whether to research/fork one later.
8. Assumed: asymmetric partial hedges may intentionally spend premium to reduce wrong-side loss, but they are never classified as arbitrage or guaranteed profit. Confirm or override.
9. Assumed: true-arbitrage opportunities are book dislocations and may not appear during normal adverse price movement; partial loss-cap hedges are the separate loss-management path. Confirm that this matches the intended strategy.

## Sources Reviewed

- Kalshi API overview: https://docs.kalshi.com/welcome
- Kalshi demo environment: https://docs.kalshi.com/getting_started/demo_env
- Kalshi API keys/auth: https://docs.kalshi.com/getting_started/api_keys
- Kalshi authenticated requests: https://docs.kalshi.com/getting_started/quick_start_authenticated_requests
- Kalshi rate limits: https://docs.kalshi.com/getting_started/rate_limits
- Kalshi order book responses: https://docs.kalshi.com/getting_started/orderbook_responses
- Kalshi order direction/pricing: https://docs.kalshi.com/getting_started/order_direction
- Kalshi orderbook endpoint: https://docs.kalshi.com/api-reference/market/get-market-orderbook
- Kalshi create order endpoint: https://docs.kalshi.com/api-reference/orders/create-order
- Kalshi fee schedule PDF: https://kalshi.com/docs/kalshi-fee-schedule.pdf
- Kalshi fee rounding: https://docs.kalshi.com/getting_started/fee_rounding
- Kalshi crypto settlement help: https://help.kalshi.com/en/articles/13823838-crypto-markets
- Kalshi CF Benchmarks REST passthrough: https://docs.kalshi.com/cfbenchmarks/rest-passthrough
- Kalshi CF Benchmarks WebSocket value feed: https://docs.kalshi.com/websockets/cfbenchmarks-value
- Kalshi SDKs: https://docs.kalshi.com/sdks/overview
- Coinbase Advanced Trade candles: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/products/get-product-candles
- Coinbase Exchange candles: https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles
- Coinbase WebSocket rate limits: https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-rate-limits
- Binance WebSocket API rate limits: https://developers.binance.com/docs/binance-spot-api-docs/websocket-api/rate-limits
- Glassnode pricing/API credits: https://studio.glassnode.com/pricing
- CryptoQuant API guide: https://userguide.cryptoquant.com/api/introduction
- Etherscan API/pricing: https://etherscan.io/apis
- Dune MCP: https://docs.dune.com/api-reference/agents/mcp
- Dune API FAQ: https://docs.dune.com/api-reference/overview/faq
- Alchemy MCP server: https://github.com/alchemyplatform/alchemy-mcp-server
- Model Context Protocol announcement: https://www.anthropic.com/news/model-context-protocol
