# Implementation Plan

Subsystems:

- venue connectors for Polymarket and Kalshi
- normalized order-book model
- market matching and confidence scoring
- depth-aware arbitrage detector
- risk limits and portfolio state
- paper executor and gated live executor
- SQLite persistence
- CLI reports

Workers planned for the live runtime:

- WebSocket/data feed worker
- pair search worker
- execution worker
- risk and portfolio management worker

Paper-first validation:

- Connect to real venue market data and WebSocket feeds.
- Run the same matching, freshness, depth, edge, and risk checks as live mode.
- Do not call the venue order APIs.
- Record an open paper position when a real live-data opportunity would have
  been entered.
- Leave paper positions open until the markets resolve.
- Generate reports showing open positions, resolved positions, expected profit,
  realized profit, and false-match incidents.
