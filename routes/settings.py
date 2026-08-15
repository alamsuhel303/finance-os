"""Settings routes — backup, restore, exports, categories, recurring income."""

from __future__ import annotations

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

from models import Account, RecurringIncome
from services import (
    account_service,
    backup_service,
    category_service,
    envelope_service,
    joint_funding_service,
    profile_service,
    recurring_income_service,
    telegram_service,
)
from services.backup_service import BackupError
from services.category_service import (
    CATEGORY_TYPE_LABELS,
    CATEGORY_TYPES,
    DEFAULT_COLORS,
    ICON_CHOICES,
    CategoryValidationError,
)
from services.joint_funding_service import JointFundingValidationError
from services.recurring_income_service import RecurringIncomeValidationError
from services.telegram_service import TelegramServiceError

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


def _category_form_context():
    return {
        "category_types": CATEGORY_TYPES,
        "category_type_labels": CATEGORY_TYPE_LABELS,
        "icon_choices": ICON_CHOICES,
        "default_colors": DEFAULT_COLORS,
        "envelopes": envelope_service.list_envelopes(active_only=True),
    }


def _spending_accounts():
    return (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )


@settings_bp.route("/")
def index():
    try:
        stats = backup_service.get_db_stats()
        backups = backup_service.list_backups()
    except BackupError as exc:
        flash(str(exc), "danger")
        stats = {}
        backups = []

    income_status = recurring_income_service.get_month_status()
    templates = recurring_income_service.list_templates(active_only=False)
    joint_status = joint_funding_service.get_month_status()
    joint_plan = joint_funding_service.get_plan()
    telegram_users = telegram_service.list_linked_users()
    telegram_accounts = telegram_service.spending_accounts()
    labels = profile_service.get_owner_labels()

    return render_template(
        "settings/index.html",
        stats=stats,
        backups=backups,
        income_templates=templates,
        income_status=income_status,
        joint_status=joint_status,
        joint_plan=joint_plan,
        funding_ui=joint_funding_service.funding_ui(),
        telegram_users=telegram_users,
        telegram_accounts=telegram_accounts,
        telegram_enabled=current_app.config.get("TELEGRAM_ENABLED"),
        telegram_token_set=bool(current_app.config.get("TELEGRAM_BOT_TOKEN")),
        owner_labels=labels,
        is_couple=profile_service.is_couple_mode(),
        config_backup_days=current_app.config.get("BACKUP_MAX_AGE_DAYS", 7),
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Settings",
        active_nav="settings",
    )


@settings_bp.route("/backup", methods=["POST"])
def backup():
    label = (request.form.get("label") or "").strip() or None
    try:
        path = backup_service.create_backup(label=label)
        flash(
            f"Backup created: {path.name}. "
            f"Also copy {path.parent} (or the live DB) off this Mac.",
            "success",
        )
    except BackupError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("settings.index"))


@settings_bp.route("/rebuild-balances", methods=["POST"])
def rebuild_balances():
    changes = account_service.rebuild_all_balances()
    if not changes:
        flash("Balances already match the ledger — nothing to fix.", "info")
    else:
        names = ", ".join(c["account"] for c in changes[:5])
        extra = f" (+{len(changes) - 5} more)" if len(changes) > 5 else ""
        flash(
            f"Recalculated {len(changes)} account balance"
            f"{'s' if len(changes) != 1 else ''}: {names}{extra}.",
            "success",
        )
    return redirect(url_for("settings.index"))


@settings_bp.route("/restore", methods=["POST"])
def restore():
    filename = (request.form.get("filename") or "").strip()
    confirm = (request.form.get("confirm") or "").strip().upper()
    if confirm != "RESTORE":
        flash("Type RESTORE to confirm restoring a backup.", "danger")
        return redirect(url_for("settings.index"))
    try:
        backup_service.restore_backup(filename)
        flash(
            f"Restored {filename}. Restart the app if balances look stale.",
            "success",
        )
    except BackupError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("settings.index"))


@settings_bp.route("/export/transactions.csv")
def export_transactions():
    csv_text = backup_service.export_transactions_csv()
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=finance_os_transactions.csv"
        },
    )


@settings_bp.route("/export/accounts.csv")
def export_accounts():
    csv_text = backup_service.export_accounts_csv()
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=finance_os_accounts.csv"
        },
    )


@settings_bp.route("/export/networth.csv")
def export_networth():
    csv_text = backup_service.export_net_worth_csv()
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=finance_os_networth.csv"
        },
    )


@settings_bp.route("/export/investments.csv")
def export_investments():
    csv_text = backup_service.export_investments_csv()
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=finance_os_investments.csv"
        },
    )


@settings_bp.route("/recurring-income/post", methods=["POST"])
def post_recurring_income():
    result = recurring_income_service.post_month_income()
    if result["created_count"]:
        flash(
            f"Posted {result['created_count']} income"
            f"{'s' if result['created_count'] != 1 else ''} for {result['label']} "
            f"({current_app.config['CURRENCY_SYMBOL']}{result['total']}).",
            "success",
        )
    else:
        flash("Nothing to post — already logged or no templates ready.", "info")
    return redirect(request.referrer or url_for("dashboard.index"))


@settings_bp.route("/recurring-income/new", methods=["GET", "POST"])
def recurring_income_create():
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    if request.method == "POST":
        try:
            item = recurring_income_service.create_template(request.form.to_dict())
            flash(f"Added recurring income: {item.name}", "success")
            return redirect(url_for("settings.index"))
        except RecurringIncomeValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "day_of_month": "1",
            "owner": "self",
            "is_active": "1",
            "account_id": accounts[0].id if accounts else "",
        }

    return render_template(
        "settings/recurring_income_form.html",
        template=None,
        form_data=form_data,
        accounts=accounts,
        owners=RecurringIncome.OWNERS,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Add Recurring Income",
        active_nav="settings",
    )


@settings_bp.route("/recurring-income/<int:template_id>/edit", methods=["GET", "POST"])
def recurring_income_edit(template_id: int):
    item = recurring_income_service.get_template(template_id)
    if not item:
        flash("Template not found.", "danger")
        return redirect(url_for("settings.index"))

    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    if request.method == "POST":
        try:
            recurring_income_service.update_template(item, request.form.to_dict())
            flash(f"Updated {item.name}.", "success")
            return redirect(url_for("settings.index"))
        except RecurringIncomeValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "name": item.name,
            "amount": item.amount,
            "account_id": item.account_id,
            "day_of_month": item.day_of_month,
            "owner": item.owner,
            "notes": item.notes or "",
            "sort_order": item.sort_order,
            "is_active": "1" if item.is_active else "0",
        }

    return render_template(
        "settings/recurring_income_form.html",
        template=item,
        form_data=form_data,
        accounts=accounts,
        owners=RecurringIncome.OWNERS,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Edit Recurring Income",
        active_nav="settings",
    )


@settings_bp.route("/recurring-income/<int:template_id>/delete", methods=["POST"])
def recurring_income_delete(template_id: int):
    item = recurring_income_service.get_template(template_id)
    if not item:
        flash("Template not found.", "danger")
    else:
        name = item.name
        recurring_income_service.delete_template(item)
        flash(f"Deleted {name}.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.route("/joint-funding", methods=["GET", "POST"])
def joint_funding_edit():
    accounts = _spending_accounts()
    envelopes = [
        e
        for e in envelope_service.list_envelopes(active_only=True)
        if e.slug != "unallocated"
    ]
    joint = joint_funding_service.resolve_joint_account()
    funding_ui = joint_funding_service.funding_ui()

    if request.method == "POST":
        try:
            joint_funding_service.save_plan(request.form)
            flash(f"{funding_ui['section_title']} plan saved.", "success")
            return redirect(url_for("settings.index"))
        except JointFundingValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
            split_rows = _joint_split_rows_from_form(request.form)
    else:
        plan = joint_funding_service.get_or_init_plan()
        form_data = {
            "self_account_id": plan.self_account_id or "",
            "wife_account_id": plan.wife_account_id or "",
            "self_amount": plan.self_amount if plan.id else "",
            "wife_amount": plan.wife_amount if plan.id else "",
            "day_of_month": plan.day_of_month or 1,
            "notes": plan.notes or "",
            "is_active": "1" if (plan.is_active if plan.id else True) else "0",
        }
        split_rows = joint_funding_service.editable_split_rows(plan)

    return render_template(
        "settings/joint_funding_form.html",
        form_data=form_data,
        split_rows=split_rows,
        accounts=accounts,
        envelopes=envelopes,
        joint_account=joint,
        funding_ui=funding_ui,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title=funding_ui["plan_title"],
        active_nav="settings",
    )


@settings_bp.route("/joint-funding/post", methods=["POST"])
def post_joint_funding():
    try:
        result = joint_funding_service.post_month()
        ui = result.get("ui") or joint_funding_service.funding_ui()
        if result["created_count"]:
            flash(
                f"{ui['title']} for {result['label']}: "
                f"{result['created_count']} transfer"
                f"{'s' if result['created_count'] != 1 else ''} "
                f"· {result['total']}.",
                "success",
            )
        else:
            flash(f"Nothing to post for {result['label']} — already done or amounts are zero.", "info")
    except JointFundingValidationError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("dashboard.index"))


def _joint_split_rows_from_form(form) -> list[dict]:
    if not hasattr(form, "getlist"):
        return []
    env_ids = form.getlist("split_envelope_id")
    amounts = form.getlist("split_amount")
    rows = []
    for eid, amt in zip(env_ids, amounts):
        if str(eid or "").strip() or str(amt or "").strip():
            rows.append({"envelope_id": eid, "amount": amt})
    return rows


@settings_bp.route("/categories")
def categories_index():
    categories = category_service.list_categories(active_only=False)
    return render_template(
        "settings/categories.html",
        categories=categories,
        category_type_labels=CATEGORY_TYPE_LABELS,
        page_title="Categories",
        active_nav="settings",
    )


@settings_bp.route("/categories/new", methods=["GET", "POST"])
def category_create():
    ctx = _category_form_context()
    if request.method == "POST":
        try:
            category = category_service.create_category(request.form.to_dict())
            flash(f"Created category: {category.name}", "success")
            return redirect(url_for("settings.categories_index"))
        except CategoryValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "category_type": "expense",
            "color": "#34d399",
            "icon": "bi-tag",
            "sort_order": "10",
            "is_active": "1",
            "envelope_id": "",
        }

    return render_template(
        "settings/category_form.html",
        category=None,
        form_data=form_data,
        page_title="Add Category",
        active_nav="settings",
        **ctx,
    )


@settings_bp.route("/categories/<int:category_id>/edit", methods=["GET", "POST"])
def category_edit(category_id: int):
    category = category_service.get_category(category_id)
    if not category or category.parent_id is not None:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.categories_index"))

    ctx = _category_form_context()
    if request.method == "POST":
        try:
            category_service.update_category(category, request.form.to_dict())
            flash(f"Updated {category.name}.", "success")
            return redirect(url_for("settings.categories_index"))
        except CategoryValidationError as exc:
            flash(str(exc), "danger")
            form_data = request.form
    else:
        form_data = {
            "name": category.name,
            "category_type": category.category_type,
            "color": category.color or "#6366f1",
            "icon": category.icon or "bi-tag",
            "sort_order": category.sort_order,
            "envelope_id": category.envelope_id or "",
            "is_active": "1" if category.is_active else "0",
        }

    return render_template(
        "settings/category_form.html",
        category=category,
        form_data=form_data,
        page_title="Edit Category",
        active_nav="settings",
        **ctx,
    )


@settings_bp.route("/categories/<int:category_id>/delete", methods=["POST"])
def category_delete(category_id: int):
    category = category_service.get_category(category_id)
    if not category:
        flash("Category not found.", "danger")
        return redirect(url_for("settings.categories_index"))
    try:
        name = category.name
        category_service.delete_category(category)
        flash(f"Deleted {name}.", "success")
    except CategoryValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("settings.categories_index"))


@settings_bp.route("/telegram/link-code", methods=["POST"])
def telegram_link_code():
    owner = (request.form.get("owner") or "self").strip().lower()
    try:
        row = telegram_service.generate_link_code(owner)
        labels = profile_service.get_owner_labels()
        who = labels.get(owner, owner)
        flash(
            f"Link code for {who}: {row.code} — expires in "
            f"{current_app.config.get('TELEGRAM_LINK_CODE_TTL_MINUTES', 30)} minutes. "
            f"In Telegram send: /link {row.code}",
            "success",
        )
    except TelegramServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("settings.index"))


@settings_bp.route("/telegram/<int:telegram_user_id>/unlink", methods=["POST"])
def telegram_unlink(telegram_user_id: int):
    try:
        telegram_service.unlink_user(telegram_user_id)
        flash("Telegram account unlinked.", "success")
    except TelegramServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("settings.index"))


@settings_bp.route("/telegram/<int:telegram_user_id>/default-account", methods=["POST"])
def telegram_default_account(telegram_user_id: int):
    raw = request.form.get("default_account_id") or ""
    account_id = int(raw) if str(raw).isdigit() else None
    try:
        telegram_service.set_default_account(telegram_user_id, account_id)
        flash("Default Telegram spend account updated.", "success")
    except TelegramServiceError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("settings.index"))
