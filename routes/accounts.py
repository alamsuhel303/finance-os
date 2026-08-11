"""Account routes — list, create, edit accounts."""

from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from services import account_service, emergency_service, envelope_service
from services.account_service import (
    ACCOUNT_TYPE_LABELS,
    ACCOUNT_TYPES,
    OWNERS,
    AccountValidationError,
)
from services.emergency_service import EmergencyValidationError

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def _form_context():
    return {
        "account_types": ACCOUNT_TYPES,
        "account_type_labels": ACCOUNT_TYPE_LABELS,
        "owners": OWNERS,
        "currency": current_app.config["CURRENCY_SYMBOL"],
    }


@accounts_bp.route("/")
def index():
    accounts = account_service.list_accounts(active_only=False)
    total = sum((float(a.current_balance or 0) for a in accounts if a.is_active), 0.0)
    envelopes = envelope_service.get_envelopes_overview()
    emergency = emergency_service.get_breakdown()
    spending_types = {"bank", "cash", "joint", "salary"}
    fund_types = {"goal", "emergency", "investment"}
    spending_accounts = [a for a in accounts if a.account_type in spending_types]
    fund_accounts = [a for a in accounts if a.account_type in fund_types]
    other_accounts = [
        a for a in accounts if a.account_type not in spending_types | fund_types
    ]
    edit_emergency = request.args.get("edit_emergency") in ("1", "true", "yes")
    return render_template(
        "accounts/index.html",
        accounts=accounts,
        spending_accounts=spending_accounts,
        fund_accounts=fund_accounts,
        other_accounts=other_accounts,
        emergency=emergency,
        edit_emergency=edit_emergency,
        total=total,
        envelopes=envelopes,
        account_type_labels=ACCOUNT_TYPE_LABELS,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Accounts",
        active_nav="accounts",
    )


@accounts_bp.route("/emergency-tags", methods=["POST"])
def save_emergency_tags():
    """Save virtual emergency tags on spending accounts (no cash movement)."""
    ids = request.form.getlist("account_id")
    amounts = request.form.getlist("emergency_tagged")
    tags: dict[int, str] = {}
    for account_id, amount in zip(ids, amounts):
        if not str(account_id or "").strip():
            continue
        tags[int(account_id)] = amount
    try:
        updated = emergency_service.save_account_tags(tags)
        if updated:
            flash("Emergency tags saved — no money moved.", "success")
        else:
            flash("No emergency tag changes.", "info")
    except EmergencyValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("accounts.index"))


@accounts_bp.route("/statement-wizard", methods=["GET", "POST"])
def statement_wizard():
    """Set books to match bank/statement balances as of a chosen date."""
    accounts = account_service.list_accounts(active_only=True)
    # Prefer real cash accounts in the form
    cash_types = {"bank", "cash", "joint", "salary"}
    focus = [a for a in accounts if a.account_type in cash_types]
    if not focus:
        focus = accounts

    if request.method == "POST":
        raw_date = (request.form.get("statement_date") or "").strip()
        try:
            statement_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            flash("Enter a valid statement date (YYYY-MM-DD).", "danger")
            return render_template(
                "accounts/statement_wizard.html",
                accounts=focus,
                form_data=request.form,
                currency=current_app.config["CURRENCY_SYMBOL"],
                page_title="Statement balances",
                active_nav="accounts",
            )
        try:
            balances = {}
            ids = request.form.getlist("account_id")
            amounts = request.form.getlist("statement_balance")
            for account_id, amount in zip(ids, amounts):
                parsed = account_service.parse_statement_amount(amount)
                if parsed is None:
                    continue
                balances[int(account_id)] = parsed
            if not balances:
                raise AccountValidationError(
                    "Enter at least one statement balance (leave others blank to skip)."
                )
            changes = account_service.apply_statement_balances(statement_date, balances)
            if changes:
                flash(
                    f"Updated {len(changes)} account"
                    f"{'s' if len(changes) != 1 else ''} to match statement "
                    f"as of {statement_date.isoformat()}.",
                    "success",
                )
            else:
                flash("Balances already matched — nothing changed.", "info")
            return redirect(url_for("accounts.index"))
        except AccountValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "accounts/statement_wizard.html",
                accounts=focus,
                form_data=request.form,
                currency=current_app.config["CURRENCY_SYMBOL"],
                page_title="Statement balances",
                active_nav="accounts",
            )

    return render_template(
        "accounts/statement_wizard.html",
        accounts=focus,
        form_data={"statement_date": datetime.now().date().isoformat()},
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Statement balances",
        active_nav="accounts",
    )


@accounts_bp.route("/new", methods=["GET", "POST"])
def create():
    ctx = _form_context()
    if request.method == "POST":
        try:
            account = account_service.create_account(request.form.to_dict())
            flash(f"Created account: {account.name}", "success")
            return redirect(url_for("accounts.index"))
        except AccountValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "accounts/form.html",
                account=None,
                form_data=request.form,
                page_title="Add Account",
                active_nav="accounts",
                **ctx,
            )
    return render_template(
        "accounts/form.html",
        account=None,
        form_data={
            "account_type": "bank",
            "owner": "self",
            "opening_balance": "0",
            "is_active": "1",
            "sort_order": "10",
        },
        page_title="Add Account",
        active_nav="accounts",
        **ctx,
    )


@accounts_bp.route("/<int:account_id>/edit", methods=["GET", "POST"])
def edit(account_id: int):
    account = account_service.get_account(account_id)
    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.index"))

    ctx = _form_context()
    if request.method == "POST":
        try:
            account_service.update_account(account, request.form.to_dict())
            flash(f"Updated {account.name}.", "success")
            return redirect(url_for("accounts.index"))
        except AccountValidationError as exc:
            flash(str(exc), "danger")
            return render_template(
                "accounts/form.html",
                account=account,
                form_data=request.form,
                page_title="Edit Account",
                active_nav="accounts",
                **ctx,
            )

    form_data = {
        "name": account.name,
        "account_type": "bank" if account.account_type == "salary" else account.account_type,
        "owner": account.owner,
        "opening_balance": account.opening_balance,
        "sort_order": account.sort_order,
        "notes": account.notes or "",
        "is_active": "1" if account.is_active else "0",
    }
    return render_template(
        "accounts/form.html",
        account=account,
        form_data=form_data,
        page_title="Edit Account",
        active_nav="accounts",
        **ctx,
    )


@accounts_bp.route("/<int:account_id>/delete", methods=["POST"])
def delete(account_id: int):
    account = account_service.get_account(account_id)
    if not account:
        flash("Account not found.", "danger")
        return redirect(url_for("accounts.index"))
    try:
        name = account.name
        account_service.delete_account(account)
        flash(f"Deleted {name}.", "success")
    except AccountValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("accounts.index"))
