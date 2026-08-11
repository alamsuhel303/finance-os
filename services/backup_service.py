"""Backup, restore, and CSV export utilities."""

from __future__ import annotations

import csv
import io
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import inspect

from extensions import db
from models import Account, Budget, Category, Goal, Investment, Liability, Transaction

logger = logging.getLogger(__name__)


class BackupError(ValueError):
    pass


def _db_path() -> Path:
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        raise BackupError("Backup/restore is only supported for SQLite.")
    return Path(uri.removeprefix("sqlite:///"))


def _backup_dir() -> Path:
    path = Path(current_app.config["BACKUP_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup(label: str | None = None) -> Path:
    """Copy the live SQLite file into backups/. Returns the backup path."""
    source = _db_path()
    if not source.exists():
        raise BackupError("Database file not found.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    dest = _backup_dir() / f"finance_os_{stamp}{suffix}.db"

    # Ensure all writes are flushed
    db.session.commit()
    engine = db.engine
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")

    shutil.copy2(source, dest)
    logger.info("Backup created: %s", dest)
    return dest


def list_backups() -> list[dict[str, Any]]:
    backups = []
    for path in sorted(_backup_dir().glob("finance_os_*.db"), reverse=True):
        stat = path.stat()
        backups.append(
            {
                "name": path.name,
                "path": str(path),
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            }
        )
    return backups


def get_backup_health(max_age_days: int | None = None) -> dict[str, Any]:
    """Status for Dashboard / Settings: is local backup fresh enough?"""
    if max_age_days is None:
        max_age_days = int(current_app.config.get("BACKUP_MAX_AGE_DAYS", 7))
    backups = list_backups()
    backup_dir = _backup_dir()
    db_path = _db_path()
    latest = backups[0] if backups else None
    age_days = None
    stale = True
    if latest:
        age = datetime.now(timezone.utc) - latest["modified"]
        age_days = age.total_seconds() / 86400
        stale = age_days >= max_age_days
    return {
        "has_backup": bool(latest),
        "stale": stale,
        "age_days": age_days,
        "max_age_days": max_age_days,
        "latest": latest,
        "backup_dir": str(backup_dir.resolve()),
        "db_path": str(db_path.resolve()),
    }


def restore_backup(filename: str) -> Path:
    """Restore a backup file over the live database. App should restart after."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise BackupError("Invalid backup filename.")

    source = _backup_dir() / filename
    if not source.exists() or not source.is_file():
        raise BackupError("Backup file not found.")

    dest = _db_path()

    # Safety copy of current DB before overwrite
    if dest.exists():
        safety = _backup_dir() / (
            f"finance_os_pre_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
        )
        shutil.copy2(dest, safety)

    db.session.remove()
    db.engine.dispose()
    shutil.copy2(source, dest)
    logger.info("Restored backup %s → %s", source, dest)
    return dest


def export_transactions_csv() -> str:
    """Return CSV text of all transactions."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "date",
            "amount",
            "description",
            "type",
            "category",
            "account",
            "to_account",
            "paid_by",
            "payment_mode",
            "need_want",
            "notes",
        ]
    )

    rows = (
        Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    )
    for txn in rows:
        writer.writerow(
            [
                txn.id,
                txn.date.isoformat() if txn.date else "",
                float(txn.amount or 0),
                txn.description,
                txn.transaction_type,
                txn.category.name if txn.category else "",
                txn.account.name if txn.account else "",
                txn.to_account.name if txn.to_account else "",
                txn.paid_by,
                txn.payment_mode,
                txn.need_want,
                txn.notes or "",
            ]
        )
    return buffer.getvalue()


def export_accounts_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "name",
            "type",
            "owner",
            "opening_balance",
            "current_balance",
            "emergency_tagged",
            "is_active",
        ]
    )
    for acc in Account.query.order_by(Account.sort_order).all():
        writer.writerow(
            [
                acc.id,
                acc.name,
                acc.account_type,
                acc.owner,
                float(acc.opening_balance or 0),
                float(acc.current_balance or 0),
                float(getattr(acc, "emergency_tagged", 0) or 0),
                acc.is_active,
            ]
        )
    return buffer.getvalue()


def export_net_worth_csv() -> str:
    """Live net worth + monthly snapshot history."""
    from models import NetWorthSnapshot
    from services import net_worth_service

    live = net_worth_service.compute_live_net_worth()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "kind",
            "date",
            "cash_savings",
            "investments",
            "liabilities",
            "total_assets",
            "net_worth",
            "notes",
        ]
    )
    writer.writerow(
        [
            "live",
            date_today_iso(),
            float(live["cash_savings"]),
            float(live["investments"]),
            float(live["liabilities"]),
            float(live["total_assets"]),
            float(live["net_worth"]),
            "Current live calculation",
        ]
    )
    snaps = (
        NetWorthSnapshot.query.order_by(NetWorthSnapshot.snapshot_date.desc()).all()
    )
    for s in snaps:
        writer.writerow(
            [
                "snapshot",
                s.snapshot_date.isoformat() if s.snapshot_date else "",
                float(s.cash_savings or 0),
                float(s.investments or 0),
                float(s.liabilities or 0),
                float(
                    (s.cash_savings or 0)
                    + (s.investments or 0)
                    + (s.other_assets or 0)
                ),
                float(s.net_worth or 0),
                s.notes or "",
            ]
        )
    return buffer.getvalue()


def export_investments_csv() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "name",
            "asset_type",
            "invested_amount",
            "current_value",
            "units",
            "monthly_sip",
            "scheme_code",
            "owner",
            "goal",
            "is_active",
        ]
    )
    for inv in Investment.query.order_by(Investment.sort_order, Investment.name).all():
        writer.writerow(
            [
                inv.id,
                inv.name,
                inv.asset_type,
                float(inv.invested_amount or 0),
                float(inv.current_value or 0),
                float(inv.units or 0),
                float(inv.monthly_sip or 0),
                inv.scheme_code or "",
                inv.owner,
                inv.goal.name if inv.goal else "",
                inv.is_active,
            ]
        )
    return buffer.getvalue()


def date_today_iso() -> str:
    from datetime import date

    return date.today().isoformat()


def get_db_stats() -> dict[str, Any]:
    path = _db_path()
    size_kb = round(path.stat().st_size / 1024, 1) if path.exists() else 0
    return {
        "path": str(path),
        "size_kb": size_kb,
        "accounts": Account.query.count(),
        "categories": Category.query.count(),
        "transactions": Transaction.query.count(),
        "budgets": Budget.query.count(),
        "investments": Investment.query.count(),
        "goals": Goal.query.count(),
        "liabilities": Liability.query.count(),
        "tables": inspect(db.engine).get_table_names(),
    }
