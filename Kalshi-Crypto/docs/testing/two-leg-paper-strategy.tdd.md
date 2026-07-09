# Two-Leg Paper Strategy TDD Evidence

## User Journey

As the paper-trading operator, I want entries and exits to react to healthy live
updates as they arrive, with the first entry on either YES or NO and at most one
strategy-authorized opposite-side entry, so the run can measure simulated PnL
without hardcoded direction or duplicate actions.

## RED

Command:

```text
PYTHONPATH=src kalshi-crypto/bin/python -m unittest tests.test_paper_strategy
```

Initial strategy result: failed with `ModuleNotFoundError` for
`kalshi_crypto.paper_strategy`, proving the coordinator did not exist. The
real-time phase also began with failures for missing `PaperRealtimeState`,
immediate callback delivery, restart recovery, and feed-health gating.

## GREEN

The same focused command passed after implementing the coordinator. The full
suite passed 94 tests:

```text
PYTHONPATH=src kalshi-crypto/bin/python -m unittest discover -s tests
```

Coverage command:

```text
COVERAGE_FILE=/private/tmp/kalshi-crypto.coverage kalshi-crypto/bin/python -m coverage report --show-missing --fail-under=80
```

Result: 84 percent total coverage; the 80 percent gate passed.

## Guarantees

| Guarantee | Test |
|---|---|
| Falling live BTC prices can authorize NO as leg 1 | `test_falling_btc_path_enters_no_instead_of_hardcoded_yes` |
| Rising live BTC prices can authorize YES as leg 1 | `test_rising_btc_path_enters_yes` |
| Leg 2 is opposite-side only and total legs never exceed two | `test_second_leg_is_opposite_and_market_is_capped_at_two_legs` |
| A YES first leg can only add NO as leg 2 | `test_yes_first_leg_can_only_add_no_as_second_leg` |
| Insufficient live signal history produces no trade | `test_does_not_trade_without_enough_live_candles` |
| Stale live Coinbase input cannot authorize a new entry | `test_does_not_enter_when_latest_coinbase_price_is_stale` |
| Paper product must match a subscribed live Coinbase product | `test_rejects_paper_product_missing_from_live_subscription` |
| Live audit output records the strategy-selected side and keeps submission disabled | `test_live_audit_prints_and_records_strategy_selected_no_order` |
| Settlement PnL combines both market legs | `test_auto_runner_skips_current_market_and_runs_next_once` |
| A strategy entry is emitted before capture completion | `test_realtime_state_emits_directional_entry_before_capture_ends` |
| A take-profit exit uses live bid, slippage, and both fees | `test_take_profit_exit_uses_live_bid_slippage_and_two_sided_fees` |
| Real-time actions are persisted exactly once | `test_network_callback_persists_realtime_entry_and_exit_once` |
| Late first entries are blocked at ten minutes remaining | `test_late_directional_entry_is_blocked_but_management_is_separate` |
| Unresolved positions recover and settle after restart | `test_auto_runner_recovers_and_settles_unresolved_position` |
| Take-profit positions are not closed again at settlement | `test_auto_runner_does_not_resettle_take_profit_closed_position` |
| Stale and clock-skewed records are gated from live strategy state | `test_realtime_strategy_gate_rejects_stale_and_clock_skewed_records` |
| Report fees are not double-counted | `test_report_uses_closed_position_fee_total_without_double_counting_orders` |
| Large live reports do not materialize raw feed rows | `test_report_cli_does_not_materialize_the_full_raw_audit_log` |

## Known Gaps

- The deterministic suite uses live-provider-shaped fixtures and does not open
  external sockets.
- A real network smoke test still depends on Kalshi/Coinbase availability and
  valid local credentials.
- Coinbase is a live directional input, while Kalshi crypto settlement uses CF
  Benchmarks RTI. This basis difference must be measured before real execution.
