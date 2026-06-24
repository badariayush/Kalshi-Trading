# risk skills.md

## Responsibility

This directory owns portfolio risk, position sizing, asymmetric partial-hedge loss caps, kill switches, circuit breakers, and time-aware fallback decisions. It is the only worker authorized to decide how much capital to risk, whether to veto a trade, and how large a partial hedge must be to satisfy the configured max-loss target. It does not compute signals, evaluate the true-arbitrage formula, or place orders.

## Edit Here vs Elsewhere

Edit this worker when:

- A trade is incorrectly allowed or vetoed.
- Position sizing is wrong.
- Daily/hourly/consecutive-loss/correlated-exposure caps change.
- Partial-hedge opposing-ask triggers, max-loss targets, or integer sizing rules change.
- Kill-switch or feed-unhealthy behavior changes.
- Time-to-expiry fallback policy changes.

Do not edit this worker for:

- Signal math; use `workers/signal`.
- True-arbitrage formula authorization; use `workers/arbitrage`.
- API order failures; use `workers/execution`.
- Audit persistence bugs; use `workers/logging`.

## Important Files And Data Flow

Planned inputs:

- `SignalReady`
- Open positions and fills.
- Opposing ask snapshots, depth, and fee estimates for partial-hedge sizing.
- Feed-health events.
- Risk config.
- P&L state.

Planned outputs:

- `EntryAuthorized`
- `EntryVetoed`
- `ExitAuthorized`
- `ReduceAuthorized`
- `PartialHedgeAuthorized`
- `PartialHedgeRejected`
- `TradingHalted`

## Common Mistakes

- Entry authorization belongs in the Risk Worker, not the Signal Worker or orchestrator. Before any future execution-capable run, `EntryAuthorized` should include side, quantity, price, probability, and confidence after checking signal edge, confidence, fresh executable ask, and depth.
- Do not use a fixed partial-hedge fraction by default. Solve the smallest integer contract quantity that caps wrong-side loss at the configured target, including fees, slippage, depth, and contract limits.
- Do not call a partial hedge arbitrage or profit when the original side loses; report it as reduced loss.
- When max-loss solving needs more contracts than fresh visible depth supports, reject with an explicit unmet-target reason instead of authorizing a smaller hedge that fails the configured cap.
- If both percent-of-original-risk and absolute USD partial-hedge max-loss limits are configured, use the more conservative lower loss cap.

## Debugging Playbook

1. Reproduce with risk config, portfolio state, and one candidate signal.
2. Evaluate limits in deterministic order: kill switch, feed health, daily/hourly loss, consecutive loss, exposure, correlated exposure, liquidity.
3. Verify existing positions are still managed when new entries are halted.
4. Confirm absolute dollar thresholds were not guessed when account equity is unknown.
5. For partial hedges, verify the opposing ask crossed the configured trigger before sizing runs.
6. For partial hedges, recompute wrong-side loss across settlement outcomes and confirm it meets the configured cap when enough depth is available.
7. Record the root cause in this file after the fix.

## Validation Commands

Planned:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli doctor --config configs/live.example.toml
```
