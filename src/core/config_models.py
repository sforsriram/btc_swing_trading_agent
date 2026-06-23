# src/core/config_models.py
"""
Pydantic v2 models for all configuration sections.
Validates config.yaml on startup - catches bad values before trading starts.
"""
from __future__ import annotations
from typing import List
from pydantic import BaseModel, field_validator, model_validator


class GeneralConfig(BaseModel):
    product_id: str = "BTC-USD"
    base_currency: str = "USD"
    timezone: str = "UTC"
    mode: str = "paper"
    dry_run: bool = True

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("paper", "live"):
            raise ValueError(f"mode must be 'paper' or 'live', got '{v}'")
        return v


class AccountConfig(BaseModel):
    starting_equity: float = 200.0
    quote_precision: int = 2
    base_precision: int = 8
    min_order_usd: float = 1.0

    @field_validator("starting_equity")
    @classmethod
    def validate_equity(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("starting_equity must be positive")
        return v


class FeesConfig(BaseModel):
    maker_rate: float = 0.006
    taker_rate: float = 0.012
    prefer_maker: bool = True
    fee_buffer_multiplier: float = 1.0
    slippage_rate: float = 0.001

    @field_validator("maker_rate", "taker_rate", "slippage_rate")
    @classmethod
    def validate_rates(cls, v: float) -> float:
        if not (0 <= v <= 0.1):
            raise ValueError(f"Fee/slippage rate {v} seems unreasonable (expected 0-10%)")
        return v


class TimeframesConfig(BaseModel):
    primary: str = "ONE_DAY"
    refinement: str = "FOUR_HOUR"
    weekly: str = "ONE_WEEK"
    evaluate_on_closed_candles_only: bool = True


class RegimeConfig(BaseModel):
    ema_fast: int = 50
    ema_slow: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    weekly_ema: int = 21
    require_weekly_rising: bool = True


class IndicatorsConfig(BaseModel):
    rsi_period: int = 14
    rsi_pullback_low: float = 40.0
    rsi_pullback_high: float = 50.0
    rsi_overbought: float = 70.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    atr_period: int = 14
    volume_avg_period: int = 20
    fib_levels: List[float] = [0.382, 0.5, 0.618]


class SignalWeightsConfig(BaseModel):
    trend_aligned: int = 25
    rsi_pullback: int = 20
    macd_bullish: int = 20
    volume_confirm: int = 10
    fib_confluence: int = 15
    adx_strength: int = 10

    @model_validator(mode="after")
    def weights_sum_to_100(self) -> "SignalWeightsConfig":
        total = (
            self.trend_aligned
            + self.rsi_pullback
            + self.macd_bullish
            + self.volume_confirm
            + self.fib_confluence
            + self.adx_strength
        )
        if total != 100:
            raise ValueError(f"Signal weights must sum to 100, got {total}")
        return self


class SignalsConfig(BaseModel):
    threshold: float = 70.0
    weights: SignalWeightsConfig = SignalWeightsConfig()


class RiskConfig(BaseModel):
    risk_per_trade_pct: float = 0.01
    max_concurrent_positions: int = 1
    weekly_drawdown_circuit_breaker_pct: float = 0.07
    enable_circuit_breaker: bool = True


class ExitsConfig(BaseModel):
    initial_stop_pct: float = 0.025
    initial_stop_atr_mult: float = 1.5
    breakeven_trigger_pct: float = 0.03
    scale_out_trigger_pct: float = 0.05
    scale_out_fraction: float = 0.50
    trail_atr_mult: float = 1.75
    take_profit_ceiling_pct: float = 0.08
    time_stop_days: int = 7
    use_server_side_oco: bool = True
    enable_thirds_variant: bool = False
    thirds_tier1_pct: float = 0.05
    thirds_tier2_pct: float = 0.08

    @model_validator(mode="after")
    def validate_exit_order(self) -> "ExitsConfig":
        # Scale-out must be achievable before ceiling (if ceiling is not disabled)
        if self.take_profit_ceiling_pct < 0.5:  # only validate if ceiling is active
            if not (self.scale_out_trigger_pct < self.take_profit_ceiling_pct):
                raise ValueError(
                    f"scale_out_trigger_pct ({self.scale_out_trigger_pct}) must be "
                    f"less than take_profit_ceiling_pct ({self.take_profit_ceiling_pct})"
                )
        # initial_stop_pct can be a dummy value (0.999) when ATR stop dominates
        if not (0 < self.initial_stop_pct < 1.0):
            raise ValueError(f"initial_stop_pct {self.initial_stop_pct} seems unreasonable")
        return self


class ScheduleConfig(BaseModel):
    daily_decision_utc: str = "00:05"
    refinement_every_hours: int = 4
    monitor_every_minutes: int = 10
    daily_heartbeat_utc: str = "12:00"


class BacktestConfig(BaseModel):
    start_date: str = "2021-01-01"
    end_date: str = "2026-06-01"
    initial_equity: float = 200.0
    success_min_profit_factor: float = 1.3
    success_max_drawdown_pct: float = 0.25
    parameter_sensitivity_check: bool = True
    out_of_sample_split_date: str = "2025-01-01"


class DataConfig(BaseModel):
    candles_per_request: int = 300
    rate_limit_sleep_seconds: float = 0.5
    cache_db_path: str = "data/candles.sqlite"
    gap_detection: bool = True


class AlertsConfig(BaseModel):
    enabled: bool = True
    alert_on_buy: bool = True
    alert_on_sell: bool = True
    alert_on_breakeven_move: bool = True
    alert_on_trailing_update: bool = False
    alert_on_circuit_breaker: bool = True
    alert_on_errors: bool = True
    alert_on_start_pause_kill: bool = True
    daily_heartbeat: bool = True
    dedupe_by_trade_id: bool = True
    retry_attempts: int = 3


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_log_path: str = "logs/agent.log"
    trade_ledger_path: str = "logs/trades.log"
    rotate_max_bytes: int = 10485760
    rotate_backups: int = 5

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        if v.upper() not in valid:
            raise ValueError(f"logging.level must be one of {valid}")
        return v.upper()


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8787
    chart_library: str = "lightweight-charts"
    refresh_seconds: int = 5
    state_db_path: str = "data/state.sqlite"
    require_auth: bool = True


class AppConfig(BaseModel):
    """Root config model — validates the entire config.yaml on load."""
    general: GeneralConfig = GeneralConfig()
    account: AccountConfig = AccountConfig()
    fees: FeesConfig = FeesConfig()
    timeframes: TimeframesConfig = TimeframesConfig()
    regime: RegimeConfig = RegimeConfig()
    indicators: IndicatorsConfig = IndicatorsConfig()
    signals: SignalsConfig = SignalsConfig()
    risk: RiskConfig = RiskConfig()
    exits: ExitsConfig = ExitsConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    backtest: BacktestConfig = BacktestConfig()
    data: DataConfig = DataConfig()
    alerts: AlertsConfig = AlertsConfig()
    logging: LoggingConfig = LoggingConfig()
    dashboard: DashboardConfig = DashboardConfig()
