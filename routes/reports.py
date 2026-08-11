"""Reports routes — monthly / quarterly / yearly summaries."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, current_app, render_template, request

from services import report_service

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")


def _parse_period() -> tuple[str, int, int, int]:
    today = date.today()
    period = (request.args.get("period") or "monthly").lower()
    if period not in ("monthly", "quarterly", "yearly"):
        period = "monthly"
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    quarter = request.args.get("quarter", type=int) or ((today.month - 1) // 3 + 1)
    month = max(1, min(12, month))
    quarter = max(1, min(4, quarter))
    return period, year, month, quarter


@reports_bp.route("/")
def index():
    period, year, month, quarter = _parse_period()
    today = date.today()

    summary = report_service.get_period_summary(
        period=period, year=year, month=month, quarter=quarter
    )
    comparison = report_service.previous_period_comparison(summary)
    top_expenses = report_service.get_top_expenses(summary["start"], summary["end"])
    cashflow = report_service.get_cashflow_trend(12)
    currency = current_app.config["CURRENCY_SYMBOL"]

    return render_template(
        "reports/index.html",
        summary=summary,
        comparison=comparison,
        top_expenses=top_expenses,
        cashflow=cashflow,
        currency=currency,
        years=list(range(today.year, today.year - 5, -1)),
        page_title="Reports",
        active_nav="reports",
    )


@reports_bp.route("/expenses")
def expenses_detail():
    """All expense/refund transactions behind Net Expenses for the period."""
    period, year, month, quarter = _parse_period()
    activity = report_service.get_net_expense_activity(
        period=period, year=year, month=month, quarter=quarter
    )
    prev = report_service.previous_period_params(activity)
    nxt = report_service.next_period_params(activity)
    from_dashboard = (request.args.get("from") or "").lower() == "dashboard"
    if from_dashboard:
        prev = {**prev, "from": "dashboard"}
        nxt = {**nxt, "from": "dashboard"}
    back = {
        "period": activity["period"],
        "year": activity["year"],
        "month": activity["month"],
        "quarter": activity["quarter"],
    }

    return render_template(
        "reports/expenses.html",
        activity=activity,
        rows=activity["rows"],
        prev=prev,
        nxt=nxt,
        back=back,
        from_dashboard=from_dashboard,
        page_title=f"Net expenses · {activity['label']}",
        active_nav="dashboard" if from_dashboard else "reports",
    )
