# Phase 0 Backtest Results — FINAL GO

**Date completed:** June 22, 2026

**Data:** BTC-USD daily, 2015-01-01 to 2026-05-31 (4,169 candles, yfinance)

**Status:** ✅ GO — proceeding to Phase 1 paper trading

---

## Performance

| Metric | Value |
|--------|-------|
| **Total Trades** | 70 |
| **Win Rate** | 52.9% (37W / 33L) |
| **Profit Factor** | 1.40 |
| **Avg Win** | +16.44% |
| **Avg Loss** | -12.24% |
| **R:R Ratio** | 1.34:1 |
| **Max Drawdown** | 6.7% |
| **Net Return** | +18.9% |
| **Avg Hold Days** | 23.8 |
| **Sharpe Ratio** | 0.38 |

---

## Exit Distribution

| Exit Type | Count | % |
|-----------|-------|---|
| Trail Stop | 39 | 56% |
| Initial Stop | 31 | 44% |
| Breakeven | 0 | 0% |
| Scale-out only | 0 | 0% |

---

## In-Sample vs Out-of-Sample (split 2025-01-01)

| Metric | In-Sample | Out-of-Sample |
|--------|-----------|----------------|
| Trades | 62 | 8 |
| Win Rate | 53.2% | 50.0% |
| PF | 1.50 | 0.73 |
| Net Return | +19.7% | -0.6% |
| Max DD | 6.0% | 2.9% |

---

## Strategy Parameters (frozen in config.py)

- **Regime:** EMA50 > EMA200 + Weekly EMA21 rising
- **Entry:** RSI(14) < 50 + price within 5% of EMA20
- **Stop:** max(2.0×ATR, 3.5%)
- **Scale-out:** 50% at +6.5%, move stop to breakeven
- **Trail:** 2.25×ATR on highest close
- **Fees:** 0.3% per side (maker)

---

## Next Phase

**Phase 1:** Paper trading via Coinbase Advanced Trade API

**GO criteria for Phase 2:** PF ≥ 1.0, WR ≥ 45%, 10+ trades, no single loss > 15%
- **Scale-out:** 50% at +6.5%, move stop to breakeven
- **Trail:** 2.25×ATR on highest close
- **Fees:** 0.3% per side (maker)

## Next Phase

**Phase 1:** Paper trading via Coinbase Advanced Trade API

GO criteria for Phase 2:
- PF ≥ 1.0
- WR ≥ 45%
- 10+ trades
- No single loss > 15%