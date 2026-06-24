# logging skills.md

## Responsibility

This directory owns durable audit logging and reporting. It captures every signal, risk decision, true-arbitrage evaluation, partial-hedge evaluation, order, fill, feed-health event, and settlement outcome. It does not make trading decisions.

## Edit Here vs Elsewhere

Edit this worker when:

- Audit events are missing or malformed.
- Reports misclassify win/true-arbitrage/reduced-loss/loss outcomes.
- Secret redaction fails.
- Replay cannot reconstruct a trade.
- Dashboard aggregates are wrong.

Do not edit this worker for:

- Worker decision logic; fix the owning worker.
- Execution retries; use `workers/execution`.
- Risk limits; use `workers/risk`.

## Important Files And Data Flow

Planned inputs:

- All worker events.

Planned outputs:

- Append-only JSON logs.
- SQLite/Postgres rows.
- Replay artifacts.
- Reports for feed health, fees, slippage, and outcome taxonomy.

Required outcome labels:

- `win_path`
- `true_arb_path`
- `reduced_loss_path`
- `loss_path`

## Common Mistakes

- Audit JSONL must redact sensitive keys before writing, including nested `api_key`, `private_key`, `secret`, `signature`, `password`, and `token` fields. Redaction belongs at the log boundary so shared artifacts are safe by default.
- SQLite audit storage must use the same redaction path as JSONL before persisting records. Do not store raw event payloads in side columns unless those columns are also redacted.
- SQLite connections should be explicitly closed after initialization, append, and read operations; transaction context alone is not a connection close.
- The `report` CLI reads only live-data SQLite audit records. It should flag `ExecutionFailed` as `status=error`, unhealthy feed events as `status=warning`, and healthy live-data captures without execution as `status=no_trades`.
- The `live-data` CLI should write raw provider-message audit events plus paired `FeedHealthEvaluated` records. It is expected to report `status=no_trades`; that is healthy for live-data audits as long as `feed_unhealthy_events=0` and `execution_failures=0`.

## Debugging Playbook

1. Start from a trade ID or causality ID.
2. Verify every expected event exists from window discovery through settlement.
3. Check timestamp ordering and source timestamps.
4. Confirm redaction before sharing logs.
5. If an event is wrong at creation time, fix the producing worker instead of patching reports.
6. Record the root cause in this file after the fix.

## Validation Commands

Planned:

```bash
python -m unittest discover -s tests
PYTHONPATH=src python -m kalshi_crypto.cli report --audit-db <tmp.sqlite3>
```
