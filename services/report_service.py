"""Report service — monthly / quarterly / yearly financial summaries."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func

from extensions import db
from models import Category, Transaction

Period = Literal["monthly", "quarterly", "yearly"]


def _sum_type(txn_type: str, start: date, end: date) -> Decimal:
    result = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.transaction_type == txn_type,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .scalar()
    )
    return Decimal(result or 0)


def _period_bounds(
    period: Period, year: int, month: int | None = None, quarter: int | None = None
) -> tuple[date, date, str]:
    if period == "monthly":
        month = month or date.today().month
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        label = start.strftime("%B %Y")
        return start, end, label

    if period == "quarterly":
        quarter = quarter or ((date.today().month - 1) // 3 + 1)
        start_month = (quarter - 1) * 3 + 1
        start = date(year, start_month, 1)
        end_month = start_month + 2
        end = date(year, end_month, monthrange(year, end_month)[1])
        label = f"Q{quarter} {year}"
        return start, end, label

    # yearly
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    label = str(year)
    return start, end, label


def get_period_summary(
    period: Period = "monthly",
    year: int | None = None,
    month: int | None = None,
    quarter: int | None = None,
) -> dict[str, Any]:
    today = date.today()
    year = year or today.year
    start, end, label = _period_bounds(period, year, month, quarter)

    income = _sum_type("income", start, end)
    expenses = _sum_type("expense", start, end)
    investments = _sum_type("investment", start, end)
    refunds = _sum_type("refund", start, end)
    transfers = _sum_type("transfer", start, end)

    net_expenses = expenses - refunds
    savings = income - net_expenses
    savings_rate = float((savings / income) * 100) if income > 0 else 0.0
    invest_rate = float((investments / income) * 100) if income > 0 else 0.0

    category_rows = (
        db.session.query(
            Category.name,
            Category.color,
            Category.icon,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(
            Transaction.transaction_type == "expense",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .group_by(Category.id)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )
    by_category = [
        {
            "name": name,
            "color": color or "#64748b",
            "icon": icon or "bi-tag",
            "total": float(total or 0),
        }
        for name, color, icon, total in category_rows
    ]

    need_want = (
        db.session.query(
            Transaction.need_want,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .filter(
            Transaction.transaction_type == "expense",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .group_by(Transaction.need_want)
        .all()
    )
    need_want_map = {nw: float(total or 0) for nw, total in need_want}

    paid_by = (
        db.session.query(
            Transaction.paid_by,
            func.coalesce(func.sum(Transaction.amount), 0).label("total"),
        )
        .filter(
            Transaction.transaction_type == "expense",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .group_by(Transaction.paid_by)
        .all()
    )
    paid_by_map = {pb: float(total or 0) for pb, total in paid_by}

    txn_count = (
        Transaction.query.filter(
            Transaction.date >= start, Transaction.date <= end
        ).count()
    )

    return {
        "period": period,
        "year": year,
        "month": month or today.month,
        "quarter": quarter or ((today.month - 1) // 3 + 1),
        "label": label,
        "start": start,
        "end": end,
        "income": income,
        "expenses": expenses,
        "refunds": refunds,
        "net_expenses": net_expenses,
        "investments": investments,
        "transfers": transfers,
        "savings": savings,
        "savings_rate": round(savings_rate, 1),
        "invest_rate": round(invest_rate, 1),
        "by_category": by_category,
        "need_want": {
            "need": need_want_map.get("need", 0.0),
            "want": need_want_map.get("want", 0.0),
            "n/a": need_want_map.get("n/a", 0.0),
        },
        "paid_by": {
            "self": paid_by_map.get("self", 0.0),
            "wife": paid_by_map.get("wife", 0.0),
            "joint": paid_by_map.get("joint", 0.0),
        },
        "transaction_count": txn_count,
    }


def get_cashflow_trend(months: int = 12) -> list[dict[str, Any]]:
    """Income / expense / investment by month for charts."""
    today = date.today()
    results = []
    for i in range(months - 1, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
        income = _sum_type("income", start, end)
        expenses = _sum_type("expense", start, end)
        investments = _sum_type("investment", start, end)
        results.append(
            {
                "label": start.strftime("%b %Y"),
                "income": float(income),
                "expenses": float(expenses),
                "investments": float(investments),
                "savings": float(income - expenses - investments),
            }
        )
    return results


def get_top_expenses(start: date, end: date, limit: int = 10) -> list[Transaction]:
    return (
        Transaction.query.filter(
            Transaction.transaction_type == "expense",
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .order_by(Transaction.amount.desc())
        .limit(limit)
        .all()
    )


def get_net_expense_activity(
    *,
    period: Period = "monthly",
    year: int | None = None,
    month: int | None = None,
    quarter: int | None = None,
    limit: int = 400,
) -> dict[str, Any]:
    """Expenses (+ refunds) that make up Reports Net Expenses for a period."""
    summary = get_period_summary(
        period=period, year=year, month=month, quarter=quarter
    )
    start: date = summary["start"]
    end: date = summary["end"]

    txns = (
        Transaction.query.filter(
            Transaction.transaction_type.in_(("expense", "refund")),
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    truncated = len(txns) > limit
    rows = txns[:limit]

    return {
        "period": summary["period"],
        "year": summary["year"],
        "month": summary["month"],
        "quarter": summary["quarter"],
        "label": summary["label"],
        "start": start,
        "end": end,
        "rows": rows,
        "truncated": truncated,
        "totals": {
            "expenses": Decimal(summary["expenses"]),
            "refunds": Decimal(summary["refunds"]),
            "net_expenses": Decimal(summary["net_expenses"]),
            "income": Decimal(summary["income"]),
        },
    }


def previous_period_params(summary: dict[str, Any]) -> dict[str, Any]:
    """Query args for the period immediately before summary."""
    period = summary["period"]
    year = summary["year"]
    if period == "monthly":
        month = summary["month"]
        if month == 1:
            return {"period": period, "year": year - 1, "month": 12}
        return {"period": period, "year": year, "month": month - 1}
    if period == "quarterly":
        quarter = summary["quarter"]
        if quarter == 1:
            return {"period": period, "year": year - 1, "quarter": 4}
        return {"period": period, "year": year, "quarter": quarter - 1}
    return {"period": "yearly", "year": year - 1}


def next_period_params(summary: dict[str, Any]) -> dict[str, Any]:
    """Query args for the period immediately after summary."""
    period = summary["period"]
    year = summary["year"]
    if period == "monthly":
        month = summary["month"]
        if month == 12:
            return {"period": period, "year": year + 1, "month": 1}
        return {"period": period, "year": year, "month": month + 1}
    if period == "quarterly":
        quarter = summary["quarter"]
        if quarter == 4:
            return {"period": period, "year": year + 1, "quarter": 1}
        return {"period": period, "year": year, "quarter": quarter + 1}
    return {"period": "yearly", "year": year + 1}


def previous_period_comparison(summary: dict[str, Any]) -> dict[str, Any]:
    """Compare current period totals to the immediately preceding period."""
    period = summary["period"]
    year = summary["year"]

    if period == "monthly":
        month = summary["month"]
        if month == 1:
            prev = get_period_summary("monthly", year=year - 1, month=12)
        else:
            prev = get_period_summary("monthly", year=year, month=month - 1)
    elif period == "quarterly":
        quarter = summary["quarter"]
        if quarter == 1:
            prev = get_period_summary("quarterly", year=year - 1, quarter=4)
        else:
            prev = get_period_summary("quarterly", year=year, quarter=quarter - 1)
    else:
        prev = get_period_summary("yearly", year=year - 1)

    def delta(current: Decimal, previous: Decimal) -> dict[str, float]:
        change = float(current - previous)
        pct = (change / float(previous) * 100) if previous != 0 else 0.0
        return {"change": change, "pct": round(pct, 1)}

    return {
        "prev_label": prev["label"],
        "income": delta(summary["income"], prev["income"]),
        "expenses": delta(summary["net_expenses"], prev["net_expenses"]),
        "investments": delta(summary["investments"], prev["investments"]),
        "savings": delta(summary["savings"], prev["savings"]),
    }
