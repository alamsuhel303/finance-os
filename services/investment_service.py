"""Investment service — CRUD, portfolio summaries, and monthly SIP posting."""

from __future__ import annotations

import calendar
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import extract, func

from extensions import db
from models import Account, Goal, Investment, Transaction
from utils.helpers import parse_nonneg_amount, parse_units

logger = logging.getLogger(__name__)


class InvestmentValidationError(ValueError):
    pass


ASSET_LABELS = {
    "mutual_fund": "Mutual Fund",
    "sip": "SIP",
    "stock": "Stocks",
    "foreign_stock": "Foreign stock",
    "rsu": "RSUs",
    "epf": "EPF",
    "fd": "Fixed Deposit",
    "nps": "NPS",
    "gold": "Gold",
    "other": "Other",
}

# Salary-deducted — monthly post credits the holding without debiting a bank account
NON_CASH_ASSET_TYPES = frozenset({"epf"})
NON_CASH_ACCOUNT_NAME = "Salary deduction (non-cash)"


def requires_source_account(inv_or_type: Investment | str) -> bool:
    asset_type = (
        inv_or_type.asset_type
        if isinstance(inv_or_type, Investment)
        else str(inv_or_type or "")
    )
    return asset_type not in NON_CASH_ASSET_TYPES


def get_or_create_non_cash_account() -> Account:
    """Ledger placeholder for EPF-style posts that never move bank cash."""
    account = Account.query.filter_by(name=NON_CASH_ACCOUNT_NAME).first()
    if account:
        return account
    account = Account(
        name=NON_CASH_ACCOUNT_NAME,
        account_type="other",
        owner="joint",
        opening_balance=0,
        current_balance=0,
        is_active=True,
        sort_order=999,
        notes="System account for salary-deducted investments (EPF). Balance stays 0.",
    )
    db.session.add(account)
    db.session.flush()
    return account


def list_investments(*, active_only: bool = True) -> list[Investment]:
    query = Investment.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Investment.sort_order, Investment.name).all()


def get_investment(inv_id: int) -> Optional[Investment]:
    return db.session.get(Investment, inv_id)


def get_portfolio_summary(*, owner: str | None = None) -> dict[str, Any]:
    """Portfolio totals. Pass owner=self|wife|joint to scope to one person."""
    owner_key = (owner or "").strip().lower() or None
    if owner_key and owner_key not in Investment.OWNERS:
        owner_key = None

    active = list_investments(active_only=True)
    all_holdings = list_investments(active_only=False)
    if owner_key:
        active = [i for i in active if i.owner == owner_key]
        all_holdings = [i for i in all_holdings if i.owner == owner_key]

    # Active first, then inactive (so paused holdings aren't "lost")
    investments = sorted(
        all_holdings,
        key=lambda i: (0 if i.is_active else 1, i.sort_order, i.name.lower()),
    )
    invested = sum((Decimal(i.invested_amount or 0) for i in active), Decimal("0"))
    current = sum((Decimal(i.current_value or 0) for i in active), Decimal("0"))
    sip = sum(
        (
            Decimal(i.monthly_sip or 0)
            for i in active
            if i.sip_active and Decimal(i.monthly_sip or 0) > 0
        ),
        Decimal("0"),
    )
    gain = current - invested
    return_pct = float((gain / invested) * 100) if invested > 0 else 0.0

    by_type: dict[str, dict[str, float]] = {}
    for inv in active:
        bucket = by_type.setdefault(
            inv.asset_type,
            {"invested": 0.0, "current": 0.0, "count": 0},
        )
        bucket["invested"] += float(inv.invested_amount or 0)
        bucket["current"] += float(inv.current_value or 0)
        bucket["count"] += 1

    allocation = [
        {
            "type": asset_type,
            "label": ASSET_LABELS.get(asset_type, asset_type),
            "current": data["current"],
            "invested": data["invested"],
            "count": data["count"],
            "pct": round((data["current"] / float(current) * 100), 1) if current else 0.0,
        }
        for asset_type, data in sorted(
            by_type.items(), key=lambda x: x[1]["current"], reverse=True
        )
    ]

    by_owner = (
        db.session.query(
            Investment.owner,
            func.coalesce(func.sum(Investment.current_value), 0),
        )
        .filter(Investment.is_active.is_(True))
        .group_by(Investment.owner)
        .all()
    )

    today = date.today()
    sip_status = get_sip_month_status(today.year, today.month, owner=owner_key)
    epf_status = get_epf_month_status(today.year, today.month, owner=owner_key)
    nav_eligible = sum(
        1
        for i in active
        if (
            i.scheme_code
            and str(i.scheme_code).isdigit()
            and i.asset_type in ("mutual_fund", "sip", "other")
        )
        or (i.asset_type == "gold" and Decimal(i.units or 0) > 0)
        or (
            i.asset_type == "foreign_stock"
            and i.scheme_code
            and Decimal(i.units or 0) > 0
        )
    )
    inactive_count = sum(1 for i in investments if not i.is_active)

    return {
        "investments": investments,
        "count": len(active),
        "inactive_count": inactive_count,
        "invested": invested,
        "current": current,
        "gain": gain,
        "return_pct": round(return_pct, 2),
        "monthly_sip": sip,
        "allocation": allocation,
        "by_owner": {owner: float(total or 0) for owner, total in by_owner},
        "sip_status": sip_status,
        "epf_status": epf_status,
        "nav_eligible_count": nav_eligible,
        "owner": owner_key,
    }


def create_investment(data: dict[str, Any]) -> Investment:
    # New holdings are always active — deactivate later from Edit if needed
    data = dict(data)
    data["is_active"] = "1"
    inv = Investment()
    _populate(inv, data)
    db.session.add(inv)
    db.session.flush()
    _maybe_value_gold(inv, data)
    _maybe_value_foreign_stock(inv, data)
    db.session.commit()
    return inv


def update_investment(inv: Investment, data: dict[str, Any]) -> Investment:
    _populate(inv, data)
    _maybe_value_gold(inv, data)
    _maybe_value_foreign_stock(inv, data)
    db.session.commit()
    return inv


def _maybe_value_gold(inv: Investment, data: dict[str, Any]) -> None:
    """For gold holdings with grams, set current value from live 22K rate."""
    if inv.asset_type != "gold":
        return
    grams = Decimal(inv.units or 0)
    if grams <= 0:
        return
    # Skip network if user already typed a current value and didn't change grams?
    # Prefer always valuing from grams when gold — that's the product ask.
    from services import gold_service
    from services.gold_service import GoldServiceError

    try:
        priced = gold_service.value_grams_22k(grams)
    except GoldServiceError as exc:
        # Allow save without live price; user can Refresh later
        logger.warning("Gold auto-value skipped: %s", exc)
        return

    inv.current_value = priced["current_value"]
    inv.last_nav = priced["price_per_gram_22k"]
    inv.last_nav_date = priced["as_of"]
    inv.scheme_code = "22K"
    # If invested left blank/0, seed cost basis to current metal value
    if Decimal(inv.invested_amount or 0) <= 0:
        inv.invested_amount = priced["current_value"]


def _maybe_value_foreign_stock(inv: Investment, data: dict[str, Any]) -> None:
    """For foreign stocks with ticker + shares, set INR value from live quote."""
    if inv.asset_type != "foreign_stock":
        return
    shares = Decimal(inv.units or 0)
    if shares <= 0 or not inv.scheme_code:
        return
    from services import foreign_stock_service
    from services.foreign_stock_service import ForeignStockServiceError

    try:
        priced = foreign_stock_service.value_shares(shares, ticker=inv.scheme_code)
    except ForeignStockServiceError as exc:
        if Decimal(inv.current_value or 0) <= 0:
            raise InvestmentValidationError(str(exc)) from exc
        logger.warning("Foreign stock auto-value skipped for %s: %s", inv.name, exc)
        return

    inv.current_value = priced["current_value"]
    inv.last_nav = priced["price"]
    inv.last_nav_date = priced["as_of"]
    inv.scheme_code = priced["ticker"]
    if Decimal(inv.invested_amount or 0) <= 0:
        inv.invested_amount = priced["current_value"]


def bulk_update_holdings(rows: list[dict[str, Any]]) -> int:
    """
    Update holdings from the Investments table edit form.
    Preserves SIP schedule fields not shown inline (sip_day, source account, etc.).
    Returns number of holdings updated.
    """
    touched = 0
    for row in rows:
        inv_id = _optional_int(row.get("id"))
        if not inv_id:
            continue
        inv = get_investment(inv_id)
        if not inv:
            raise InvestmentValidationError(f"Investment #{inv_id} not found.")

        name = (row.get("name") or "").strip()
        if not name:
            raise InvestmentValidationError("Each holding needs a name.")

        invested = parse_nonneg_amount(row.get("invested_amount"))
        current = parse_nonneg_amount(row.get("current_value"))
        sip = parse_nonneg_amount(row.get("monthly_sip"))
        units = parse_units(row.get("units"))
        if invested is None or current is None or sip is None or units is None:
            raise InvestmentValidationError(
                f"Enter valid amounts for “{name}” (invested / current / SIP / units)."
            )

        if sip > 0 and not inv.sip_day:
            raise InvestmentValidationError(
                f"“{name}” has a monthly amount but no debit day. "
                "Open full edit to finish the schedule."
            )
        if sip > 0 and requires_source_account(inv) and not inv.source_account_id:
            raise InvestmentValidationError(
                f"“{name}” needs a source account for cash debit. "
                "Open full edit to finish the schedule."
            )

        goal_id = _optional_int(row.get("goal_id"))
        if goal_id:
            goal = db.session.get(Goal, goal_id)
            if not goal:
                raise InvestmentValidationError(f"Invalid goal for “{name}”.")

        sip_active = str(row.get("sip_active", "1")).lower() in (
            "1",
            "true",
            "on",
            "yes",
        )
        scheme_code = _normalize_scheme_code(row.get("scheme_code"))

        inv.name = name
        inv.invested_amount = invested
        inv.current_value = current
        inv.monthly_sip = sip
        inv.units = units
        inv.scheme_code = scheme_code
        inv.goal_id = goal_id
        inv.sip_active = sip_active if sip > 0 else inv.sip_active
        touched += 1

    if touched:
        db.session.commit()
    return touched


def delete_investment(inv: Investment) -> None:
    """
    Remove a holding. Linked SIP/investment transactions stay in the ledger
    (cash already moved) but are unlinked so history is preserved.
    """
    linked = (
        Transaction.query.filter_by(investment_id=inv.id)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    for txn in linked:
        txn.investment_id = None
    inv.goal_id = None
    db.session.delete(inv)
    db.session.commit()
    return len(linked)


def list_sip_plans(
    *,
    active_only: bool = True,
    asset_types: set[str] | frozenset[str] | None = None,
    exclude_types: set[str] | frozenset[str] | None = None,
) -> list[Investment]:
    """Holdings with a monthly contribution amount configured."""
    query = Investment.query.filter(Investment.monthly_sip > 0)
    if active_only:
        query = query.filter(Investment.is_active.is_(True))
    if asset_types is not None:
        query = query.filter(Investment.asset_type.in_(tuple(asset_types)))
    if exclude_types:
        query = query.filter(Investment.asset_type.notin_(tuple(exclude_types)))
    return query.order_by(Investment.sip_day, Investment.name).all()


def sip_posted_for_month(inv: Investment, year: int, month: int) -> bool:
    return (
        Transaction.query.filter(
            Transaction.investment_id == inv.id,
            Transaction.transaction_type == "investment",
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        ).first()
        is not None
    )


def get_sip_month_status(
    year: int, month: int, *, owner: str | None = None
) -> dict[str, Any]:
    """Cash SIPs / fund contributions (excludes EPF salary deductions)."""
    plans = list_sip_plans(active_only=True, exclude_types=NON_CASH_ASSET_TYPES)
    if owner:
        plans = [p for p in plans if p.owner == owner]
    return _contribution_month_status(year, month, plans=plans)


def get_epf_month_status(
    year: int, month: int, *, owner: str | None = None
) -> dict[str, Any]:
    """EPF salary-deduction contributions for the month."""
    plans = list_sip_plans(active_only=True, asset_types=NON_CASH_ASSET_TYPES)
    if owner:
        plans = [p for p in plans if p.owner == owner]
    return _contribution_month_status(year, month, plans=plans)


def _contribution_month_status(
    year: int, month: int, *, plans: list[Investment]
) -> dict[str, Any]:
    rows = []
    ready = 0
    posted = 0
    skipped = 0
    total_ready = Decimal("0")
    total_posted = Decimal("0")

    for inv in plans:
        amount = Decimal(inv.monthly_sip or 0)
        already = sip_posted_for_month(inv, year, month)
        missing = []
        if not inv.sip_active:
            missing.append("paused")
        if requires_source_account(inv):
            if not inv.source_account_id:
                missing.append("no source account")
            elif not db.session.get(Account, inv.source_account_id):
                missing.append("invalid source account")
        if not inv.sip_day:
            missing.append("no credit/debit day")

        if already:
            status = "posted"
            posted += 1
            total_posted += amount
        elif missing:
            status = "skipped"
            skipped += 1
        else:
            status = "ready"
            ready += 1
            total_ready += amount

        rows.append(
            {
                "investment": inv,
                "amount": amount,
                "status": status,
                "reasons": missing,
                "post_date": _sip_post_date(inv, year, month) if inv.sip_day else None,
            }
        )

    return {
        "year": year,
        "month": month,
        "label": date(year, month, 1).strftime("%B %Y"),
        "rows": rows,
        "ready_count": ready,
        "posted_count": posted,
        "skipped_count": skipped,
        "ready_total": total_ready,
        "posted_total": total_posted,
        "plan_count": len(plans),
    }


def post_month_sips(
    *,
    year: int | None = None,
    month: int | None = None,
    kind: str = "sip",
) -> dict[str, Any]:
    """
    Create investment transactions for ready contributions this month.
    kind: "sip" (cash SIPs), "epf" (salary EPF), or "all".
    Idempotent: already-posted holdings are skipped.
    """
    from services import transaction_service
    from services.transaction_service import TransactionValidationError

    today = date.today()
    year = year or today.year
    month = month or today.month

    if kind == "epf":
        status = get_epf_month_status(year, month)
    elif kind == "all":
        status = _contribution_month_status(
            year, month, plans=list_sip_plans(active_only=True)
        )
    else:
        status = get_sip_month_status(year, month)
    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for row in status["rows"]:
        inv: Investment = row["investment"]
        if row["status"] == "posted":
            skipped.append(f"{inv.name}: already posted")
            continue
        if row["status"] == "skipped":
            skipped.append(f"{inv.name}: {', '.join(row['reasons'])}")
            continue

        post_date = row["post_date"] or date(
            year, month, min(today.day, calendar.monthrange(year, month)[1])
        )
        label = _contribution_label(inv)
        non_cash = not requires_source_account(inv) and not inv.source_account_id
        if non_cash:
            account_id = get_or_create_non_cash_account().id
        else:
            account_id = inv.source_account_id
        try:
            transaction_service.create_transaction(
                {
                    "date": post_date.isoformat(),
                    "amount": str(row["amount"]),
                    "description": f"{label} — {inv.name}",
                    "transaction_type": "investment",
                    "account_id": account_id,
                    "investment_id": inv.id,
                    "payment_mode": "auto_debit",
                    "skip_cash_impact": "1" if non_cash else "0",
                    "notes": (
                        f"Auto-posted {label} for {status['label']}"
                        + (" · salary deduction (no cash debit)" if non_cash else "")
                    ),
                }
            )
            # Accrue MF units from latest NAV when scheme is linked
            try:
                from services import nav_service

                db.session.refresh(inv)
                nav_service.accrue_units_from_purchase(inv, Decimal(row["amount"]))
                db.session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Unit accrual failed for %s", inv.name)
            created.append(inv.name)
        except TransactionValidationError as exc:
            errors.append(f"{inv.name}: {exc}")

    return {
        "year": year,
        "month": month,
        "label": status["label"],
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "created_count": len(created),
    }


def _sip_post_date(inv: Investment, year: int, month: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    day = min(int(inv.sip_day or 1), last_day)
    return date(year, month, day)


def _contribution_label(inv: Investment) -> str:
    labels = {
        "epf": "EPF",
        "nps": "NPS",
        "fd": "FD",
        "sip": "SIP",
        "mutual_fund": "SIP",
        "gold": "SIP",
        "stock": "Investment",
        "foreign_stock": "Investment",
        "rsu": "Investment",
        "other": "Investment",
    }
    return labels.get(inv.asset_type, "Investment")


def _populate(inv: Investment, data: dict[str, Any]) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise InvestmentValidationError("Name is required.")

    asset_type = (data.get("asset_type") or "mutual_fund").strip().lower()
    if asset_type not in Investment.ASSET_TYPES:
        raise InvestmentValidationError("Invalid asset type.")

    invested = parse_nonneg_amount(data.get("invested_amount"))
    current = parse_nonneg_amount(data.get("current_value"))
    sip = parse_nonneg_amount(data.get("monthly_sip"))
    if invested is None or current is None or sip is None:
        raise InvestmentValidationError("Enter valid non-negative amounts.")

    owner = (data.get("owner") or "joint").lower()
    if owner not in Investment.OWNERS:
        owner = "joint"

    goal_id = _optional_int(data.get("goal_id"))
    if goal_id:
        goal = db.session.get(Goal, goal_id)
        if not goal:
            raise InvestmentValidationError("Selected goal is invalid.")

    sip_day = _optional_int(data.get("sip_day"))
    if sip and sip > 0:
        if sip_day is None:
            raise InvestmentValidationError(
                "Set debit/credit day (1–28) when monthly amount > 0."
            )
        if sip_day < 1 or sip_day > 28:
            raise InvestmentValidationError("Day must be between 1 and 28.")
    else:
        sip_day = sip_day if sip_day and 1 <= sip_day <= 28 else None

    source_account_id = _optional_int(data.get("source_account_id"))
    needs_source = requires_source_account(asset_type)
    if sip and sip > 0 and needs_source:
        if not source_account_id:
            raise InvestmentValidationError(
                "Select a source account to debit for this monthly contribution."
            )
        account = db.session.get(Account, source_account_id)
        if not account or not account.is_active:
            raise InvestmentValidationError("Selected source account is invalid.")
    elif source_account_id:
        account = db.session.get(Account, source_account_id)
        if not account or not account.is_active:
            raise InvestmentValidationError("Selected source account is invalid.")
    else:
        source_account_id = None

    sip_active = str(data.get("sip_active", "1")).lower() in ("1", "true", "on", "yes")

    units = parse_units(data.get("units"))
    if units is None:
        raise InvestmentValidationError(
            "Enter valid grams/units (0 or more)."
            if asset_type == "gold"
            else "Enter valid shares (0 or more)."
            if asset_type == "foreign_stock"
            else "Enter valid units (0 or more)."
        )
    if asset_type == "gold" and units <= 0:
        raise InvestmentValidationError("Enter gold weight in grams.")
    if asset_type == "foreign_stock" and units <= 0:
        raise InvestmentValidationError("Enter number of shares.")

    if asset_type == "gold":
        scheme_code = "22K"
    elif asset_type == "foreign_stock":
        from services.foreign_stock_service import (
            ForeignStockServiceError,
            normalize_ticker,
        )

        try:
            scheme_code = normalize_ticker(data.get("scheme_code") or data.get("ticker"))
        except ForeignStockServiceError as exc:
            raise InvestmentValidationError(str(exc)) from exc
    else:
        scheme_code = _normalize_scheme_code(data.get("scheme_code"))

    inv.name = name
    inv.asset_type = asset_type
    inv.invested_amount = invested
    inv.current_value = current
    inv.monthly_sip = sip
    inv.sip_day = sip_day
    inv.source_account_id = source_account_id
    inv.sip_active = sip_active
    inv.scheme_code = scheme_code
    inv.units = units
    inv.owner = owner
    inv.goal_id = goal_id
    inv.start_date = _parse_date(data.get("start_date"), required=False)
    inv.notes = (data.get("notes") or "").strip() or None
    inv.is_active = str(data.get("is_active", "1")).lower() in ("1", "true", "on", "yes")


def _normalize_scheme_code(value) -> Optional[str]:
    code = (str(value).strip() if value is not None else "")
    if not code:
        return None
    if not code.isdigit():
        raise InvestmentValidationError("Scheme code must be numeric (AMFI code).")
    return code


def _optional_int(value) -> Optional[int]:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value, *, required: bool = True) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        if required:
            raise InvestmentValidationError("Date is required.")
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise InvestmentValidationError("Invalid date format. Use YYYY-MM-DD.") from exc
