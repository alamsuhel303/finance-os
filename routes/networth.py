"""Net worth routes — overview, snapshots, liabilities."""

from __future__ import annotations

from datetime import date, datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from models import Liability
from services import net_worth_service
from services.net_worth_service import NetWorthValidationError

networth_bp = Blueprint("networth", __name__, url_prefix="/networth")


@networth_bp.route("/")
def index():
    live = net_worth_service.compute_live_net_worth()
    growth = net_worth_service.get_growth_stats(live["net_worth"])
    currency = current_app.config["CURRENCY_SYMBOL"]

    return render_template(
        "networth/index.html",
        live=live,
        growth=growth,
        currency=currency,
        page_title="Net Worth",
        active_nav="networth",
    )


@networth_bp.route("/snapshot", methods=["POST"])
def snapshot():
    raw = (request.form.get("snapshot_date") or "").strip()
    snap_date = None
    if raw:
        try:
            snap_date = datetime.strptime(raw, "%Y-%m-%d").date().replace(day=1)
        except ValueError:
            flash("Invalid snapshot date.", "danger")
            return redirect(url_for("networth.index"))

    notes = (request.form.get("notes") or "").strip() or None
    snap = net_worth_service.record_snapshot(snap_date, notes=notes)
    flash(
        f"Snapshot saved for {snap.snapshot_date.strftime('%B %Y')}: "
        f"{current_app.config['CURRENCY_SYMBOL']}{snap.net_worth}",
        "success",
    )
    return redirect(request.referrer or url_for("networth.index"))


@networth_bp.route("/liabilities/new", methods=["GET", "POST"])
def liability_create():
    if request.method == "POST":
        try:
            item = net_worth_service.create_liability(request.form.to_dict())
            flash(f"Added liability: {item.name}", "success")
            return redirect(url_for("networth.index"))
        except NetWorthValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {"liability_type": "home_loan", "owner": "joint", "is_active": "1"}

    return render_template(
        "networth/liability_form.html",
        liability=None,
        form_data=form_data,
        liability_types=Liability.LIABILITY_TYPES,
        owners=Liability.OWNERS,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Add Liability",
        active_nav="networth",
    )


@networth_bp.route("/liabilities/<int:lid>/edit", methods=["GET", "POST"])
def liability_edit(lid: int):
    item = net_worth_service.get_liability(lid)
    if not item:
        flash("Liability not found.", "danger")
        return redirect(url_for("networth.index"))

    if request.method == "POST":
        try:
            net_worth_service.update_liability(item, request.form.to_dict())
            flash(f"Updated {item.name}.", "success")
            return redirect(url_for("networth.index"))
        except NetWorthValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "name": item.name,
            "liability_type": item.liability_type,
            "outstanding_amount": item.outstanding_amount,
            "interest_rate": item.interest_rate or "",
            "owner": item.owner,
            "notes": item.notes or "",
            "is_active": "1" if item.is_active else "0",
        }

    return render_template(
        "networth/liability_form.html",
        liability=item,
        form_data=form_data,
        liability_types=Liability.LIABILITY_TYPES,
        owners=Liability.OWNERS,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Edit Liability",
        active_nav="networth",
    )


@networth_bp.route("/liabilities/<int:lid>/delete", methods=["POST"])
def liability_delete(lid: int):
    item = net_worth_service.get_liability(lid)
    if not item:
        flash("Liability not found.", "danger")
    else:
        name = item.name
        net_worth_service.delete_liability(item)
        flash(f"Deleted {name}.", "success")
    return redirect(url_for("networth.index"))
