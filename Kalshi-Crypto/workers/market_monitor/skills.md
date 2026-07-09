# market_monitor skills.md

## Responsibility

This directory owns market/window lifecycle. It tracks upcoming and currently open BTC/ETH Kalshi windows, strike metadata, open/close timestamps, settlement metadata, and timing events. It does not score signals, size trades, evaluate partial hedges, evaluate true arbitrage, or place orders.

## Edit Here vs Elsewhere

Edit this worker when:

- Window discovery is wrong.
- Close-time or strike metadata is wrong.
- The next window is not ready before the current window closes.
- Clock-skew or lifecycle events are mishandled.

Do not edit this worker for:

- Probability logic; use `workers/signal`.
- Position sizing or kill switches; use `workers/risk`.
- Partial-hedge sizing; use `workers/risk`.
- True-arbitrage math; use `workers/arbitrage`.
- Order placement; use `workers/execution`.

## Important Files And Data Flow

Planned files:

- `workers/market_monitor/`: lifecycle discovery and event publishing.
- `workers/market_monitor/skills.md`: this memory file.

Planned inputs:

- Kalshi market list.
- Kalshi lifecycle WebSocket events.
- System clock and clock-skew check.

Planned outputs:

- `WindowDiscovered`
- `WindowOpened`
- `WindowClosingSoon`
- `WindowClosed`
- `SettlementPending`

## Common Mistakes

- Kalshi REST books expose YES and NO bids, not direct asks. Normalize executable asks from the opposite bid (`YES ask = 1 - best NO bid`, `NO ask = 1 - best YES bid`) and preserve source/received timestamps for freshness checks.
- Feed freshness uses `circuit_breakers.data_feed_stale_seconds` / `data_feed_stale_ms`, not entry book age. Entry, true-arbitrage, and partial-hedge book ages remain separate execution-decision thresholds.
- Market lifecycle projection uses an immutable `MarketWindowRegistry`: applying events returns a new registry and leaves the previous registry unchanged. `current_windows` include only `open` or `closing` windows where `open_timestamp_ms <= now_ms < close_timestamp_ms`; `next_window` returns the earliest non-terminal future window for each underlying.
- Live WebSocket work should be enabled for the live-data product. Kalshi WebSocket handshakes require authenticated `KALSHI-ACCESS-*` headers, while Coinbase public market-data subscriptions can be represented separately; committed config should keep `live_data.enable_live_network = true` so live data is ready while real order submission stays disabled.
- Live WebSocket auditing belongs in `live-data`: parse Kalshi/Coinbase provider messages, write feed-health audit records, and keep final execution disabled. Use `wss://external-api-ws.kalshi.com/trade-api/ws/v2` as the current Kalshi production default.
- For future provider timestamps, market-monitor feed health must distinguish
  minor provider/local clock jitter from real skew. Observed Kalshi ticker jitter
  of 1,106 ms should pass only because config sets
  `future_clock_skew_tolerance_ms = 1500`; records beyond the configured
  tolerance remain `reason=clock_skew` and must not feed real-time strategy.
- Kalshi ticker payload `ts` values may be Unix seconds (`1782646202`) rather than
  milliseconds. Market Monitor must normalize numeric `ts` below
  `10_000_000_000` by multiplying by 1000 before feed-health checks; failing to
  do this produces huge stale ages and blocks all ticker books from strategy.
- BTC 15m public REST metadata may expose the market strike at top level or inside
  `custom_strike`. Parse both `floor_strike` and `functional_strike` from both
  locations; missing strike disables paper strategy for that market, so the auto
  runner must print `auto_paper_strategy_disabled=... reason=missing_market_strike`.
- Do not reintroduce non-live operator runs. Operator market-monitor runs must come from live WebSocket capture.

## Debugging Playbook

1. Reproduce with a single market ticker and captured provider payload.
2. Compare local clock, Kalshi open/close timestamps, and emitted event timestamps.
3. Verify the worker publishes next-window metadata before current-window close.
4. Check whether a stale or missing lifecycle event should have triggered REST rediscovery.
5. Record the root cause in this file after the fix.

## Validation Commands

Planned:

```bash
python -m unittest discover -s tests
PYTHONPATH=src python -m kalshi_crypto.cli live-data --config configs/live.example.toml --kalshi-market-ticker <current-real-kalshi-market> --audit-db <tmp.sqlite3>
```
