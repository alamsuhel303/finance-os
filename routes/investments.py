"""Investment routes."""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db
from models import Goal, Investment
from services import account_service, investment_service, nav_service
from services.investment_service import ASSET_LABELS, InvestmentValidationError
from services.nav_service import NavServiceError
from sqlalchemy import func

investments_bp = Blueprint("investments", __name__, url_prefix="/investments")


def _form_context():
    from services import profile_service

    goals = Goal.query.filter_by(is_active=True).order_by(Goal.sort_order, Goal.name).all()
    accounts = account_service.list_accounts(active_only=True)
    owner_labels = {**profile_service.get_owner_labels(), "joint": "Joint"}
    return {
        "asset_types": Investment.ASSET_TYPES,
        "asset_labels": ASSET_LABELS,
        "owners": Investment.OWNERS,
        "owner_labels": owner_labels,
        "goals": goals,
        "accounts": accounts,
        "sip_days": list(range(1, 29)),
        "currency": current_app.config["CURRENCY_SYMBOL"],
    }


def _form_payload() -> dict:
    data = request.form.to_dict()
    data["is_active"] = "1" if request.form.get("is_active") else "0"
    data["sip_active"] = "1" if request.form.get("sip_active") else "0"
    return data


@investments_bp.route("/")
def index():
    from services import profile_service

    owner = (request.args.get("owner") or "").strip().lower() or None
    if owner and owner not in Investment.OWNERS:
        owner = None

    summary = investment_service.get_portfolio_summary(owner=owner)
    edit_mode = request.args.get("edit") in ("1", "true", "yes")
    holdings = summary["investments"]
    filter_types = [
        {
            "type": row["type"],
            "label": row["label"],
            "count": row["count"],
        }
        for row in (summary.get("allocation") or [])
    ]
    labels = {**profile_service.get_owner_labels(), "joint": "Joint"}

    # Chip counts always reflect the full portfolio (not the filtered view)
    owner_counts = {
        row[0]: row[1]
        for row in (
            db.session.query(Investment.owner, func.count(Investment.id))
            .filter(Investment.is_active.is_(True))
            .group_by(Investment.owner)
            .all()
        )
    }
    all_active_count = sum(owner_counts.values())

    owner_keys = ["self"]
    if profile_service.is_couple_mode():
        owner_keys.append("wife")
    owner_keys.append("joint")
    filter_owners = [
        {
            "owner": key,
            "label": labels.get(key, key.title()),
            "count": owner_counts.get(key, 0),
        }
        for key in owner_keys
    ]

    return render_template(
        "investments/index.html",
        summary=summary,
        holdings=holdings,
        asset_labels=ASSET_LABELS,
        edit_mode=edit_mode,
        filter_types=filter_types,
        filter_owners=filter_owners,
        owner_labels=labels,
        active_owner=owner,
        all_active_count=all_active_count,
        page_title="Investments",
        active_nav="investments",
    )


@investments_bp.route("/save-holdings", methods=["POST"])
def save_holdings():
    """Batch-save inline holdings edits and return to locked view."""
    rows_by_id: dict[int, dict] = {}
    for key, value in request.form.items():
        if "_" not in key:
            continue
        field, _, id_part = key.partition("_")
        if field not in (
            "name",
            "invested",
            "current",
            "sip",
            "goal",
            "sipactive",
            "scheme",
            "units",
        ):
            continue
        try:
            inv_id = int(id_part)
        except ValueError:
            continue
        row = rows_by_id.setdefault(inv_id, {"id": inv_id})
        if field == "name":
            row["name"] = value
        elif field == "invested":
            row["invested_amount"] = value
        elif field == "current":
            row["current_value"] = value
        elif field == "sip":
            row["monthly_sip"] = value
        elif field == "goal":
            row["goal_id"] = value
        elif field == "sipactive":
            row["sip_active"] = value
        elif field == "scheme":
            row["scheme_code"] = value
        elif field == "units":
            row["units"] = value

    # Unchecked sip_active checkboxes are absent — default paused off only when SIP > 0
    for inv_id, row in rows_by_id.items():
        if "sip_active" not in row:
            row["sip_active"] = "0"

    try:
        touched = investment_service.bulk_update_holdings(list(rows_by_id.values()))
        if touched:
            flash(f"Holdings saved & locked — updated {touched}.", "success")
        else:
            flash("Holdings locked. No changes were needed.", "success")
    except InvestmentValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("investments.index", edit=1))

    return redirect(url_for("investments.index"))


@investments_bp.route("/refresh-navs", methods=["POST"])
def refresh_navs():
    from services import foreign_stock_service, gold_service

    result = nav_service.refresh_all_nav_holdings()
    gold = gold_service.refresh_all_gold_holdings()
    foreign = foreign_stock_service.refresh_all_foreign_stock_holdings()

    updated = (
        result["updated_count"] + gold["updated_count"] + foreign["updated_count"]
    )
    errors = (
        list(result.get("errors") or [])
        + list(gold.get("errors") or [])
        + list(foreign.get("errors") or [])
    )
    skipped = (
        list(result.get("skipped") or [])
        + list(gold.get("skipped") or [])
        + list(foreign.get("skipped") or [])
    )
    eligible = (
        result.get("eligible_count", 0)
        + gold.get("eligible_count", 0)
        + foreign.get("eligible_count", 0)
    )

    if updated:
        parts = []
        if result["updated_count"]:
            parts.append(f"{result['updated_count']} fund")
        if gold["updated_count"]:
            rate = gold.get("price_per_gram_22k")
            rate_bit = f" @ ₹{rate:,.0f}/g 22K" if rate is not None else ""
            parts.append(f"{gold['updated_count']} gold{rate_bit}")
        if foreign["updated_count"]:
            fx = foreign.get("usd_inr")
            fx_bit = f" · USD/INR {fx:.2f}" if fx is not None else ""
            parts.append(f"{foreign['updated_count']} foreign stock{fx_bit}")
        flash(
            f"Updated market value for {' + '.join(parts)}.",
            "success",
        )
    elif eligible == 0:
        flash(
            "Nothing to refresh yet. Add mutual-fund scheme+units, gold grams, or foreign ticker+shares.",
            "info",
        )
    elif errors:
        flash("Value refresh had errors — see details.", "danger")
    else:
        flash("Prices saved where possible. Add units/grams/shares for exact market value.", "info")

    for err in errors[:5]:
        flash(err, "danger")
    for msg in skipped[:3]:
        flash(msg, "secondary")

    owner = (request.form.get("owner") or "").strip().lower() or None
    if owner and owner not in Investment.OWNERS:
        owner = None
    return redirect(url_for("investments.index", owner=owner))


@investments_bp.route("/search-schemes")
def search_schemes():
    q = (request.args.get("q") or "").strip()
    try:
        results = nav_service.search_schemes(q)
        return jsonify(results)
    except NavServiceError as exc:
        return jsonify({"error": str(exc)}), 502


@investments_bp.route("/post-sips", methods=["POST"])
def post_sips():
    return _post_contributions(kind="sip", label="SIP")


@investments_bp.route("/post-epf", methods=["POST"])
def post_epf():
    return _post_contributions(kind="epf", label="EPF")


def _post_contributions(*, kind: str, label: str):
    result = investment_service.post_month_sips(kind=kind)
    created = result["created_count"]
    if created:
        flash(
            f"Posted {created} {label} contribution"
            f"{'s' if created != 1 else ''} for {result['label']}.",
            "success",
        )
    elif result["errors"]:
        flash(f"Could not post {label} — see details below.", "danger")
    else:
        flash(f"No {label} to post for {result['label']}.", "info")

    for err in result["errors"]:
        flash(err, "danger")
    if not created and not result["errors"] and result["skipped"]:
        for msg in result["skipped"][:5]:
            flash(msg, "secondary")

    return redirect(request.referrer or url_for("investments.index"))


@investments_bp.route("/new", methods=["GET", "POST"])
def create():
    ctx = _form_context()
    if request.method == "POST":
        try:
            inv = investment_service.create_investment(_form_payload())
            flash(f"Added investment: {inv.name}", "success")
            return redirect(url_for("investments.index", edit=1))
        except InvestmentValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "investments/form.html",
                investment=None,
                form_data=request.form,
                page_title="Add Investment",
                active_nav="investments",
                **ctx,
            )
    return render_template(
        "investments/form.html",
        investment=None,
        form_data={
            "asset_type": "mutual_fund",
            "owner": "joint",
            "is_active": "1",
            "sip_active": "1",
            "sip_day": "1",
        },
        page_title="Add Investment",
        active_nav="investments",
        **ctx,
    )


@investments_bp.route("/<int:inv_id>/edit", methods=["GET", "POST"])
def edit(inv_id: int):
    inv = investment_service.get_investment(inv_id)
    if not inv:
        flash("Investment not found.", "danger")
        return redirect(url_for("investments.index"))

    ctx = _form_context()
    if request.method == "POST":
        try:
            investment_service.update_investment(inv, _form_payload())
            flash(f"Updated {inv.name}.", "success")
            return redirect(url_for("investments.index", edit=1))
        except InvestmentValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "investments/form.html",
                investment=inv,
                form_data=request.form,
                page_title="Edit Investment",
                active_nav="investments",
                **ctx,
            )

    def _amt(value) -> str:
        if value is None or value == "":
            return ""
        return f"{float(value):.2f}"

    form_data = {
        "name": inv.name,
        "asset_type": inv.asset_type,
        "invested_amount": _amt(inv.invested_amount),
        "current_value": _amt(inv.current_value),
        "monthly_sip": _amt(inv.monthly_sip) if inv.monthly_sip else "",
        "sip_day": inv.sip_day or "",
        "source_account_id": inv.source_account_id or "",
        "sip_active": "1" if inv.sip_active else "0",
        "scheme_code": inv.scheme_code or "",
        "units": f"{float(inv.units):.4f}" if inv.units is not None else "",
        "owner": inv.owner,
        "goal_id": inv.goal_id or "",
        "start_date": inv.start_date.isoformat() if inv.start_date else "",
        "notes": inv.notes or "",
        "is_active": "1" if inv.is_active else "0",
    }
    return render_template(
        "investments/form.html",
        investment=inv,
        form_data=form_data,
        page_title="Edit Investment",
        active_nav="investments",
        **ctx,
    )


@investments_bp.route("/<int:inv_id>/delete", methods=["POST"])
def delete(inv_id: int):
    inv = investment_service.get_investment(inv_id)
    if not inv:
        flash("Investment not found.", "danger")
    else:
        name = inv.name
        try:
            unlinked = investment_service.delete_investment(inv)
            if unlinked:
                flash(
                    f"Deleted {name}. Unlinked {unlinked} past SIP/investment "
                    f"transaction{'s' if unlinked != 1 else ''} (cash history kept).",
                    "success",
                )
            else:
                flash(f"Deleted {name}.", "success")
        except InvestmentValidationError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("investments.index", edit=1))
