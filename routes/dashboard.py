"""Dashboard routes."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template

from services import dashboard_service

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    summary = dashboard_service.get_dashboard_summary()
    expense_categories = dashboard_service.get_expense_by_category()
    expense_trend = dashboard_service.get_monthly_expense_trend(6)
    currency = current_app.config["CURRENCY_SYMBOL"]

    return render_template(
        "dashboard.html",
        summary=summary,
        expense_categories=expense_categories,
        expense_trend=expense_trend,
        currency=currency,
        page_title="Dashboard",
        active_nav="dashboard",
    )
