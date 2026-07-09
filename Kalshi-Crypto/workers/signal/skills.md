# signal skills.md

## Responsibility

This directory owns signal generation only. It transforms settlement-aligned price data, Kalshi implied probability, and secondary on-chain metrics into a probability estimate, confidence score, and reasoning payload. It does not decide position size, risk limits, partial hedges, true arbitrage, or orders.

## Edit Here vs Elsewhere

Edit this worker when:

- Feature calculation is wrong.
- Probability calibration changes.
- Kalshi implied probability cross-check changes.
- On-chain metrics are added, removed, or reweighted.

Do not edit this worker for:

- Window timing; use `workers/market_monitor`.
- Trade sizing or vetoes; use `workers/risk`.
- Partial-hedge sizing; use `workers/risk`.
- True-arbitrage trigger math; use `workers/arbitrage`.
- Execution behavior; use `workers/execution`.

## Important Files And Data Flow

Planned inputs:

- `WindowOpened` and active window metadata.
- CF Benchmarks values and derived candles.
- Kalshi normalized book snapshots.
- Secondary on-chain metrics with source timestamps.

Planned outputs:

- `SignalReady`
- `SignalSkipped`

The output must include feature snapshots and source timestamps so a future review can explain why a trade happened.

## Common Mistakes

- Live BTC paper signals must use `BTC-USD` records only. Never choose the latest
  record from a mixed BTC/ETH subscription without matching the underlying.
- Signal Worker output is informational only: feature snapshots, `probability_yes`, confidence, and skip reasons. It must not decide side, size, entry, partial hedge, true arbitrage, or orders.
- Do not add non-live signal runs as operator commands. Signal inputs for operator runs must come from live WebSocket capture.
- Signal v1 computes explainable features from CF Benchmark candles and normalized Kalshi asks: EMA spread, one-candle momentum, realized-volatility proxy, strike distance, and Kalshi yes/no ask cross-check. Treat this as a testable feature path, not a proven trading edge.
- Signal analysis now includes the requested institutional concepts only where the
  current live feeds support them: market trend, BOS-style structure breaks,
  liquidity sweeps, VWAP proxy, volume-profile POC proxy, and signed order-flow
  delta proxy are derived from live Coinbase/Kalshi candle/book data. DOM ladder,
  footprint charts, open interest, funding, liquidation maps, and on-chain metrics
  are recorded as unavailable context until real live providers are configured;
  do not synthesize those inputs or use stale screenshots/manual assumptions.
- Stale order book checks should report `stale_orderbook` before lower-priority skip reasons such as insufficient candle count.
- The current live paper signal samples Coinbase BTC-USD to at most one point per
  second and recomputes on each healthy Kalshi ticker update. Coinbase is a live
  directional input, not Kalshi's CF Benchmarks RTI settlement source.

## Debugging Playbook

1. Reproduce from a persisted feature snapshot.
2. Verify CF Benchmarks data, not generic exchange spot, is the primary price source.
3. Confirm every feature timestamp is fresh enough for the active window.
4. Check the Kalshi implied-probability cross-check before blaming model calibration.
5. Disable one feature at a time in backtest to isolate bad signal contribution.
6. Record the root cause in this file after the fix.

## Validation Commands

Planned:

```bash
python -m unittest discover -s tests
python -m kalshi_crypto.cli backtest --config configs/backtest.example.toml
```
