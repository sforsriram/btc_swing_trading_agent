# BTC Swing Trading Agent

Automated BTC-USD swing trading system built on Coinbase Advanced Trade API.

## Strategy Summary
| Parameter  | Value                                      |
|------------|--------------------------------------------|
| Timeframe  | Daily candles (BTC-USD)                    |
| Direction  | Long-only trend following                  |
| Regime     | EMA50 > EMA200 + Weekly EMA21 rising       |
| Entry      | RSI(14) < 50 + price within 5% of EMA20   |
| Stop       | max(2.0×ATR, 3.5%) initial, 2.25×ATR trail|
| Scale-out  | 50% at +6.5%, move stop to breakeven       |
| Edge       | Noisy entries + asymmetric exits           |
| Fees       | 0.3% per side (maker)                      |

## Phase Status
| Phase | Description          | Status                  |
|-------|----------------------|-------------------------|
| 0     | Backtesting          | ✅ GO — PF 1.40          |
| 1     | Paper Trading        | 🔄 In progress           |
| 2     | Live $200 Test Rig   | ⏳ Pending               |
| 3     | Dashboard + Scaling  | ⏳ Pending               |

## Phase 0 Results
- **70 trades** over 2015–2026 (4,169 daily candles)
- **52.9% win rate** (37W / 33L)
- **Profit Factor 1.40** (target ≥ 1.3) ✅
- **Max Drawdown 6.7%** (limit 25%) ✅
- **Net Return +18.9%**, Sharpe 0.38
- **Exit split:** 56% trail stop, 44% initial stop
- Full results: [results/phase0_final_results.md](results/phase0_final_results.md)

## Project Structure

```
btc_swing_trading_agent/

│
├── config.py                  # All strategy constants (frozen at Phase 0 GO)
├── config/
│   ├── config.yaml            # Runtime configuration
│   └── settings.py            # Environment settings
├── requirements.txt
├── run_backtest.py            # Backtest entry point
│
├── backtest/                  # Phase 0 — backtesting engine
│   ├── engine.py              # Main orchestration + run_backtest()
│   ├── indicators.py          # EMA, RSI, ATR, weekly EMA
│   ├── stops.py               # Stop loss state machine
│   ├── position_sizing.py     # Risk-based sizing
│   └── results.py             # Metrics, reporting, filter attribution
│
├── src/                       # Live trading infrastructure (Phase 1+)
│   ├── core/                  # Models, DB, logging
│   ├── data/                  # Coinbase client, candle collector, repository
│   ├── strategy/              # Regime filter, signal generation
│   ├── execution/             # Order execution (Phase 2)
│   ├── risk/                  # Risk management (Phase 2)
│   ├── monitoring/            # Alerting + dashboard (Phase 3)
│   └── scheduler/             # Job scheduling (Phase 1+)
├── tests/                     # Test suite (pytest)
├── results/                   # Backtest output snapshots
├── data/                      # Runtime data (gitignored)
└── live/                      # Phase 1+ paper trading runner
```

## Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Run backtest
python run_backtest.py

# Run tests
pytest tests/
```

## Versioning Strategy
| Branch          | Purpose                                      |
|-----------------|----------------------------------------------|
| `main`          | Stable, tested configs only — never break    |
| `phase1-paper`  | Paper trading integration (active)           |
| `experiments/`  | Parameter changes — never merge untested     |

**Rule:** All Phase 1+ work happens on `phase1-paper`. 
Merge to `main` only when a phase GO decision is confirmed.

## Key Design Decisions
- **Simplicity over optimization:** 8 backtest iterations proved that adding 
  filters reduces performance. The minimalist 3-condition entry is intentional.
- **Exit asymmetry is the edge:** Entries are deliberately noisy. 
  Profitability comes from letting winners run (trail stop) and cutting 
  losers quickly (ATR stop).
- **Maker fees only:** 0.6% round-trip vs 2.4% taker — this assumption 
  must be preserved in live trading via limit orders.

---

After updating the file run:
  git add README.md
  git commit -m "Update README — correct project structure and strategy details"
  git push origin main