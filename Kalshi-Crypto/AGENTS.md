# AGENTS.md

This repo plans an autonomous Kalshi crypto-market trading system. Treat it as financial-risk software: small mistakes can create real losses once live trading exists.

## Session Start

Before making any change, read the root `skills.md` and the `skills.md` for the specific worker directory you're about to touch. Summarize the constraints that matter for the task before writing code. After finishing the task, whether it's a feature, a bug fix, or a refactor, update the relevant `skills.md` file(s) with anything a future agent would need to know to avoid re-making the same mistake or re-deriving the same decision. Do not write vague notes; write specific, actionable rules tied to actual files and behavior.

## Current Phase

This repo is in planning. Do not scaffold application code until the operator signs off on `ARCHITECTURE.md`, `PLAN.md`, this file, and the worker `skills.md` stubs.

## ECC Conventions Used

The local ECC install was inspected at `/Users/ayushbadari/Developer/ECC`.

- Agents are specialized, scoped, and documented with role, process, and red flags.
- Skills are the canonical long-lived workflow memory.
- Commands are a legacy compatibility surface.
- Hooks provide safety in Claude Code, but Codex needs instruction-based equivalents.
- TDD, security review, least privilege, immutable data, and smallest-change edits are default expectations.

## Repo Agents

The roles below mirror ECC agent style, but they are project-specific. When Codex multi-agent support is available, map these roles to local agents. Otherwise, perform the role inline and state which role you are using.

### planning-architecture-agent

Purpose: owns system design, worker contracts, and milestone planning.

Use when:

- A change affects more than one worker.
- A worker input/output contract changes.
- A provider, persistence, runtime mode, or risk-policy decision changes.

Responsibilities:

- Update `ARCHITECTURE.md` and `PLAN.md` for structural decisions.
- Keep the worker-authority boundaries explicit.
- Maintain the consolidated open-question list.

### coding-agent

Purpose: implements one worker at a time after planning sign-off.

Use when:

- Adding or changing worker code.
- Adding provider adapters.
- Implementing config, event models, persistence, or CLI entrypoints.

Responsibilities:

- Read root `skills.md` and the worker `skills.md` before editing.
- Keep changes within one worker unless the planning agent has approved a contract change.
- Use `Decimal` for monetary math.
- Default to paper/simulated mode.

### testing-agent

Purpose: owns tests, backtesting evidence, and regression coverage.

Use when:

- Adding a feature, fixing a bug, or changing risk/hedge logic.
- Writing backtests or replay tests.
- Validating acceptance criteria.

Responsibilities:

- Follow TDD: write failing tests, verify RED, implement, verify GREEN.
- Maintain 80 percent or better coverage once code exists.
- Prioritize property-based tests for true-arbitrage math and partial-hedge max-loss sizing.
- Prove the Arbitrage/Hedge Margin Worker never authorizes Mechanism 1 when `Locked_in_cost > 1.00 - min_arb_margin`.
- Prove the Risk Worker solves partial-hedge size from the configured max-loss target rather than assuming a fixed fraction.

### debugging-agent

Purpose: follows a deliberate debugging playbook instead of guessing.

Use when:

- A worker stalls, emits wrong events, misclassifies positions, or fails validation.
- Feeds go stale or order/fill reconciliation disagrees.

Responsibilities:

- Reproduce the failure with the smallest event trace possible.
- Identify which worker owns the failing decision.
- Fix root cause, not symptoms.
- Update the relevant `skills.md` with the specific lesson learned.

### security-risk-review-agent

Purpose: reviews anything that can expose secrets, change risk limits, or place/cancel orders.

Use when:

- Code touches Kalshi auth, API keys, order placement, risk config, kill switches, circuit breakers, MCP servers, external data ingestion, or logging.
- Before any commit that could affect live trading behavior.

Responsibilities:

- Check for hardcoded secrets and unsafe logging.
- Confirm paper/demo/live mode cannot be flipped accidentally.
- Confirm venue rate limits and terms are not bypassed.
- Confirm execution workers do not make trading decisions.
- Confirm risk, true-arbitrage, and partial-hedge formulas match `ARCHITECTURE.md`.

## Worker Authority

- `workers/market_monitor`: owns window lifecycle and timing.
- `workers/signal`: owns probability estimates only.
- `workers/risk`: owns sizing, vetoes, partial-hedge max-loss calculations, kill switches, and fallback decisions.
- `workers/arbitrage`: owns true-arbitrage trigger decisions only.
- `workers/execution`: owns Kalshi order lifecycle only.
- `workers/logging`: owns durable audit logs and reports.

No worker may silently take over another worker's authority.

## Dangerous Areas

Any change here requires security/risk review:

- True-arbitrage formula: `Locked_in_cost(t) <= 1.00 - min_arb_margin`.
- Asymmetric partial-hedge trigger, max-loss target, and integer contract sizing.
- Kalshi API signing, private key paths, API key IDs, and credentials.
- Live/paper/demo mode selection.
- Kill switch and circuit breaker behavior.
- Fee calculations and fee rounding.
- Order placement, cancellation, `buy_max_cost`, `reduce_only`, `post_only`, and time-in-force logic.
- CF Benchmarks settlement-source mapping.
- BTC/ETH correlated exposure caps.
- Any MCP server that can access credentials, account data, or trading operations.

## Coding Rules

- No implementation code before planning sign-off.
- Use small, focused files. Target 200-400 lines; 800 lines is the hard ceiling.
- Prefer immutable objects and append-only audit records.
- Validate all external data at system boundaries.
- Handle errors explicitly and log structured context without secrets.
- Do not hardcode thresholds; put them in config.
- Do not introduce dependencies without justification.
- Do not read, print, or modify `key.txt`, `polykey.txt`, `.env*`, `*.key`, or `*.pem` unless the operator explicitly requests credential work.

## Testing Rules

- TDD is mandatory once implementation begins.
- Unit tests are required for pure math, config validation, and worker decisions.
- Integration tests are required for provider adapters and persistence.
- End-to-end paper/demo tests are required before any live pilot.
- True-arbitrage tests must include stale books, fee rounding, depth, slippage, and boundary values at exactly `$1.00 - min_arb_margin`.
- Partial-hedge tests must prove the computed hedge quantity caps wrong-side loss at the configured target across trigger prices and opposing ask prices.

## Self-Improvement Rule

Whenever any agent fixes a bug, makes an architectural decision, learns something about Kalshi's API/fee behavior, or gets corrected by the operator, it must update the relevant `skills.md` file before considering the task done. Lessons that apply repo-wide go in the root `skills.md`. Lessons specific to one worker go in that worker's directory-level `skills.md`. Do not leave important knowledge only in the chat transcript - the repo is the memory, not the conversation.

## External Action Boundary

Networked tools are read-only by default. Search, inspect, and draft freely within the requested scope, but require explicit operator approval before posting, publishing, pushing, merging, opening paid jobs, changing third-party resources, modifying credentials, or enabling live trading.
