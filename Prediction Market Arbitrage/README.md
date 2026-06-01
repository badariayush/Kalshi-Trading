# Prediction Market Arbitrage

Python CLI bot for finding and validating cross-venue arbitrage between Polymarket and Kalshi.

V1 focuses on the actual money-making loop: match equivalent markets, normalize YES/NO books, find cases where one YES plus the opposite NO costs less than `$1.00`, verify depth and risk caps, then paper trade by default.

## Local Setup

```bash
source arbitrage/bin/activate
python -m unittest discover -s tests
```

`key.txt` lives at the parent repo root and is ignored by Git. Do not commit API keys.
