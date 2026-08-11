"""Lightweight SQLite schema upgrades for additive column changes."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from extensions import db

logger = logging.getLogger(__name__)

# table -> list of (column_name, ddl_type_sql)
ADDITIVE_COLUMNS = {
    "categories": [
        ("envelope_id", "INTEGER REFERENCES envelopes(id)"),
    ],
    "accounts": [
        ("emergency_tagged", "NUMERIC(14, 2) DEFAULT 0 NOT NULL"),
    ],
    "transactions": [
        ("envelope_id", "INTEGER REFERENCES envelopes(id)"),
        ("investment_id", "INTEGER REFERENCES investments(id)"),
        ("skip_cash_impact", "BOOLEAN DEFAULT 0 NOT NULL"),
    ],
    "investments": [
        ("sip_day", "INTEGER"),
        ("source_account_id", "INTEGER REFERENCES accounts(id)"),
        ("sip_active", "BOOLEAN DEFAULT 1 NOT NULL"),
        ("scheme_code", "VARCHAR(20)"),
        ("units", "NUMERIC(18, 4) DEFAULT 0 NOT NULL"),
        ("last_nav", "NUMERIC(14, 4)"),
        ("last_nav_date", "DATE"),
    ],
}


def upgrade_schema() -> None:
    """Create missing tables, then add any missing columns on SQLite."""
    db.create_all()

    bind = db.session.get_bind()
    if bind.dialect.name != "sqlite":
        return

    inspector = inspect(bind)
    tables = set(inspector.get_table_names())

    for table, columns in ADDITIVE_COLUMNS.items():
        if table not in tables:
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        for col_name, col_ddl in columns:
            if col_name in existing:
                continue
            ddl = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"
            db.session.execute(text(ddl))
            logger.info("Added column %s.%s", table, col_name)
    db.session.commit()
