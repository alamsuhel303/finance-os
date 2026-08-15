"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _resolve_database_uri() -> str:
    """Prefer absolute SQLite paths so the DB works regardless of CWD."""
    default_path = BASE_DIR / "database" / "finance.db"
    raw = os.getenv("DATABASE_URL", f"sqlite:///{default_path}")

    # In-memory / special SQLite URIs must not be path-joined
    if raw in ("sqlite://", "sqlite:///:memory:") or raw.startswith("sqlite:///:memory:"):
        return "sqlite:///:memory:"

    if raw.startswith("sqlite:///"):
        path_part = raw.removeprefix("sqlite:///")
        # Absolute filesystem path already (sqlite:////abs or sqlite:///C:/...)
        if path_part.startswith("/") or (len(path_part) > 2 and path_part[1] == ":"):
            return raw
        absolute = (BASE_DIR / path_part).resolve()
        return f"sqlite:///{absolute}"

    return raw


class Config:
    """Base configuration shared across environments."""

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-finance-os-change-me")
    SQLALCHEMY_DATABASE_URI = _resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
    }

    CURRENCY_SYMBOL = os.getenv("CURRENCY_SYMBOL", "₹")
    CURRENCY_CODE = os.getenv("CURRENCY_CODE", "INR")

    ITEMS_PER_PAGE = 25
    BACKUP_DIR = BASE_DIR / "backups"
    DATABASE_DIR = BASE_DIR / "database"
    # Dashboard nudge when newest backup is older than this many days
    BACKUP_MAX_AGE_DAYS = int(os.getenv("BACKUP_MAX_AGE_DAYS", "7"))
    IMPORT_STAGING_DIR = BASE_DIR / "database" / ".import_staging"

    # Telegram bot (separate worker process)
    TELEGRAM_ENABLED = os.getenv("TELEGRAM_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_POLL_TIMEOUT = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "30"))
    TELEGRAM_TIMEZONE = os.getenv("TELEGRAM_TIMEZONE", "Asia/Kolkata")
    TELEGRAM_NOTIFY_STARTUP = os.getenv("TELEGRAM_NOTIFY_STARTUP", "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    TELEGRAM_PENDING_TTL_MINUTES = int(os.getenv("TELEGRAM_PENDING_TTL_MINUTES", "60"))
    TELEGRAM_LINK_CODE_TTL_MINUTES = int(
        os.getenv("TELEGRAM_LINK_CODE_TTL_MINUTES", "30")
    )


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
