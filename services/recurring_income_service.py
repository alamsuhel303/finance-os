"""Recurring income — monthly salary/credit templates and posting."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import extract

from extensions import db
from models import Account, Category, RecurringIncome, Transaction
from services import transaction_service
from utils.helpers import parse_nonneg_amount


class RecurringIncomeValidationError(ValueError):
    pass


def _salary_category_id() -> int | None:
    """Prefer seeded Salary income category; else first active income category."""
    salary = Category.query.filter_by(
        name="Salary", category_type="income", is_active=True
    ).first()
    if salary:
        return salary.id
    fallback = (
        Category.query.filter_by(category_type="income", is_active=True)
        .order_by(Category.sort_order, Category.name)
        .first()
    )
    return fallback.id if fallback else None


def list_templates(*, active_only: bool = True) -> list[RecurringIncome]:
    query = RecurringIncome.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(RecurringIncome.sort_order, RecurringIncome.name).all()


def get_template(template_id: int) -> Optional[RecurringIncome]:
    return db.session.get(RecurringIncome, template_id)


def _month_label(year: int, month: int) -> str:
    return date(year, month, 1).strftime("%b %Y")


def description_for(template: RecurringIncome, year: int, month: int) -> str:
    return f"{template.name} · {_month_label(year, month)}"


def posted_for_month(template: RecurringIncome, year: int, month: int) -> bool:
    desc = description_for(template, year, month)
    return (
        Transaction.query.filter(
            Transaction.transaction_type == "income",
            Transaction.account_id == template.account_id,
            Transaction.description == desc,
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        ).first()
        is not None
    )


def get_month_status(year: int | None = None, month: int | None = None) -> dict[str, Any]:
    today = date.today()
    year = year or today.year
    month = month or today.month
    templates = list_templates(active_only=True)
    rows = []
    ready = 0
    posted = 0
    ready_total = Decimal("0")
    posted_total = Decimal("0")

    for tmpl in templates:
        amount = Decimal(tmpl.amount or 0)
        already = posted_for_month(tmpl, year, month)
        if already:
            status = "posted"
            posted += 1
            posted_total += amount
        elif amount <= 0 or not tmpl.account_id:
            status = "skipped"
        else:
            status = "ready"
            ready += 1
            ready_total += amount
        rows.append(
            {
                "template": tmpl,
                "amount": amount,
                "status": status,
                "description": description_for(tmpl, year, month),
                "post_date": _credit_date(tmpl, year, month),
            }
        )

    return {
        "year": year,
        "month": month,
        "label": date(year, month, 1).strftime("%B %Y"),
        "rows": rows,
        "ready_count": ready,
        "posted_count": posted,
        "ready_total": ready_total,
        "posted_total": posted_total,
        "plan_count": len(templates),
    }


def post_month_income(
    year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    """Create income transactions for all ready templates this month."""
    status = get_month_status(year, month)
    created = []
    skipped = []

    for row in status["rows"]:
        if row["status"] != "ready":
            skipped.append(row["template"].name)
            continue
        tmpl: RecurringIncome = row["template"]
        post_date = row["post_date"] or date(status["year"], status["month"], 1)
        payload = {
            "date": post_date.isoformat(),
            "amount": str(row["amount"]),
            "description": row["description"],
            "transaction_type": "income",
            "account_id": tmpl.account_id,
            "paid_by": tmpl.owner,
            "payment_mode": "other",
            "need_want": "n/a",
            "notes": tmpl.notes or "Posted from recurring income",
        }
        cat_id = _salary_category_id()
        if cat_id:
            payload["category_id"] = cat_id
        txn, _ = transaction_service.create_transaction(payload)
        created.append(txn)

    return {
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "label": status["label"],
        "total": sum((Decimal(t.amount or 0) for t in created), Decimal("0")),
    }


def create_template(data: dict[str, Any]) -> RecurringIncome:
    item = RecurringIncome()
    _populate(item, data)
    db.session.add(item)
    db.session.commit()
    return item


def update_template(item: RecurringIncome, data: dict[str, Any]) -> RecurringIncome:
    _populate(item, data)
    db.session.commit()
    return item


def delete_template(item: RecurringIncome) -> None:
    db.session.delete(item)
    db.session.commit()


def _credit_date(tmpl: RecurringIncome, year: int, month: int) -> date:
    last = monthrange(year, month)[1]
    day = min(int(tmpl.day_of_month or 1), last, 28)
    return date(year, month, day)


def _populate(item: RecurringIncome, data: dict[str, Any]) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise RecurringIncomeValidationError("Name is required.")

    amount = parse_nonneg_amount(data.get("amount"))
    if amount is None:
        raise RecurringIncomeValidationError("Enter a valid amount.")
    if amount <= 0:
        raise RecurringIncomeValidationError("Amount must be greater than zero.")

    try:
        account_id = int(data.get("account_id") or 0)
    except (TypeError, ValueError) as exc:
        raise RecurringIncomeValidationError("Select a credit account.") from exc
    account = db.session.get(Account, account_id)
    if not account or not account.is_active:
        raise RecurringIncomeValidationError("Select a valid credit account.")

    try:
        day = int(data.get("day_of_month") or 1)
    except (TypeError, ValueError) as exc:
        raise RecurringIncomeValidationError("Invalid credit day.") from exc
    if day < 1 or day > 28:
        raise RecurringIncomeValidationError("Credit day must be between 1 and 28.")

    owner = (data.get("owner") or "self").lower()
    if owner not in RecurringIncome.OWNERS:
        owner = "self"

    try:
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0

    item.name = name
    item.amount = amount
    item.account_id = account_id
    item.day_of_month = day
    item.owner = owner
    item.notes = (data.get("notes") or "").strip() or None
    item.sort_order = sort_order
    item.is_active = str(data.get("is_active", "1")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
