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


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
