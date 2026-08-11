"""Finance OS — application entry point."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Flask

from config import config_by_name
from extensions import db
from utils.helpers import format_inr
from utils.seed import seed_database


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.getenv("FLASK_ENV", "development")
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.from_object(config_by_name.get(config_name, config_by_name["default"]))

    _configure_logging(app)
    _ensure_directories(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_template_helpers(app)
    _init_database(app)

    return app


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app.logger.setLevel(level)


def _ensure_directories(app: Flask) -> None:
    Path(app.config["DATABASE_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["BACKUP_DIR"]).mkdir(parents=True, exist_ok=True)


def _init_extensions(app: Flask) -> None:
    db.init_app(app)


def _register_blueprints(app: Flask) -> None:
    from routes import (
        accounts_bp,
        budget_bp,
        checklist_bp,
        dashboard_bp,
        envelopes_bp,
        goals_bp,
        insurance_bp,
        investments_bp,
        networth_bp,
        reports_bp,
        settings_bp,
        transactions_bp,
    )

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(checklist_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(envelopes_bp)
    app.register_blueprint(budget_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(investments_bp)
    app.register_blueprint(goals_bp)
    app.register_blueprint(networth_bp)
    app.register_blueprint(insurance_bp)


def _register_template_helpers(app: Flask) -> None:
    symbol = app.config["CURRENCY_SYMBOL"]

    @app.template_filter("inr")
    def inr_filter(value):
        return format_inr(value, symbol=symbol)

    @app.context_processor
    def inject_globals():
        return {
            "app_name": "Finance OS",
            "currency_symbol": symbol,
            "nav_items": [
                {"id": "dashboard", "label": "Dashboard", "icon": "bi-grid-1x2", "endpoint": "dashboard.index", "ready": True},
                {"id": "checklist", "label": "Month", "icon": "bi-calendar-check", "endpoint": "checklist.index", "ready": True},
                {"id": "transactions", "label": "Transactions", "icon": "bi-arrow-left-right", "endpoint": "transactions.list_transactions", "ready": True},
                {"id": "accounts", "label": "Accounts", "icon": "bi-wallet2", "endpoint": "accounts.index", "ready": True},
                {"id": "envelopes", "label": "Envelopes", "icon": "bi-envelope", "endpoint": "envelopes.index", "ready": True},
                {"id": "budget", "label": "Budget", "icon": "bi-pie-chart", "endpoint": "budget.index", "ready": True},
                {"id": "networth", "label": "Net Worth", "icon": "bi-bar-chart-line", "endpoint": "networth.index", "ready": True},
                {"id": "investments", "label": "Investments", "icon": "bi-graph-up-arrow", "endpoint": "investments.index", "ready": True},
                {"id": "home", "label": "Home Planner", "icon": "bi-house-heart", "endpoint": None, "ready": False, "sprint": 4},
                {"id": "car", "label": "Car Planner", "icon": "bi-car-front", "endpoint": None, "ready": False, "sprint": 4},
                {"id": "travel", "label": "Travel", "icon": "bi-airplane", "endpoint": None, "ready": False, "sprint": 4},
                {"id": "insurance", "label": "Insurance", "icon": "bi-shield-check", "endpoint": "insurance.index", "ready": True},
                {"id": "goals", "label": "Goals", "icon": "bi-flag", "endpoint": "goals.index", "ready": True},
                {"id": "reports", "label": "Reports", "icon": "bi-file-earmark-bar-graph", "endpoint": "reports.index", "ready": True},
                {"id": "settings", "label": "Settings", "icon": "bi-gear", "endpoint": "settings.index", "ready": True},
            ],
        }


def _init_database(app: Flask) -> None:
    with app.app_context():
        # Import models so metadata is registered
        import models  # noqa: F401
        from utils.schema import upgrade_schema

        upgrade_schema()
        seed_database()
        app.logger.info("Database ready at %s", app.config["SQLALCHEMY_DATABASE_URI"])


app = create_app()


if __name__ == "__main__":
    # Port 5000 conflicts with macOS AirPlay Receiver (browser 403).
    app.run(host="127.0.0.1", port=5001, debug=True)
