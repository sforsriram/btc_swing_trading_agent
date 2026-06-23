# config.py
# Phase 0 GO Configuration — DO NOT modify without creating a new branch
# Backtest result: PF=1.40, WR=52.9%, Trades=70, MaxDD=6.7%, Net=+18.9%

# ── Fees ──────────────────────────────────────────────────────────────
FEE_PER_SIDE = 0.003           # 0.3% maker fee = 0.6% round-trip

# ── Data ──────────────────────────────────────────────────────────────
DATA_START = "2015-01-01"
DATA_TICKER = "BTC-USD"
DATA_INTERVAL = "1d"

# ── Regime Filter ─────────────────────────────────────────────────────
REGIME_EMA_FAST = 50           # EMA50 > EMA200 = bullish trend
REGIME_EMA_SLOW = 200
WEEKLY_EMA_PERIOD = 21         # Weekly EMA21 must be rising

# ── Entry Conditions ──────────────────────────────────────────────────
RSI_PERIOD = 14
RSI_THRESHOLD = 50             # RSI < 50 = pullback zone
EMA_PROXIMITY_PERIOD = 20      # Price within 5% of EMA20
EMA_PROXIMITY_PCT = 0.05       # 5%

# ── Stop Loss ─────────────────────────────────────────────────────────
INITIAL_STOP_ATR_MULT = 2.0    # Initial stop = entry - max(2×ATR, 3.5%)
INITIAL_STOP_FLOOR_PCT = 0.035 # 3.5% minimum stop distance

# ── Exit ──────────────────────────────────────────────────────────────
SCALE_OUT_PCT = 0.065          # Scale out 50% at +6.5%
TRAIL_ATR_MULT = 2.25          # Trail remaining 50% at 2.25×ATR
# No hard take-profit, no time stop

# ── Position Sizing ───────────────────────────────────────────────────
INITIAL_CAPITAL = 200.0        # USD — Phase 2 live rig size
RISK_PER_TRADE_PCT = 0.02      # Risk 2% of capital per trade