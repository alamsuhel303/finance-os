"""Pytest fixtures — isolated SQLite app for Finance OS tests."""

from __future__ import annotations

import pytest
from flask import Flask


@pytest.fixture()
def app(tmp_path):
    """Fresh Flask + SQLite file per test — does not touch database/finance.db."""
    from extensions import db

    # Import models so metadata is registered
    import models  # noqa: F401

    application = Flask("finance-os-test")
    application.config.update(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "SQLALCHEMY_ENGINE_OPTIONS": {
                "connect_args": {"check_same_thread": False, "timeout": 30},
            },
            "CURRENCY_SYMBOL": "₹",
            "CURRENCY_CODE": "INR",
            "TELEGRAM_ENABLED": True,
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_TIMEZONE": "Asia/Kolkata",
            "TELEGRAM_LINK_CODE_TTL_MINUTES": 30,
            "TELEGRAM_PENDING_TTL_MINUTES": 60,
            "TELEGRAM_NOTIFY_STARTUP": False,
            "BACKUP_MAX_AGE_DAYS": 7,
        }
    )
    db.init_app(application)

    with application.app_context():
        db.create_all()
        from models import AppProfile
        from utils.seed import seed_database

        db.session.add(
            AppProfile(
                mode="couple",
                person1_name="Suhel",
                person2_name="Seema",
                is_setup_complete=True,
            )
        )
        db.session.commit()
        seed_database()
        yield application
        db.session.remove()


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield app
