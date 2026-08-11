"""Financial Health Score — weighted 0–100 household fitness gauge."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func

from extensions import db
from models import Goal, Liability, Transaction
from services import budget_service, net_worth_service


# Weights sum to 1.0
FACTOR_WEIGHTS = {
    "emergency": 0.20,
    "savings": 0.20,
    "investment": 0.15,
    "debt": 0.15,
    "goals": 0.15,
    "budget": 0.15,
}


def compute_health_score(ref: date | None = None) -> dict[str, Any]:
    """
    Full Financial Health Score with factor breakdown.

    Returns:
      score (0–100), label, factors[{key, label, score, weight, detail, tip}]
    """
    ref = ref or date.today()
    start = ref.replace(day=1)
    end = ref.replace(day=monthrange(ref.year, ref.month)[1])

    income = _sum_type("income", start, end)
    expenses = _sum_type("expense", start, end) - _sum_type("refund", start, end)
    investments = _sum_type("investment", start, end)
    if expenses < 0:
        expenses = Decimal("0")

    factors = [
        _score_emergency(expenses),
        _score_savings(income, expenses),
        _score_investment(income, investments),
        _score_debt(income),
        _score_goals(),
        _score_budget(ref.year, ref.month),
    ]

    total = 0.0
    for f in factors:
        total += f["score"] * f["weight"]
    score = int(round(max(0.0, min(100.0, total))))

    return {
        "score": score,
        "label": _label(score),
        "month_label": ref.strftime("%B %Y"),
        "factors": factors,
        "weights": FACTOR_WEIGHTS,
    }


def _score_emergency(monthly_expenses: Decimal) -> dict[str, Any]:
    """Target: 6 months of expenses via tagged cash + emergency-purpose investments."""
    from services import emergency_service

    balance = emergency_service.get_total()

    if monthly_expenses <= 0:
        score = 70.0 if balance > 0 else 40.0
        detail = "Add some expenses this month to measure cover"
        tip = "Emergency money is what you tagged on Accounts / Investments"
    else:
        months = float(balance / monthly_expenses)
        if months >= 6:
            score = 100.0
            detail = "Fully covered — 6+ months of expenses ready"
        elif months >= 3:
            score = 55.0 + (months - 3) / 3 * 45.0
            detail = f"About {months:.0f} months of expenses ready (aim for 6)"
        elif months >= 1:
            score = 25.0 + (months - 1) / 2 * 30.0
            detail = f"About {months:.0f} month of expenses ready (aim for 6)"
        else:
            score = min(25.0, months * 25.0)
            detail = "Less than 1 month of expenses ready (aim for 6)"
        tip = "Tag cash on Accounts or set investment Purpose → Emergency"

    return _factor("emergency", "Emergency cushion", score, detail, tip)


def _score_savings(income: Decimal, expenses: Decimal) -> dict[str, Any]:
    """Share of income left after spending (investments count as kept)."""
    if income <= 0:
        return _factor(
            "savings",
            "Money kept",
            30.0,
            "No income logged this month",
            "Post salary so we can measure what you kept",
        )
    rate = float((income - expenses) / income) * 100
    # 30%+ = 100, 20% = 80, 10% = 50, 0% = 25, negative scales down
    if rate >= 30:
        score = 100.0
    elif rate >= 20:
        score = 80.0 + (rate - 20) / 10 * 20.0
    elif rate >= 10:
        score = 50.0 + (rate - 10) / 10 * 30.0
    elif rate >= 0:
        score = 25.0 + rate / 10 * 25.0
    else:
        score = max(0.0, 25.0 + rate)  # rate negative
    if rate >= 0:
        detail = f"Kept {rate:.0f}% of income after spending"
    else:
        detail = "Spent more than you earned this month"
    tip = "A healthy habit is keeping ~20% or more"
    return _factor("savings", "Money kept", score, detail, tip)


def _score_investment(income: Decimal, investments: Decimal) -> dict[str, Any]:
    """Share of income going to investments this month."""
    if income <= 0:
        return _factor(
            "investment",
            "Investment rate",
            30.0,
            "No income to compare",
            "Post SIPs / EPF or log investment txns",
        )
    rate = float(investments / income) * 100
    if rate >= 20:
        score = 100.0
    elif rate >= 10:
        score = 70.0 + (rate - 10) / 10 * 30.0
    elif rate >= 5:
        score = 40.0 + (rate - 5) / 5 * 30.0
    else:
        score = rate / 5 * 40.0
    return _factor(
        "investment",
        "Invested from income",
        score,
        f"{rate:.0f}% of income went into investments",
        "Aim for about 10–20% via SIPs / EPF",
    )


def _score_debt(monthly_income: Decimal) -> dict[str, Any]:
    """Lower debt burden = higher score. Uses liabilities vs annualized income & assets."""
    debt = (
        db.session.query(func.coalesce(func.sum(Liability.outstanding_amount), 0))
        .filter(Liability.is_active.is_(True))
        .scalar()
    )
    debt = Decimal(debt or 0)
    nw = net_worth_service.compute_live_net_worth()
    assets = Decimal(nw["total_assets"] or 0)

    if debt <= 0:
        return _factor(
            "debt",
            "Debt load",
            100.0,
            "No active liabilities",
            "Keep loans tracked if you add any",
        )

    annual_income = monthly_income * 12
    if annual_income > 0:
        dti = float(debt / annual_income)  # debt / annual income
        # 0 = 100, 1x = 70, 2x = 40, 3x+ = 10
        if dti <= 0.5:
            score = 100.0 - dti / 0.5 * 15.0
        elif dti <= 1.5:
            score = 85.0 - (dti - 0.5) / 1.0 * 35.0
        elif dti <= 3:
            score = 50.0 - (dti - 1.5) / 1.5 * 35.0
        else:
            score = max(5.0, 15.0 - (dti - 3) * 5.0)
        detail = f"Debt is {dti:.1f}× annual income"
    elif assets > 0:
        ratio = float(debt / assets)
        score = max(10.0, 100.0 - ratio * 100.0)
        detail = f"Debt is {ratio * 100:.0f}% of assets"
    else:
        score = 40.0
        detail = f"Outstanding {float(debt):,.0f}"

    return _factor(
        "debt",
        "Debt load",
        score,
        detail,
        "Add loans on Net Worth · keep DTI under 1× income",
    )


def _score_goals() -> dict[str, Any]:
    """Average progress across active goals with a target."""
    goals = Goal.query.filter_by(is_active=True).all()
    scored = [g for g in goals if Decimal(g.target_amount or 0) > 0]
    if not scored:
        return _factor(
            "goals",
            "Goal progress",
            40.0,
            "No goals with targets yet",
            "Set targets on Goals page",
        )
    avg = sum(g.progress_pct for g in scored) / len(scored)
    # Map 0–100 progress to score (being on track at 50% progress mid-journey still scores well)
    # Use raw average progress as the score component — simple and transparent
    score = min(100.0, float(avg))
    on_track = sum(1 for g in scored if g.progress_pct >= 50)
    detail = f"{avg:.0f}% avg · {on_track}/{len(scored)} at 50%+"
    return _factor(
        "goals",
        "Goal progress",
        score,
        detail,
        "Keep monthly contributions flowing",
    )


def _score_budget(year: int, month: int) -> dict[str, Any]:
    """How well this month’s spend stays within category limits."""
    overview = budget_service.get_budget_overview(year, month)
    budgeted = Decimal(overview.get("total_budgeted") or 0)
    actual = Decimal(overview.get("total_actual") or 0)
    over_count = int(overview.get("over_count") or 0)
    cats = int(overview.get("categories_with_budget") or 0)

    if budgeted <= 0:
        return _factor(
            "budget",
            "Budget discipline",
            45.0,
            "No category budgets set this month",
            "Set limits on the Budget page",
        )

    usage = float(actual / budgeted) * 100  # % of budget used
    # Under 80% used = excellent; 100% = good; over = penalty
    if usage <= 80:
        score = 100.0
    elif usage <= 100:
        score = 100.0 - (usage - 80) / 20 * 25.0  # 100 → 75
    elif usage <= 120:
        score = 75.0 - (usage - 100) / 20 * 40.0  # 75 → 35
    else:
        score = max(10.0, 35.0 - (usage - 120) / 20 * 25.0)

    if over_count:
        score = max(10.0, score - min(25.0, over_count * 5.0))

    detail = f"{usage:.0f}% of limits used"
    if over_count:
        detail += f" · {over_count} over"
    elif cats:
        detail += f" · {cats} categories"

    return _factor(
        "budget",
        "Budget discipline",
        score,
        detail,
        "Stay within category limits",
    )


def _factor(
    key: str, label: str, score: float, detail: str, tip: str
) -> dict[str, Any]:
    score = max(0.0, min(100.0, float(score)))
    return {
        "key": key,
        "label": label,
        "score": round(score, 1),
        "weight": FACTOR_WEIGHTS[key],
        "weight_pct": int(FACTOR_WEIGHTS[key] * 100),
        "detail": detail,
        "tip": tip,
        "status": (
            "ok" if score >= 75 else "watch" if score >= 45 else "risk"
        ),
    }


def _label(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Strong"
    if score >= 55:
        return "Good"
    if score >= 40:
        return "Building"
    return "Needs attention"


def _sum_type(transaction_type: str, start: date, end: date) -> Decimal:
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
