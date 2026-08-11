"""Transaction routes — list, create, edit, delete."""

from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from models import Account, Category, Envelope, Investment, Transaction
from services import import_service, transaction_service
from services.import_service import ImportValidationError
from services.transaction_service import TransactionValidationError

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")

LIST_FILTER_KEYS = ("q", "type", "category", "account", "paid_by", "from", "to")


def _list_filter_kwargs() -> dict:
    """Preserve list filters across create/edit/delete redirects."""
    out: dict = {}
    for key in LIST_FILTER_KEYS:
        val = None
        if request.method == "POST":
            val = request.form.get(f"filter_{key}")
        if val is None or val == "":
            val = request.args.get(key)
        if val in (None, ""):
            continue
        # List uses from/to as YYYY-MM-DD; ignore account-id style shortcuts
        if key in ("from", "to") and not _looks_like_date(str(val)):
            continue
        out[key] = val
    return out


def _looks_like_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _redirect_to_list():
    return redirect(url_for("transactions.list_transactions", **_list_filter_kwargs()))


def _form_context():
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    categories = (
        Category.query.filter_by(is_active=True, parent_id=None)
        .order_by(Category.sort_order, Category.name)
        .all()
    )
    envelopes = (
        Envelope.query.filter_by(is_active=True)
        .order_by(Envelope.sort_order, Envelope.name)
        .all()
    )
    investments = (
        Investment.query.filter_by(is_active=True)
        .order_by(Investment.sort_order, Investment.name)
        .all()
    )
    from services import envelope_service, profile_service

    spend_account = envelope_service.resolve_envelope_cash_account()
    my_account = next(
        (
            a
            for a in accounts
            if a.owner == "self" and a.account_type in ("bank", "salary")
        ),
        None,
    )
    essentials = next((e for e in envelopes if e.slug == "essentials"), None)
    return {
        "accounts": accounts,
        "categories": categories,
        "envelopes": envelopes,
        "investments": investments,
        "transaction_types": Transaction.TRANSACTION_TYPES,
        "payment_modes": Transaction.PAYMENT_MODES,
        "paid_by_choices": Transaction.PAID_BY_CHOICES,
        "need_want_choices": Transaction.NEED_WANT_CHOICES,
        "default_joint_id": spend_account.id if spend_account else "",
        "default_from_id": my_account.id if my_account else "",
        "default_essentials_id": essentials.id if essentials else "",
        "is_couple_mode": profile_service.is_couple_mode(),
    }


@transactions_bp.route("/")
def list_transactions():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip() or None
    txn_type = request.args.get("type", "").strip() or None
    category_id = request.args.get("category", type=int)
    account_id = request.args.get("account", type=int)
    paid_by = request.args.get("paid_by", "").strip() or None

    date_from = _parse_optional_date(request.args.get("from"))
    date_to = _parse_optional_date(request.args.get("to"))

    pagination = transaction_service.list_transactions(
        page=page,
        per_page=current_app.config["ITEMS_PER_PAGE"],
        search=search,
        transaction_type=txn_type,
        category_id=category_id,
        account_id=account_id,
        paid_by=paid_by,
        date_from=date_from,
        date_to=date_to,
    )

    ctx = _form_context()
    filter_account = None
    running_balances = None
    if account_id:
        filter_account = next(
            (a for a in ctx["accounts"] if a.id == account_id), None
        )
        if not filter_account:
            from extensions import db

            filter_account = db.session.get(Account, account_id)
        running_balances = transaction_service.build_account_running_balances(
            account_id
        )

    return render_template(
        "transactions/list.html",
        pagination=pagination,
        transactions=pagination.items,
        filter_account=filter_account,
        running_balances=running_balances,
        filters={
            "q": search or "",
            "type": txn_type or "",
            "category": category_id or "",
            "account": account_id or "",
            "paid_by": paid_by or "",
            "from": request.args.get("from", ""),
            "to": request.args.get("to", ""),
        },
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Transactions",
        active_nav="transactions",
        **ctx,
    )


@transactions_bp.route("/new", methods=["GET", "POST"])
def create():
    ctx = _form_context()
    list_filters = _list_filter_kwargs()
    if request.method == "POST":
        try:
            # Pass MultiDict so transfer split lists are preserved
            txn, warning = transaction_service.create_transaction(request.form)
            flash(f"Transaction “{txn.description}” added.", "success")
            if warning:
                flash(warning, "warning")
            return _redirect_to_list()
        except TransactionValidationError as exc:
            flash(str(exc), "danger")

    if request.method == "POST":
        form_data = request.form
    else:
        # Prefill from query (account ledger filter, or transfer shortcuts)
        account_prefill = request.args.get("account")
        from_arg = request.args.get("from")
        to_arg = request.args.get("to")
        if not account_prefill and from_arg and str(from_arg).isdigit():
            account_prefill = from_arg
        to_prefill = to_arg if to_arg and str(to_arg).isdigit() else None
        type_arg = request.args.get("type")
        type_prefill = (
            type_arg if type_arg in Transaction.TRANSACTION_TYPES else None
        )
        cat_arg = request.args.get("category")
        form_data = {
            k: v
            for k, v in {
                "transaction_type": type_prefill,
                "account_id": account_prefill,
                "to_account_id": to_prefill,
                "category_id": cat_arg if cat_arg and str(cat_arg).isdigit() else None,
                "amount": request.args.get("amount"),
                "description": request.args.get("description"),
                "investment_id": request.args.get("investment_id"),
            }.items()
            if v
        }

    return render_template(
        "transactions/form.html",
        transaction=None,
        form_data=form_data,
        list_filters=list_filters,
        split_rows=_split_rows_from_form(request.form) if request.method == "POST" else [],
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Add Transaction",
        active_nav="transactions",
        **ctx,
    )


@transactions_bp.route("/<int:txn_id>/edit", methods=["GET", "POST"])
def edit(txn_id: int):
    txn = transaction_service.get_transaction(txn_id)
    list_filters = _list_filter_kwargs()
    if not txn:
        flash("Transaction not found.", "danger")
        return _redirect_to_list()

    ctx = _form_context()
    if request.method == "POST":
        try:
            _, warning = transaction_service.update_transaction(txn, request.form)
            flash("Transaction updated.", "success")
            if warning:
                flash(warning, "warning")
            return _redirect_to_list()
        except TransactionValidationError as exc:
            flash(str(exc), "danger")

    if request.method == "POST":
        form_data = request.form
        split_rows = _split_rows_from_form(request.form)
    else:
        form_data = {
            "date": txn.date.isoformat(),
            "amount": str(txn.amount),
            "description": txn.description,
            "transaction_type": txn.transaction_type,
            "category_id": txn.category_id or "",
            "subcategory_id": txn.subcategory_id or "",
            "account_id": txn.account_id,
            "to_account_id": txn.to_account_id or "",
            "paid_by": txn.paid_by,
            "payment_mode": txn.payment_mode,
            "need_want": txn.need_want,
            "notes": txn.notes or "",
            "envelope_id": txn.envelope_id or "",
            "investment_id": txn.investment_id or "",
        }
        split_rows = [
            {"envelope_id": e.envelope_id, "amount": str(e.amount)}
            for e in txn.envelope_entries.filter_by(entry_type="allocation").all()
        ]

    return render_template(
        "transactions/form.html",
        transaction=txn,
        form_data=form_data,
        list_filters=list_filters,
        split_rows=split_rows,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Edit Transaction",
        active_nav="transactions",
        **ctx,
    )


@transactions_bp.route("/<int:txn_id>/delete", methods=["POST"])
def delete(txn_id: int):
    txn = transaction_service.get_transaction(txn_id)
    if not txn:
        flash("Transaction not found.", "danger")
        return _redirect_to_list()

    description = txn.description
    transaction_service.delete_transaction(txn)
    flash(f"Transaction “{description}” deleted.", "success")
    return _redirect_to_list()


@transactions_bp.route("/import")
def import_page():
    return render_template(
        "transactions/import.html",
        page_title="Import Transactions",
        active_nav="transactions",
        result=None,
        preview=None,
    )


@transactions_bp.route("/import/template.xlsx")
def import_template():
    data = import_service.build_template_xlsx()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=finance_os_transactions_template.xlsx"
        },
    )


@transactions_bp.route("/import", methods=["POST"])
def import_upload():
    action = (request.form.get("action") or "preview").strip().lower()
    skip_duplicates = request.form.get("skip_duplicates", "1") in ("1", "true", "on", "yes")

    if action == "confirm":
        token = (request.form.get("staging_token") or "").strip()
        try:
            result = import_service.import_xlsx(
                staging_token=token,
                skip_duplicates=skip_duplicates,
            )
        except ImportValidationError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("transactions.import_page"))

        if result["created_count"]:
            flash(
                f"Imported {result['created_count']} transaction"
                f"{'s' if result['created_count'] != 1 else ''}.",
                "success",
            )
        if result["duplicate_count"]:
            flash(
                f"Skipped {result['duplicate_count']} duplicate"
                f"{'s' if result['duplicate_count'] != 1 else ''} "
                "(same date, amount, description).",
                "warning",
            )
        if result["skipped_count"] and not result["duplicate_count"]:
            flash(
                f"Skipped {result['skipped_count']} row"
                f"{'s' if result['skipped_count'] != 1 else ''} — see details below.",
                "warning",
            )
        elif result["skipped_count"] > result["duplicate_count"]:
            flash("Some rows had errors — see details below.", "warning")
        if not result["created_count"] and not result["skipped_count"]:
            flash("No data rows found in the spreadsheet.", "info")

        return render_template(
            "transactions/import.html",
            result=result,
            preview=None,
            page_title="Import Transactions",
            active_nav="transactions",
        )

    # Preview (dry-run)
    upload = request.files.get("file")
    if not upload or not upload.filename:
        flash("Choose an .xlsx file to upload.", "danger")
        return redirect(url_for("transactions.import_page"))

    try:
        preview = import_service.preview_xlsx(upload.stream, filename=upload.filename)
    except ImportValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("transactions.import_page"))

    if not preview["preview_rows"]:
        flash("No data rows found in the spreadsheet.", "info")
        if preview.get("staging_token"):
            import_service.clear_staged(preview["staging_token"])
        return redirect(url_for("transactions.import_page"))

    flash(
        f"Preview ready: {preview['ready_count']} OK"
        f" · {preview['duplicate_count']} duplicate"
        f" · {preview['skipped_count']} error/skip."
        " Confirm to import.",
        "info",
    )
    return render_template(
        "transactions/import.html",
        result=None,
        preview=preview,
        page_title="Import Transactions",
        active_nav="transactions",
    )


def _parse_optional_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _split_rows_from_form(form) -> list[dict]:
    if not hasattr(form, "getlist"):
        return []
    env_ids = form.getlist("split_envelope_id")
    amounts = form.getlist("split_amount")
    rows = []
    for eid, amt in zip(env_ids, amounts):
        if str(eid or "").strip() or str(amt or "").strip():
            rows.append({"envelope_id": eid, "amount": amt})
    return rows
