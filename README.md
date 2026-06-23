# BTC Swing Trading Agent — Phase 0

Production-grade BTC-USD swing trading agent on Coinbase Advanced Trade.
**Phase 0: Data Pipeline + Backtest** — validate edge before any real money.

---

## Project Structure

```
btc-swing-agent/
├── config/
│   ├── config.yaml        ← ALL strategy parameters (edit here)
│   └── settings.py        ← config loader
├── src/
│   ├── core/              ← config models, logging, DB, ORM
│   ├── data/              ← Coinbase client, paginated collector, repository
│   ├── strategy/          ← indicators, regime, signals, sizing, stops
│   └── backtest/          ← engine + metrics (Phase 0)
├── tests/                 ← pytest suite
├── data/                  ← SQLite databases (gitignored)
├── logs/                  ← rotating logs + charts (gitignored)
├── .env.example           ← copy to .env and fill in API keys
├── requirements.txt
└── run_backtest.py        ← Phase 0 entry point
```

---

## Quick Start

### 1. Install dependencies
```bash
cd btc-swing-agent
pip install -r requirements.txt
```

### 2. Configure API keys
```bash
cp .env.example .env
# Edit .env with your Coinbase API key and secret (TRADE-ONLY scope)
# Telegram credentials are optional for Phase 0
```

### 3. Run tests first
```bash
pytest tests/ -v
```
All tests should pass before running the backtest.

### 4. Run the backtest
```bash
# Full backtest (fetches live data from Coinbase — takes ~30 seconds)
python run_backtest.py

# Offline mode (use cached data only — instant if already fetched)
python run_backtest.py --offline

# Test the thirds-based exit variant
python run_backtest.py --thirds

# Both variants + sensitivity sweep
python run_backtest.py --sensitivity
python run_backtest.py --thirds --sensitivity
```

---

## Strategy Overview

**Timeframes:** Daily decisions, 4h refinement, weekly filter
**Style:** Swing trading, 1–7 day holds, enter on PULLBACKS only

### Regime Filter (ALL must pass to enter)
- EMA50 > EMA200 on daily (macro uptrend)
- ADX(14) > 20 (real trend, not chop)
- Weekly EMA21 rising

### Entry Signal (weighted confluence, threshold 70/100)
| Component         | Weight |
|-------------------|--------|
| Trend aligned     | 25     |
| RSI(14) 40–50     | 20     |
| MACD bullish      | 20     |
| Volume > 20d avg  | 10     |
| Fib 38.2/50/61.8  | 15     |
| ADX strength      | 10     |

### Exit State Machine (5 stages)
1. **Initial Stop:** −2.5% or 1.5×ATR (whichever is more conservative)
2. **Break-Even:** At +3% net, raise stop to entry + fee buffer
3. **Scale-Out:** At +5% net, SELL 50% of position
4. **Trail Remainder:** 1.75×ATR trailing stop (only moves up)
5. **TP Ceiling:** Hard cap at +8%, exit remainder

**Hard Backstops:** Time stop (7 days), momentum exit (RSI>70 + MACD div), circuit breaker (−7% week)

---

## Success Criteria (GO/NO-GO for Phase 1)
- ✅ Profit Factor ≥ 1.3
- ✅ Max Drawdown ≤ 25%
- ✅ Positive expectancy
- ✅ Out-of-sample degradation < 30%

---

## Configuration

Edit `config/config.yaml` to tune strategy parameters.
All thresholds are config-driven — backtest and live code are always identical.

Key parameters:
```yaml
signals:
  threshold: 70          # raise to 75 for fewer/higher-quality signals

exits:
  initial_stop_pct: 0.025    # 2.5% stop
  scale_out_fraction: 0.50   # sell 50% at +5%
  enable_thirds_variant: false  # set true for 1/3+1/3+trail

risk:
  risk_per_trade_pct: 0.01   # 1% equity per trade
```

---

## Output Files

After running backtest:
- `logs/equity_curve.png` — equity curve + drawdown chart
- `logs/r_distribution.png` — R-multiple distribution histogram
- `logs/sensitivity.csv` — parameter sensitivity sweep (if --sensitivity)
- `logs/agent.log` — structured JSON logs
- `data/candles.sqlite` — cached OHLCV data (reused on next run)

---

## Phase Roadmap

| Phase | Name              | Status      |
|-------|-------------------|-------------|
| 0     | Data + Backtest   | ← YOU ARE HERE |
| 1     | Paper Trading     | Locked until Phase 0 GO |
| 2     | Live Trading ($200) | Locked until Phase 1 GO |
| 3     | Dashboard + Scale | Locked until Phase 2 GO |

**DO NOT proceed to Phase 1 until Phase 0 returns GO.**

---

## Security Notes

- API keys are TRADE-ONLY scope — no withdrawal permission ever
- Keys live in `.env` only — never in `config.yaml` or code
- `.env` and `data/*.sqlite` are gitignored
- Dashboard (Phase 3) binds to localhost only
