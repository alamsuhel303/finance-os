"""Dashboard aggregation service."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func

from extensions import db
from models import Account, Category, Goal, Investment, Transaction
from services import envelope_service, health_service, net_worth_service, reminder_service


def _month_bounds(ref: date | None = None) -> tuple[date, date]:
    ref = ref or date.today()
    start = ref.replace(day=1)
    end = ref.replace(day=monthrange(ref.year, ref.month)[1])
    return start, end


def _sum_by_type(
    transaction_type: str, start: date, end: date
) -> Decimal:
    result = (
        db.session.query(func.coalesce(func.sum(Transaction.amount), 0))
        .filter(
            Transaction.transaction_type == transaction_type,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .scalar()
    )
    return Decimal(result or 0)


def get_dashboard_summary() -> dict[str, Any]:
    today = date.today()
    start, end = _month_bounds(today)

    income = _sum_by_type("income", start, end)
    expenses = _sum_by_type("expense", start, end)
    investments = _sum_by_type("investment", start, end)
    refunds = _sum_by_type("refund", start, end)

    net_expenses = expenses - refunds
    # Investments count as saving (not subtracted from the rate)
    savings = income - net_expenses
    savings_rate = (
        float((savings / income) * 100) if income > 0 else 0.0
    )

    all_accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order)
        .all()
    )
    # Hide optional fund accounts (Travel/Home/Lifestyle…) when empty
    spending_types = {"bank", "cash", "joint", "salary"}
    accounts = [
        a
        for a in all_accounts
        if a.account_type in spending_types
        or Decimal(a.current_balance or 0) != 0
    ]
    live_nw = net_worth_service.compute_live_net_worth()
    net_worth = live_nw["net_worth"]

    recent = (
        Transaction.query.order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(8)
        .all()
    )

    goal_cards = _goal_cards_for_dashboard(net_expenses)

    portfolio_current = sum(
        (
            Decimal(i.current_value or 0)
            for i in Investment.query.filter_by(is_active=True).all()
        ),
        Decimal("0"),
    )

    envelopes = envelope_service.get_envelopes_overview()
    investment_purpose = envelope_service.investments_by_purpose()
    joint_account = envelopes.get("joint_account")
    joint_balance = Decimal(envelopes.get("joint_balance") or 0)

    return {
        "net_worth": net_worth,
        "monthly_income": income,
        "monthly_expenses": net_expenses,
        "monthly_investments": investments,
        "savings": savings,
        "savings_rate": round(savings_rate, 1),
        "joint_balance": joint_balance,
        "joint_account_name": joint_account.name if joint_account else "Joint Account",
        "joint_unlabelled": Decimal(envelopes.get("difference") or 0)
        if envelopes.get("difference", 0) > 0
        else Decimal("0"),
        "goal_cards": goal_cards,
        "portfolio_current": portfolio_current,
        "liabilities": live_nw["liabilities"],
        "accounts": accounts,
        "envelopes": envelopes,
        "investment_purpose": investment_purpose,
        "recent_transactions": recent,
        "month_label": today.strftime("%B %Y"),
        "year": today.year,
        "month": today.month,
        "health": health_service.compute_health_score(today),
        "reminders": reminder_service.get_month_reminders(today),
    }


def _goal_cards_for_dashboard(monthly_expenses: Decimal) -> list[dict[str, Any]]:
    """Prefer Goal records; fall back to account balances for emergency/home/travel."""
    goals = (
        Goal.query.filter_by(is_active=True)
        .order_by(Goal.sort_order)
        .limit(3)
        .all()
    )
    if goals:
        return [
            {
                "name": g.name,
                "current": g.effective_current,
                "progress": round(g.progress_pct, 1),
                "meta": (
                    f"Target {float(g.target_amount or 0):,.0f}"
                    if g.target_amount
                    else "In progress"
                ),
            }
            for g in goals
        ]

    from services import emergency_service

    accounts = Account.query.filter_by(is_active=True).all()
    home_fund = next((a for a in accounts if a.name == "Home Fund"), None)
    travel_fund = next((a for a in accounts if a.name == "Travel Fund"), None)
    emergency_balance = emergency_service.get_total()
    emergency_target = monthly_expenses * 6 if monthly_expenses > 0 else Decimal("0")
    emergency_progress = 0.0
    if emergency_target > 0:
        emergency_progress = min(
            100.0, float((emergency_balance / emergency_target) * 100)
        )

    return [
        {
            "name": "Emergency",
            "current": emergency_balance,
            "progress": emergency_progress,
            "meta": "Tagged cash + emergency investments",
        },
        {
            "name": "Home Fund",
            "current": Decimal(home_fund.current_balance or 0) if home_fund else Decimal("0"),
            "progress": 0.0,
            "meta": "Set a goal target",
        },
        {
            "name": "Travel Fund",
            "current": Decimal(travel_fund.current_balance or 0)
            if travel_fund
            else Decimal("0"),
            "progress": 0.0,
            "meta": "Set a goal target",
        },
    ]


def get_expense_by_category(months_back: int = 0) -> list[dict[str, Any]]:
    """Category breakdown for the current (or offset) month."""
    ref = date.today().replace(day=1)
    for _ in range(months_back):
        ref = (ref - timedelta(days=1)).replace(day=1)
    start, end = _month_bounds(ref)

    rows = (
        db.session.query(
            Category.name,
            Category.color,
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
    return [
        {"name": name, "color": color or "#64748b", "total": float(total or 0)}
        for name, color, total in rows
    ]


def get_monthly_expense_trend(months: int = 6) -> list[dict[str, Any]]:
    """Last N months of expense totals for charts."""
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
        total = _sum_by_type("expense", start, end)
        results.append(
            {
                "label": start.strftime("%b %Y"),
                "total": float(total),
            }
        )
    return results
