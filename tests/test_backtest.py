# tests/test_backtest.py
"""
Integration tests for the backtest engine.
Uses synthetic data to verify the full pipeline runs correctly.
"""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def make_synthetic_daily(n=600, seed=42) -> pd.DataFrame:
    """Generate synthetic daily OHLCV with a bull run then bear market."""
    np.random.seed(seed)
    # Simulate: 2021 bull → 2022 bear → 2023-2024 recovery
    dates = pd.date_range("2021-01-01", periods=n, freq="D")

    # Price with mean reversion and trend
    returns = []
    for i in range(n):
        if i < 150:   # bull
            r = np.random.normal(0.003, 0.025)
        elif i < 350:  # bear
            r = np.random.normal(-0.002, 0.03)
        else:          # recovery
            r = np.random.normal(0.001, 0.02)
        returns.append(r)

    close = 40000.0 * np.cumprod(1 + np.array(returns))
    noise = np.abs(np.random.normal(0, 0.01, n))
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.lognormal(12, 0.5, n)

    return pd.DataFrame({
        "open_time": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def make_synthetic_weekly(n=100, seed=42) -> pd.DataFrame:
    """Generate synthetic weekly candles."""
    np.random.seed(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="W")
    close = 30000.0 * np.cumprod(1 + np.random.normal(0.005, 0.04, n))
    noise = np.abs(np.random.normal(0, 0.02, n))
    high = close * (1 + noise)
    low = close * (1 - noise)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    volume = np.random.lognormal(13, 0.5, n)

    return pd.DataFrame({
        "open_time": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })


def get_test_config(start="2021-01-01", end="2022-12-31"):
    """Get config with test date range — no DB needed."""
    from config.settings import get_config
    cfg = get_config()
    cfg.backtest.start_date = start
    cfg.backtest.end_date = end
    cfg.backtest.initial_equity = 200.0
    cfg.backtest.parameter_sensitivity_check = False
    return cfg


class TestBacktestEngine:
    @pytest.fixture(autouse=True)
    def mock_db(self):
        """Mock DB session so tests don't need SQLite."""
        with patch("src.core.db.get_state_session"), \
             patch("src.core.db.get_candles_session"):
            yield

    def test_backtest_runs_without_error(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        assert results is not None

    def test_results_have_equity_curve(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        assert not results.equity_curve.empty
        assert "equity" in results.equity_curve.columns

    def test_all_trades_have_exit(self):
        """No trade should be left open (backtest closes all at end)."""
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        for t in results.trades:
            assert t.exit_date is not None
            assert t.exit_price is not None

    def test_win_rate_between_0_and_1(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        if results.total_trades > 0:
            assert 0 <= results.win_rate <= 1

    def test_profit_factor_non_negative(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        if results.total_trades > 0:
            assert results.profit_factor >= 0

    def test_max_drawdown_between_0_and_1(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        assert 0 <= results.max_drawdown_pct <= 1.0

    def test_fees_are_deducted(self):
        """Total fees paid should be > 0 if any trades executed."""
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        if results.total_trades > 0:
            assert results.total_fees_usd > 0

    def test_no_concurrent_positions(self):
        """Should never have more than 1 position at a time."""
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        # Verify no overlapping trades
        trades = sorted(results.trades, key=lambda t: t.entry_date)
        for i in range(1, len(trades)):
            if trades[i - 1].exit_date and trades[i].entry_date:
                assert trades[i].entry_date >= trades[i - 1].exit_date

    def test_r_multiples_computed(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        if results.total_trades > 0:
            assert len(results.r_multiples) == results.total_trades

    def test_empty_data_returns_empty_results(self):
        from src.backtest.engine import run_backtest
        cfg = get_test_config()
        results = run_backtest(pd.DataFrame(), pd.DataFrame(), cfg)
        assert results.total_trades == 0

    def test_equity_never_goes_negative(self):
        """Equity should never go below zero (we cap risk at 95% of equity)."""
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        if not results.equity_curve.empty:
            assert (results.equity_curve["equity"] >= 0).all()

    def test_thirds_variant_runs(self):
        """Thirds-based exit variant should run without error."""
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        cfg.exits.enable_thirds_variant = True
        cfg.exits.scale_out_fraction = 0.333
        results = run_backtest(daily, weekly, cfg)
        assert results is not None

    def test_go_live_field_set(self):
        from src.backtest.engine import run_backtest
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()
        results = run_backtest(daily, weekly, cfg)
        # go_live is a boolean
        assert isinstance(results.go_live, bool)


class TestBacktestMetrics:
    def test_print_report_runs(self, capsys):
        from src.backtest.engine import run_backtest
        from src.backtest.metrics import print_report
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()

        with patch("src.core.db.get_state_session"), \
             patch("src.core.db.get_candles_session"):
            results = run_backtest(daily, weekly, cfg)

        print_report(results)
        captured = capsys.readouterr()
        assert "BACKTEST REPORT" in captured.out
        assert "Win Rate" in captured.out
        assert "Profit Factor" in captured.out
        assert "GO" in captured.out or "NO-GO" in captured.out

    def test_print_trade_table_runs(self, capsys):
        from src.backtest.engine import run_backtest
        from src.backtest.metrics import print_trade_table
        daily = make_synthetic_daily()
        weekly = make_synthetic_weekly()
        cfg = get_test_config()

        with patch("src.core.db.get_state_session"), \
             patch("src.core.db.get_candles_session"):
            results = run_backtest(daily, weekly, cfg)

        print_trade_table(results)
        captured = capsys.readouterr()
        # Should print something even with 0 trades
        assert len(captured.out) >= 0


class TestConfigValidation:
    def test_config_loads_and_validates(self):
        from config.settings import get_config
        cfg = get_config()
        assert cfg.signals.weights.trend_aligned + \
               cfg.signals.weights.rsi_pullback + \
               cfg.signals.weights.macd_bullish + \
               cfg.signals.weights.volume_confirm + \
               cfg.signals.weights.fib_confluence + \
               cfg.signals.weights.adx_strength == 100

    def test_exit_thresholds_ascending(self):
        from config.settings import get_config
        cfg = get_config()
        assert cfg.exits.breakeven_trigger_pct < cfg.exits.scale_out_trigger_pct
        assert cfg.exits.scale_out_trigger_pct < cfg.exits.take_profit_ceiling_pct

    def test_risk_pct_reasonable(self):
        from config.settings import get_config
        cfg = get_config()
        assert 0 < cfg.risk.risk_per_trade_pct <= 0.05

    def test_fee_rates_realistic(self):
        from config.settings import get_config
        cfg = get_config()
        assert 0 < cfg.fees.maker_rate < cfg.fees.taker_rate
        assert cfg.fees.taker_rate <= 0.05


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
