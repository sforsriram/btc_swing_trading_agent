# config/settings.py
"""
Settings loader: merges .env (secrets) + config.yaml (params) into AppConfig.
Call get_config() anywhere to get the validated singleton.
"""
from __future__ import annotations
import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.core.config_models import AppConfig

# Load .env from project root (silently ok if missing in CI)
load_dotenv(Path(__file__).parent.parent / ".env")


@lru_cache(maxsize=1)
def get_config(config_path: str | None = None) -> AppConfig:
    """Load and validate config.yaml. Cached after first call."""
    if config_path is None:
        config_path = os.getenv(
            "CONFIG_PATH",
            str(Path(__file__).parent / "config.yaml"),
        )

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    cfg = AppConfig(**raw)
    return cfg


def get_env(key: str, default: str | None = None) -> str:
    """Retrieve a secret from environment. Raises if missing and no default."""
    value = os.getenv(key, default)
    if value is None:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Check your .env file."
        )
    return value


# Convenience accessors for secrets
def get_coinbase_credentials() -> tuple[str, str]:
    return get_env("COINBASE_API_KEY"), get_env("COINBASE_API_SECRET")


def get_telegram_credentials() -> tuple[str, str]:
    return get_env("TELEGRAM_BOT_TOKEN"), get_env("TELEGRAM_CHAT_ID")


def get_dashboard_token() -> str:
    return get_env("DASHBOARD_AUTH_TOKEN", "dev-token-change-in-prod")
