# BTC Swing Trading Agent

Automated BTC-USD swing trading system built on Coinbase Advanced Trade API.

## Strategy Summary

- **Timeframe:** Daily candles
- **Direction:** Long-only trend following
- **Entry:** Pullback to EMA20 in bullish regime (RSI < 50)
- **Exit:** Asymmetric — scale 50% at +6.5%, trail remainder at 2.25×ATR
- **Edge:** Accept noisy entries, rely on exit asymmetry for profitability

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Backtesting | ✅ GO — PF 1.40 |
| 1 | Paper Trading | 🔄 Next |
| 2 | Live $200 Test Rig | ⏳ Pending |
| 3 | Dashboard + Scaling | ⏳ Pending |

## Phase 0 Results

- 70 trades over 2015–2026
- 52.9% win rate, PF 1.40, Max DD 6.7%, Net +18.9%
- Full results: `results/phase0_final_results.md`

## Project Structure

```
btc_swing_trading_agent/
├── config.py              # All strategy constants (frozen at Phase 0 GO)
├── backtest/
│   ├── engine.py          # Main backtest orchestration
│   ├── indicators.py      # EMA, RSI, ATR, weekly EMA calculations
│   ├── stops.py           # Stop loss state machine
│   ├── position_sizing.py # Risk-based position sizing
│   └── results.py         # Metrics, reporting, attribution
├── results/               # Backtest output snapshots
└── live/                  # Phase 1+ live trading (coming)
```

## Setup

```bash
pip install -r requirements.txt
python backtest/engine.py
```

## Versioning

- `main` — stable, tested configs only
- `phase1-paper` — paper trading integration (Phase 1)
- `experiments/` — parameter changes, never merged to main untested