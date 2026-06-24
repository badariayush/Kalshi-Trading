# execution skills.md

## Responsibility

This directory owns Kalshi order lifecycle after another worker has authorized an action. It submits, cancels, amends/decreases if needed, tracks fills, reconciles fees, handles partial fills, and emits execution events. It must not make independent trading decisions.

## Edit Here vs Elsewhere

Edit this worker when:

- Orders are malformed.
- Kalshi auth/signing fails.
- `client_order_id` idempotency fails.
- Fill, fee, or partial-fill reconciliation is wrong.
- Time-in-force, `buy_max_cost`, `reduce_only`, `post_only`, or `cancel_order_on_pause` behavior changes.

Do not edit this worker for:

- Whether to trade; use `workers/risk`.
- Whether to authorize a partial hedge; use `workers/risk`.
- Whether to authorize true arbitrage; use `workers/arbitrage`.
- Signal probability; use `workers/signal`.

## Important Files And Data Flow

Planned inputs:

- `EntryAuthorized`
- `ExitAuthorized`
- `ReduceAuthorized`
- `ArbitrageHedgeAuthorized`
- `PartialHedgeAuthorized`

Planned outputs:

- `OrderSubmitted`
- `OrderRejected`
- `OrderCanceled`
- `FillRecorded`
- `ExecutionFailed`

Execution must reconcile expected vs actual price, quantity, and fees for every fill.

## Common Mistakes

- Do not add trade-execution MCP support as an execution backend without a separate security/risk review and explicit operator approval. The first implementation guard blocks `trade_mcp` even in paper mode.
- The `doctor` CLI validates config and safety gates only. It must not place orders, open WebSockets, call REST endpoints, read credentials, or mutate account state.
- Kalshi auth/order API plumbing can build RSA-PSS header messages and V2 order payloads, and committed configs may keep `order_api.enable_order_api = true` for readiness. They must keep `order_api.allow_order_submission = false`. Any command that flips from request building to order submission requires explicit operator approval and a fresh security/risk review.
- Buy order request objects must include `client_order_id` and `buy_max_cost`; this preserves idempotency and cost bounds before any future HTTP transport is allowed.
- Paper execution is the only approved execution path before real order submission: it consumes already-authorized instructions, prints `order placed` / `order sold` lines, emits `OrderSubmitted`, `FillRecorded`, and `PositionClosed` audit events, and computes realized P&L after both entry and exit Kalshi fees. It must not choose side, size, or authorization.
- The `paper` CLI should exercise paper execution through the full worker chain and keep `order_submission=disabled` in the completion summary and audit record.

## Debugging Playbook

1. Reproduce with the authorized instruction and the exact Kalshi request/response, with secrets redacted.
2. Confirm mode and endpoint: paper, demo, and live must not be mixed.
3. Confirm RSA-PSS signing path excludes query params.
4. Check idempotent `client_order_id` before retrying.
5. Verify time-in-force and cost bounds match the authorization.
6. Record the root cause in this file after the fix.

## Validation Commands

Planned:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli paper-demo --config configs/demo.example.toml --max-seconds 3600
```
