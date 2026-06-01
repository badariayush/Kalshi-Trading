# Risk Management

V1 is paper-first. Live trading should stay disabled until paper results prove that opportunities survive latency, fees, and market-pair matching risk.

Core controls:

- secret files ignored by Git
- explicit live-mode opt-in
- tiny validation notional per leg
- max total exposure
- max per-pair exposure
- max per-venue exposure
- daily loss and drawdown caps
- consecutive-loss circuit breaker
- stale-book rejection
- halt-and-reconcile on data feed failure

The largest risks are false market equivalence and one-leg fills. The implementation treats partial fills and stale data as critical incidents.
