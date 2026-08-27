"""Insurance routes — list, create, edit, delete."""

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

from models import Account, Insurance
from services import insurance_service
from services.insurance_service import (
    FREQUENCY_LABELS,
    POLICY_LABELS,
    InsuranceValidationError,
)

insurance_bp = Blueprint("insurance", __name__, url_prefix="/insurance")


def _form_context():
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return {
        "policy_types": Insurance.POLICY_TYPES,
        "policy_labels": POLICY_LABELS,
        "premium_frequencies": Insurance.PREMIUM_FREQUENCIES,
        "frequency_labels": FREQUENCY_LABELS,
        "owners": Insurance.OWNERS,
        "accounts": accounts,
        "currency": current_app.config["CURRENCY_SYMBOL"],
    }


@insurance_bp.route("/")
def index():
    overview = insurance_service.get_overview()
    premium_status = insurance_service.get_premium_month_status()
    return render_template(
        "insurance/index.html",
        overview=overview,
        premium_status=premium_status,
        policy_labels=POLICY_LABELS,
        frequency_labels=FREQUENCY_LABELS,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Insurance",
        active_nav="insurance",
    )


@insurance_bp.route("/post-premiums", methods=["POST"])
def post_premiums():
    result = insurance_service.post_month_premiums()
    created = result["created_count"]
    if created:
        flash(
            f"Posted {created} insurance premium"
            f"{'s' if created != 1 else ''} for {result['label']}.",
            "success",
        )
    elif result["errors"]:
        flash("Could not post insurance premiums — see details below.", "danger")
    else:
        flash(f"No insurance premiums to post for {result['label']}.", "info")

    for err in result["errors"]:
        flash(err, "danger")
    if not created and not result["errors"] and result["skipped"]:
        for msg in result["skipped"][:5]:
            flash(msg, "secondary")

    return redirect(request.referrer or url_for("insurance.index"))


@insurance_bp.route("/new", methods=["GET", "POST"])
def create():
    ctx = _form_context()
    if request.method == "POST":
        try:
            item = insurance_service.create_policy(request.form.to_dict())
            flash(f"Added policy: {item.name}", "success")
            return redirect(url_for("insurance.index"))
        except InsuranceValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "policy_type": "health",
            "premium_frequency": "yearly",
            "owner": "joint",
            "is_active": "1",
        }

    return render_template(
        "insurance/form.html",
        policy=None,
        form_data=form_data,
        page_title="Add Policy",
        active_nav="insurance",
        **ctx,
    )


@insurance_bp.route("/<int:policy_id>/edit", methods=["GET", "POST"])
def edit(policy_id: int):
    item = insurance_service.get_policy(policy_id)
    if not item:
        flash("Policy not found.", "danger")
        return redirect(url_for("insurance.index"))

    ctx = _form_context()
    if request.method == "POST":
        try:
            insurance_service.update_policy(item, request.form.to_dict())
            flash(f"Updated {item.name}.", "success")
            return redirect(url_for("insurance.index"))
        except InsuranceValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "name": item.name,
            "policy_type": item.policy_type,
            "insurer": item.insurer or "",
            "policy_number": item.policy_number or "",
            "cover_amount": item.cover_amount,
            "premium_amount": item.premium_amount,
            "premium_frequency": item.premium_frequency,
            "next_renewal_date": (
                item.next_renewal_date.isoformat() if item.next_renewal_date else ""
            ),
            "coverage_start_date": (
                item.coverage_start_date.isoformat()
                if item.coverage_start_date
                else ""
            ),
            "coverage_end_date": (
                item.coverage_end_date.isoformat() if item.coverage_end_date else ""
            ),
            "premium_paying_term_years": item.premium_paying_term_years or "",
            "policy_term_years": item.policy_term_years or "",
            "source_account_id": item.source_account_id or "",
            "owner": item.owner,
            "notes": item.notes or "",
            "sort_order": item.sort_order,
            "is_active": "1" if item.is_active else "0",
        }

    return render_template(
        "insurance/form.html",
        policy=item,
        form_data=form_data,
        page_title="Edit Policy",
        active_nav="insurance",
        **ctx,
    )


@insurance_bp.route("/<int:policy_id>/delete", methods=["POST"])
def delete(policy_id: int):
    item = insurance_service.get_policy(policy_id)
    if not item:
        flash("Policy not found.", "danger")
    else:
        name = item.name
        insurance_service.delete_policy(item)
        flash(f"Deleted {name}.", "success")
    return redirect(url_for("insurance.index"))
