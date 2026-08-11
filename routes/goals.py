"""Goal routes."""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from models import Account, Goal
from services import goal_service
from services.goal_service import GoalValidationError

goals_bp = Blueprint("goals", __name__, url_prefix="/goals")


def _form_context():
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return {
        "goal_types": Goal.GOAL_TYPES,
        "owners": Goal.OWNERS,
        "accounts": accounts,
        "currency": current_app.config["CURRENCY_SYMBOL"],
    }


@goals_bp.route("/")
def index():
    overview = goal_service.get_goals_overview()
    return render_template(
        "goals/index.html",
        overview=overview,
        page_title="Goals",
        active_nav="goals",
    )


@goals_bp.route("/<int:goal_id>")
def detail(goal_id: int):
    detail_data = goal_service.get_goal_detail(goal_id)
    if not detail_data:
        flash("Goal not found.", "danger")
        return redirect(url_for("goals.index"))
    goal = detail_data["goal"]
    return render_template(
        "goals/detail.html",
        detail=detail_data,
        page_title=goal.name,
        active_nav="goals",
    )


@goals_bp.route("/new", methods=["GET", "POST"])
def create():
    ctx = _form_context()
    if request.method == "POST":
        try:
            goal = goal_service.create_goal(request.form.to_dict())
            flash(f"Created goal: {goal.name}", "success")
            return redirect(url_for("goals.index"))
        except GoalValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "goals/form.html",
                goal=None,
                form_data=request.form,
                page_title="Add Goal",
                active_nav="goals",
                **ctx,
            )
    return render_template(
        "goals/form.html",
        goal=None,
        form_data={"goal_type": "custom", "owner": "joint", "is_active": "1"},
        page_title="Add Goal",
        active_nav="goals",
        **ctx,
    )


@goals_bp.route("/<int:goal_id>/edit", methods=["GET", "POST"])
def edit(goal_id: int):
    goal = goal_service.get_goal(goal_id)
    if not goal:
        flash("Goal not found.", "danger")
        return redirect(url_for("goals.index"))

    ctx = _form_context()
    if request.method == "POST":
        try:
            goal_service.update_goal(goal, request.form.to_dict())
            flash(f"Updated {goal.name}.", "success")
            return redirect(url_for("goals.index"))
        except GoalValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "goals/form.html",
                goal=goal,
                form_data=request.form,
                page_title="Edit Goal",
                active_nav="goals",
                **ctx,
            )

    form_data = {
        "name": goal.name,
        "goal_type": goal.goal_type,
        "target_amount": goal.target_amount,
        "current_amount": goal.current_amount,
        "monthly_contribution": goal.monthly_contribution,
        "target_date": goal.target_date.isoformat() if goal.target_date else "",
        "linked_account_id": goal.linked_account_id or "",
        "owner": goal.owner,
        "icon": goal.icon,
        "color": goal.color,
        "notes": goal.notes or "",
        "is_active": "1" if goal.is_active else "0",
    }
    return render_template(
        "goals/form.html",
        goal=goal,
        form_data=form_data,
        page_title="Edit Goal",
        active_nav="goals",
        **ctx,
    )


@goals_bp.route("/<int:goal_id>/delete", methods=["POST"])
def delete(goal_id: int):
    goal = goal_service.get_goal(goal_id)
    if not goal:
        flash("Goal not found.", "danger")
    else:
        name = goal.name
        goal_service.delete_goal(goal)
        flash(f"Deleted {name}.", "success")
    return redirect(url_for("goals.index"))
