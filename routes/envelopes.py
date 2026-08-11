"""Envelope routes — virtual purpose pots."""

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

from services import envelope_service
from services.envelope_service import EnvelopeValidationError

envelopes_bp = Blueprint("envelopes", __name__, url_prefix="/envelopes")


@envelopes_bp.route("/")
def index():
    overview = envelope_service.get_envelopes_overview()
    purpose = envelope_service.investments_by_purpose()
    envelopes = envelope_service.list_envelopes(active_only=True)
    move_unlocked = request.args.get("move") in ("1", "true", "yes")
    return render_template(
        "envelopes/index.html",
        overview=overview,
        investment_purpose=purpose,
        envelopes=envelopes,
        move_unlocked=move_unlocked,
        page_title="Envelopes",
        active_nav="envelopes",
    )


@envelopes_bp.route("/reallocate", methods=["POST"])
def reallocate():
    try:
        from_id = int(request.form.get("from_envelope_id"))
        to_id = int(request.form.get("to_envelope_id"))
    except (TypeError, ValueError):
        flash("Choose source and destination envelopes.", "danger")
        return redirect(url_for("envelopes.index", move=1))

    try:
        result = envelope_service.reallocate(
            from_envelope_id=from_id,
            to_envelope_id=to_id,
            amount=request.form.get("amount"),
            notes=request.form.get("notes"),
        )
        flash(
            f"Moved {current_app.config['CURRENCY_SYMBOL']}"
            f"{float(result['amount']):,.2f} "
            f"{result['from'].name} → {result['to'].name} "
            "(labels only — Joint bank balance unchanged).",
            "success",
        )
        return redirect(url_for("envelopes.index"))
    except EnvelopeValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("envelopes.index", move=1))


@envelopes_bp.route("/<int:envelope_id>")
def detail(envelope_id: int):
    today = date.today()
    year = request.args.get("year", type=int) or today.year
    month = request.args.get("month", type=int) or today.month
    ledger = envelope_service.get_envelope_ledger(
        envelope_id, year=year, month=month
    )
    if not ledger["envelope"]:
        abort(404)

    # Prev / next month links
    if month == 1:
        prev_y, prev_m = year - 1, 12
    else:
        prev_y, prev_m = year, month - 1
    if month == 12:
        next_y, next_m = year + 1, 1
    else:
        next_y, next_m = year, month + 1

    return render_template(
        "envelopes/detail.html",
        ledger=ledger,
        envelope=ledger["envelope"],
        rows=ledger["rows"],
        meta=ledger["meta"],
        prev_year=prev_y,
        prev_month=prev_m,
        next_year=next_y,
        next_month=next_m,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title=ledger["envelope"].name,
        active_nav="envelopes",
    )
