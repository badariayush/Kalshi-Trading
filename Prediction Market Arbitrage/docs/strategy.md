# Arbitrage Strategy

The bot makes money by buying both sides of equivalent binary contracts across venues for less than the guaranteed payout.

Example: if Polymarket YES costs `0.46` and Kalshi NO costs `0.51`, the paired cost is `0.97`. If the contracts are equivalent, one leg pays `1.00` at settlement and the other pays `0.00`, creating `0.03` gross edge per paired contract before fees.

## Entry Logic

For each matched pair, evaluate both directions:

- Buy Polymarket YES + Kalshi NO
- Buy Kalshi YES + Polymarket NO

A candidate is valid only when:

- both order books are fresh, default under 2.5 seconds
- pair confidence is high enough
- depth exists on both legs
- net edge after fees and slippage buffer is at least 2%
- risk caps allow the trade

The bot walks order-book depth instead of trusting only best ask. Size is capped at the deepest quantity that still satisfies the minimum net edge.

## Paper Trading Validation

The real validation mode should not use fake markets. It should connect to live
Kalshi and Polymarket market data exactly as the real bot would, then stop right
before order submission.

In paper mode, when an opportunity passes every entry gate, the bot records:

- matched market pair
- direction
- YES venue and entry price
- NO venue and entry price
- fillable size
- net edge
- expected profit
- timestamp
- status = open

Because this strategy does not use a take-profit or stop-loss, most paper
positions cannot be scored immediately. They stay open until the underlying
markets resolve. After settlement, the report compares the recorded entry cost
against the realized payout and marks the paper position resolved.

Short-duration crypto markets are useful for validation because they resolve
quickly. BTC and other crypto markets should be treated as a first discovery
target, but only if both venues list equivalent markets with matching settlement
rules.
