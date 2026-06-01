# API Verification Checklist

Before live trading, verify for both Polymarket and Kalshi:

- order book depth endpoint and WebSocket update semantics
- timestamp precision and stale-feed behavior
- fees and settlement payout assumptions
- order types available: FOK, IOC, or marketable limit
- fill event shape and partial-fill reporting
- authentication/session refresh behavior
- rate limits and backoff requirements
- cancel behavior after failed or partial execution

Crypto market availability checks:

- Confirm which Kalshi crypto markets are available through the API, especially
  BTC and other short-duration contracts.
- Confirm whether the accessible Polymarket account/API is using global
  Polymarket or Polymarket US.
- Do not assume Polymarket US has short-duration crypto markets. Discover them
  through the API and record the exact market ids before enabling crypto
  matching.
- For every crypto pair, compare the resolution rule carefully. "Above at close"
  and "touches during interval" are not equivalent.

Live mode must remain blocked until this checklist is completed.
