"""Budget routes."""

from __future__ import annotations

from datetime import date

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from services import budget_service, envelope_service
from services.budget_service import BudgetValidationError

budget_bp = Blueprint("budget", __name__, url_prefix="/budget")


def _parse_ym() -> tuple[int, int]:
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    month = max(1, min(12, month))
    return year, month


@budget_bp.route("/")
def index():
    year, month = _parse_ym()
    overview = budget_service.get_budget_overview(year, month)
    envelopes = envelope_service.get_envelopes_overview(year, month)
    # First-time setup always opens unlocked; otherwise ?edit=1
    edit_mode = overview["needs_setup"] or request.args.get("edit") in ("1", "true", "yes")

    # Adjacent months for navigation
    if month == 1:
        prev = {"year": year - 1, "month": 12}
    else:
        prev = {"year": year, "month": month - 1}
    if month == 12:
        nxt = {"year": year + 1, "month": 1}
    else:
        nxt = {"year": year, "month": month + 1}

    funding_gaps = budget_service.envelope_funding_gaps(overview, envelopes)

    return render_template(
        "budget/index.html",
        overview=overview,
        envelopes=envelopes,
        funding_gaps=funding_gaps,
        edit_mode=edit_mode,
        prev=prev,
        nxt=nxt,
        page_title="Budget",
        active_nav="budget",
    )


def _adjacent_months(year: int, month: int) -> tuple[int, int, int, int]:
    if month == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, month - 1
    if month == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, month + 1
    return prev_y, prev_m, next_y, next_m


@budget_bp.route("/spent")
def spent_detail():
    """All transactions that make up the Budget Spent total for a month."""
    year, month = _parse_ym()
    activity = budget_service.get_month_spent_activity(year=year, month=month)
    prev_y, prev_m, next_y, next_m = _adjacent_months(year, month)

    return render_template(
        "budget/spent.html",
        activity=activity,
        rows=activity["rows"],
        prev_year=prev_y,
        prev_month=prev_m,
        next_year=next_y,
        next_month=next_m,
        page_title=f"Budget spent · {activity['month_label']}",
        active_nav="budget",
    )


@budget_bp.route("/category/<int:category_id>")
def category_detail(category_id: int):
    year, month = _parse_ym()
    activity = budget_service.get_category_month_activity(
        category_id, year=year, month=month
    )
    if not activity["category"]:
        abort(404)

    prev_y, prev_m, next_y, next_m = _adjacent_months(year, month)

    return render_template(
        "budget/detail.html",
        activity=activity,
        category=activity["category"],
        rows=activity["rows"],
        prev_year=prev_y,
        prev_month=prev_m,
        next_year=next_y,
        next_month=next_m,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title=activity["category"].name,
        active_nav="budget",
    )


@budget_bp.route("/save", methods=["POST"])
def save():
    try:
        year = int(request.form.get("year", date.today().year))
        month = int(request.form.get("month", date.today().month))
    except (TypeError, ValueError):
        flash("Invalid month selection.", "danger")
        return redirect(url_for("budget.index"))

    amounts: dict[int, str] = {}
    names: dict[int, str] = {}
    for key, value in request.form.items():
        if key.startswith("amount_"):
            try:
                cat_id = int(key.removeprefix("amount_"))
            except ValueError:
                continue
            amounts[cat_id] = value
        elif key.startswith("name_"):
            try:
                cat_id = int(key.removeprefix("name_"))
            except ValueError:
                continue
            names[cat_id] = value

    try:
        renamed = budget_service.rename_budget_categories(names)
        touched = budget_service.upsert_budgets(year, month, amounts)
        parts = []
        if renamed:
            parts.append(f"renamed {renamed}")
        if touched:
            parts.append(f"updated {touched} amount(s)")
        if parts:
            flash("Budget saved & locked — " + ", ".join(parts) + ".", "success")
        else:
            flash("Budget locked. No amount/name changes were needed.", "success")
    except BudgetValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("budget.index", year=year, month=month, edit=1))

    return redirect(url_for("budget.index", year=year, month=month))


@budget_bp.route("/add-row", methods=["POST"])
def add_row():
    try:
        year = int(request.form.get("year", date.today().year))
        month = int(request.form.get("month", date.today().month))
    except (TypeError, ValueError):
        flash("Invalid month selection.", "danger")
        return redirect(url_for("budget.index"))

    raw_cat = (request.form.get("category_id") or "").strip()
    category_id = int(raw_cat) if raw_cat.isdigit() else None
    try:
        msg = budget_service.add_budget_row(
            year,
            month,
            category_id=category_id,
            name=request.form.get("name"),
            amount=request.form.get("amount"),
        )
        flash(msg, "success")
    except BudgetValidationError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("budget.index", year=year, month=month, edit=1))


@budget_bp.route("/remove-row", methods=["POST"])
def remove_row():
    try:
        year = int(request.form.get("year", date.today().year))
        month = int(request.form.get("month", date.today().month))
        category_id = int(request.form.get("category_id"))
    except (TypeError, ValueError):
        flash("Invalid remove request.", "danger")
        return redirect(url_for("budget.index"))

    try:
        flash(budget_service.remove_budget_row(year, month, category_id), "success")
    except BudgetValidationError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("budget.index", year=year, month=month, edit=1))


@budget_bp.route("/copy-previous", methods=["POST"])
def copy_previous():
    try:
        year = int(request.form.get("year", date.today().year))
        month = int(request.form.get("month", date.today().month))
    except (TypeError, ValueError):
        flash("Invalid month selection.", "danger")
        return redirect(url_for("budget.index"))

    count = budget_service.copy_budgets_from_previous(year, month)
    if count:
        flash(f"Reset {count} category budget(s) from the latest prior month.", "success")
    else:
        flash("No earlier budgets found to copy from. Set amounts below and save.", "warning")

    return redirect(url_for("budget.index", year=year, month=month, edit=1))
