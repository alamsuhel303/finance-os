"""Telegram linking, inbox, pending drafts — shared by Settings UI and bot worker."""

from __future__ import annotations

import logging
import secrets
import string
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import func

from extensions import db
from models import (
    Account,
    Category,
    TelegramCategoryAlias,
    TelegramLinkCode,
    TelegramMessage,
    TelegramPendingTransaction,
    TelegramUser,
    Transaction,
)
from services import (
    budget_service,
    envelope_service,
    profile_service,
    report_service,
    transaction_service,
)
from services.transaction_service import TransactionValidationError

logger = logging.getLogger(__name__)


class TelegramServiceError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tz() -> ZoneInfo:
    name = current_app.config.get("TELEGRAM_TIMEZONE", "Asia/Kolkata")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def today_local() -> date:
    return datetime.now(_tz()).date()


def list_linked_users() -> list[TelegramUser]:
    return (
        TelegramUser.query.order_by(TelegramUser.owner, TelegramUser.id).all()
    )


def get_user_by_telegram_id(telegram_user_id: int) -> TelegramUser | None:
    return TelegramUser.query.filter_by(
        telegram_user_id=int(telegram_user_id), is_active=True
    ).first()


def touch_user(user: TelegramUser, *, username=None, first_name=None, last_name=None) -> None:
    user.last_seen_at = _utcnow()
    if username is not None:
        user.telegram_username = username
    if first_name is not None:
        user.telegram_first_name = first_name
    if last_name is not None:
        user.telegram_last_name = last_name
    db.session.commit()


def generate_link_code(owner: str) -> TelegramLinkCode:
    if owner not in TelegramUser.OWNERS:
        raise TelegramServiceError("Invalid owner for link code.")
    if owner == "wife" and not profile_service.is_couple_mode():
        raise TelegramServiceError("Couple mode is required to link a second person.")

    ttl = int(current_app.config.get("TELEGRAM_LINK_CODE_TTL_MINUTES", 30))
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if not TelegramLinkCode.query.filter_by(code=code).first():
            break
    else:
        raise TelegramServiceError("Could not generate a unique link code.")

    row = TelegramLinkCode(
        code=code,
        owner=owner,
        expires_at=_utcnow() + timedelta(minutes=ttl),
    )
    db.session.add(row)
    db.session.commit()
    return row


def redeem_link_code(
    code: str,
    telegram_user_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> TelegramUser:
    cleaned = (code or "").strip().upper()
    if not cleaned:
        raise TelegramServiceError("Provide a link code, e.g. /link ABC123")

    row = TelegramLinkCode.query.filter_by(code=cleaned).first()
    if not row:
        raise TelegramServiceError("Invalid link code.")
    if row.used_at is not None:
        raise TelegramServiceError("This link code was already used.")
    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < _utcnow():
        raise TelegramServiceError("This link code has expired. Generate a new one in Settings.")

    existing = TelegramUser.query.filter_by(telegram_user_id=int(telegram_user_id)).first()
    if existing:
        existing.owner = row.owner
        existing.is_active = True
        existing.telegram_username = username
        existing.telegram_first_name = first_name
        existing.telegram_last_name = last_name
        existing.last_seen_at = _utcnow()
        user = existing
    else:
        user = TelegramUser(
            telegram_user_id=int(telegram_user_id),
            owner=row.owner,
            telegram_username=username,
            telegram_first_name=first_name,
            telegram_last_name=last_name,
            is_active=True,
            last_seen_at=_utcnow(),
        )
        db.session.add(user)

    row.used_at = _utcnow()
    row.used_by_telegram_user_id = int(telegram_user_id)
    db.session.commit()
    return user


def unlink_user(telegram_user_id: int) -> None:
    user = TelegramUser.query.filter_by(telegram_user_id=int(telegram_user_id)).first()
    if not user:
        raise TelegramServiceError("Linked Telegram user not found.")
    user.is_active = False
    db.session.commit()


def set_default_account(telegram_user_id: int, account_id: int | None) -> TelegramUser:
    user = TelegramUser.query.filter_by(telegram_user_id=int(telegram_user_id)).first()
    if not user or not user.is_active:
        raise TelegramServiceError("Linked Telegram user not found.")
    if account_id:
        acc = db.session.get(Account, int(account_id))
        if not acc or not acc.is_active:
            raise TelegramServiceError("Account not found.")
        user.default_account_id = acc.id
    else:
        user.default_account_id = None
    db.session.commit()
    return user


def resolve_default_account(user: TelegramUser) -> Account | None:
    if user.default_account_id:
        acc = db.session.get(Account, user.default_account_id)
        if acc and acc.is_active:
            return acc
    return envelope_service.resolve_envelope_cash_account()


def resolve_personal_account(owner: str) -> Account | None:
    return (
        Account.query.filter(
            Account.owner == owner,
            Account.is_active.is_(True),
            Account.account_type.in_(("bank", "salary")),
        )
        .order_by(Account.sort_order, Account.id)
        .first()
    )


def resolve_category_alias(text: str) -> Category | None:
    key = (text or "").strip().lower()
    if not key:
        return None
    alias = TelegramCategoryAlias.query.filter_by(alias=key).first()
    if alias and alias.category:
        return alias.category
    # Also match category name / slug loosely
    cat = Category.query.filter(
        Category.is_active.is_(True),
        Category.parent_id.is_(None),
        Category.category_type == "expense",
        func.lower(Category.name) == key,
    ).first()
    return cat


def find_alias_in_tokens(tokens: list[str]) -> Category | None:
    for token in tokens:
        cat = resolve_category_alias(token)
        if cat:
            return cat
    # multi-word: try pairs
    for i in range(len(tokens) - 1):
        cat = resolve_category_alias(f"{tokens[i]} {tokens[i+1]}")
        if cat:
            return cat
    return None


def record_incoming_update(
    *,
    update_id: int,
    message_id: int | None,
    telegram_user_id: int,
    chat_id: int,
    text: str | None,
) -> tuple[TelegramMessage, bool]:
    """
    Persist inbox row. Returns (row, is_new).
    If update_id already exists, returns existing row and is_new=False (idempotent).
    """
    existing = TelegramMessage.query.filter_by(
        telegram_update_id=int(update_id)
    ).first()
    if existing:
        return existing, False

    row = TelegramMessage(
        telegram_update_id=int(update_id),
        telegram_message_id=message_id,
        telegram_user_id=int(telegram_user_id),
        chat_id=int(chat_id),
        message_text=(text or "")[:4000] or None,
        status="received",
        received_at=_utcnow(),
    )
    db.session.add(row)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        existing = TelegramMessage.query.filter_by(
            telegram_update_id=int(update_id)
        ).first()
        if existing:
            return existing, False
        raise
    return row, True


def mark_message(
    row: TelegramMessage,
    status: str,
    *,
    error: str | None = None,
    transaction_id: int | None = None,
) -> None:
    row.status = status
    if error is not None:
        row.error_message = error[:2000]
    if transaction_id is not None:
        row.transaction_id = transaction_id
    if status in ("processed", "failed", "ignored"):
        row.processed_at = _utcnow()
    db.session.commit()


def create_pending(
    *,
    user: TelegramUser,
    chat_id: int,
    message_row: TelegramMessage | None,
    amount: Decimal,
    description: str,
    category_id: int | None,
    account_id: int | None,
    txn_date: date,
    paid_by: str,
    merchant: str | None = None,
) -> TelegramPendingTransaction:
    ttl = int(current_app.config.get("TELEGRAM_PENDING_TTL_MINUTES", 60))
    expires = _utcnow() + timedelta(minutes=ttl)

    # Reuse draft for the same inbox message (restart / redelivery safe)
    existing = None
    if message_row is not None:
        existing = (
            TelegramPendingTransaction.query.filter_by(
                telegram_message_row_id=message_row.id,
                telegram_user_id=user.telegram_user_id,
            )
            .order_by(TelegramPendingTransaction.id.desc())
            .first()
        )
        if existing and existing.status in ("pending", "expired"):
            existing.status = "pending"
            existing.chat_id = int(chat_id)
            existing.amount = amount
            existing.merchant = (merchant or "").strip() or None
            existing.description = description.strip()
            existing.category_id = category_id
            existing.account_id = account_id
            existing.transaction_date = txn_date
            existing.paid_by = (
                paid_by if paid_by in Transaction.PAID_BY_CHOICES else user.owner
            )
            existing.edit_field = None
            existing.expires_at = expires
            existing.confirmed_transaction_id = None
            db.session.commit()
            return existing

    # Expire other open drafts for this user (not the one we may reuse above)
    q = TelegramPendingTransaction.query.filter_by(
        telegram_user_id=user.telegram_user_id, status="pending"
    )
    if existing:
        q = q.filter(TelegramPendingTransaction.id != existing.id)
    q.update({"status": "expired"})

    pending = TelegramPendingTransaction(
        telegram_user_id=user.telegram_user_id,
        chat_id=int(chat_id),
        telegram_message_row_id=message_row.id if message_row else None,
        amount=amount,
        merchant=(merchant or "").strip() or None,
        description=description.strip(),
        category_id=category_id,
        account_id=account_id,
        transaction_date=txn_date,
        paid_by=paid_by if paid_by in Transaction.PAID_BY_CHOICES else user.owner,
        status="pending",
        expires_at=expires,
    )
    db.session.add(pending)
    db.session.commit()
    return pending


def touch_pending(pending: TelegramPendingTransaction) -> TelegramPendingTransaction:
    """Extend draft TTL and ensure status stays pending during edit/confirm UI."""
    ttl = int(current_app.config.get("TELEGRAM_PENDING_TTL_MINUTES", 60))
    if pending.status == "expired":
        pending.status = "pending"
    if pending.status == "pending":
        pending.expires_at = _utcnow() + timedelta(minutes=ttl)
        db.session.commit()
    return pending


def get_pending(pending_id: int) -> TelegramPendingTransaction | None:
    return db.session.get(TelegramPendingTransaction, pending_id)


def require_editable_pending(
    pending_id: int, telegram_user_id: int
) -> TelegramPendingTransaction:
    """Load a draft for Edit/Confirm; revive expired drafts that were never confirmed."""
    pending = get_pending(pending_id)
    if not pending:
        raise TelegramServiceError("Draft not found. Send the expense again.")
    if int(pending.telegram_user_id) != int(telegram_user_id):
        raise TelegramServiceError("This action is not available to you.")
    if pending.status == "confirmed":
        raise TelegramServiceError("This draft was already confirmed.")
    if pending.status == "cancelled":
        raise TelegramServiceError("This draft was cancelled. Send the expense again.")
    if pending.status in ("pending", "expired"):
        # Revive expired drafts so Edit still works after bot restarts / delays
        pending.status = "pending"
        touch_pending(pending)
        return pending
    raise TelegramServiceError("This draft is no longer available.")


def get_active_pending_for_user(telegram_user_id: int) -> TelegramPendingTransaction | None:
    now = _utcnow()
    row = (
        TelegramPendingTransaction.query.filter_by(
            telegram_user_id=int(telegram_user_id), status="pending"
        )
        .order_by(TelegramPendingTransaction.id.desc())
        .first()
    )
    if not row:
        return None
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        row.status = "expired"
        db.session.commit()
        return None
    return row


def cancel_pending(pending: TelegramPendingTransaction, telegram_user_id: int) -> None:
    if int(pending.telegram_user_id) != int(telegram_user_id):
        raise TelegramServiceError("This action is not available to you.")
    if pending.status not in ("pending", "expired"):
        raise TelegramServiceError("This draft is no longer pending.")
    pending.status = "cancelled"
    db.session.commit()


def confirm_pending(pending: TelegramPendingTransaction, telegram_user_id: int) -> Transaction:
    """Create Finance OS transaction via existing service. Idempotent if already confirmed."""
    if not pending:
        raise TelegramServiceError("Draft not found. Send the expense again.")
    if int(pending.telegram_user_id) != int(telegram_user_id):
        raise TelegramServiceError("This action is not available to you.")

    if pending.status == "confirmed" and pending.confirmed_transaction_id:
        txn = db.session.get(Transaction, pending.confirmed_transaction_id)
        if txn:
            return txn

    pending = require_editable_pending(pending.id, telegram_user_id)

    if not pending.account_id:
        raise TelegramServiceError("Choose an account before confirming.")
    if not pending.category_id:
        raise TelegramServiceError("Choose a category before confirming.")

    desc = pending.description
    if pending.merchant:
        desc = f"{pending.merchant} · {pending.description}"

    msg_id = pending.telegram_message_row_id
    payload = {
        "amount": str(pending.amount),
        "description": desc[:255],
        "transaction_type": "expense",
        "account_id": pending.account_id,
        "category_id": pending.category_id,
        "date": pending.transaction_date.isoformat(),
        "paid_by": pending.paid_by,
        "payment_mode": "upi",
        "need_want": "need",
        "source": "telegram",
        "telegram_message_id": msg_id,
    }

    try:
        txn, _warning = transaction_service.create_transaction(payload)
    except TransactionValidationError as exc:
        if msg_id:
            msg = db.session.get(TelegramMessage, msg_id)
            if msg:
                mark_message(msg, "failed", error=str(exc))
        raise TelegramServiceError(str(exc)) from exc

    pending.status = "confirmed"
    pending.confirmed_transaction_id = txn.id
    if msg_id:
        msg = db.session.get(TelegramMessage, msg_id)
        if msg:
            msg.status = "processed"
            msg.processed_at = _utcnow()
            msg.transaction_id = txn.id
    db.session.commit()
    return txn


def last_telegram_transaction_for_user(telegram_user_id: int) -> Transaction | None:
    msg = (
        TelegramMessage.query.filter(
            TelegramMessage.telegram_user_id == int(telegram_user_id),
            TelegramMessage.transaction_id.isnot(None),
            TelegramMessage.status == "processed",
        )
        .order_by(TelegramMessage.id.desc())
        .first()
    )
    if not msg or not msg.transaction_id:
        return None
    return db.session.get(Transaction, msg.transaction_id)


def undo_telegram_transaction(txn: Transaction, telegram_user_id: int) -> None:
    if (txn.source or "") != "telegram":
        raise TelegramServiceError("Only Telegram-created transactions can be undone here.")
    msg = None
    if txn.telegram_message_id:
        msg = db.session.get(TelegramMessage, txn.telegram_message_id)
    if msg and int(msg.telegram_user_id) != int(telegram_user_id):
        raise TelegramServiceError("This action is not available to you.")
    # Also allow if linked via transaction_id on message
    if not msg:
        msg = TelegramMessage.query.filter_by(transaction_id=txn.id).first()
        if msg and int(msg.telegram_user_id) != int(telegram_user_id):
            raise TelegramServiceError("This action is not available to you.")
    transaction_service.delete_transaction(txn)


def list_today_expenses(limit: int = 20) -> list[Transaction]:
    d = today_local()
    return (
        Transaction.query.filter(
            Transaction.date == d,
            Transaction.transaction_type == "expense",
        )
        .order_by(Transaction.id.desc())
        .limit(limit)
        .all()
    )


def list_recent_expenses(limit: int = 10) -> list[Transaction]:
    return (
        Transaction.query.filter(Transaction.transaction_type == "expense")
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(limit)
        .all()
    )


def format_inr(amount: Decimal | float | int | None) -> str:
    sym = current_app.config.get("CURRENCY_SYMBOL", "₹")
    try:
        return f"{sym}{float(amount or 0):,.0f}"
    except (TypeError, ValueError):
        return f"{sym}0"


def _sym() -> str:
    return current_app.config.get("CURRENCY_SYMBOL", "₹")


def month_summary_text() -> str:
    from telegram_bot.formatting import bold, card, code, money

    summary = report_service.get_period_summary(period="monthly")
    label = summary.get("label") or today_local().strftime("%B %Y")
    sym = _sym()
    body = [
        f"💵 Income · {bold(money(summary.get('income'), sym))}",
        f"💸 Net expenses · {bold(money(summary.get('net_expenses'), sym))}",
        f"🏦 Savings · {bold(money(summary.get('savings'), sym))}",
        f"🧾 Transactions · {code(str(summary.get('transaction_count', 0)))}",
    ]
    by_cat = summary.get("by_category") or []
    if by_cat:
        body.append("")
        body.append(bold("Top categories"))
        for row in by_cat[:8]:
            name = row.get("name") or row.get("category") or "—"
            amt = money(row.get("total") or row.get("amount") or 0, sym)
            body.append(f"• {bold(name)} — {amt}")
    return card(f"📊 {label}", body)


def budget_summary_text() -> str:
    """Plain-text budget status (no HTML) — easier to read in Telegram."""
    overview = budget_service.get_budget_overview()
    label = overview.get("month_label") or today_local().strftime("%B %Y")
    lines = [f"Budget — {label}", ""]
    for row in overview.get("rows") or []:
        cat = row.get("category")
        name = cat.name if cat else "—"
        budgeted = Decimal(str(row.get("budgeted") or 0))
        actual = Decimal(str(row.get("actual") or 0))
        remaining = Decimal(str(row.get("remaining") or 0))
        if budgeted <= 0 and actual <= 0:
            continue
        lines.append(f"{name}")
        lines.append(
            f"  {format_inr(actual)} / {format_inr(budgeted)}  left {format_inr(remaining)}"
        )
        lines.append("")
    lines.append(
        f"Total  {format_inr(overview.get('total_actual'))} / "
        f"{format_inr(overview.get('total_budgeted'))}"
    )
    lines.append(f"Left   {format_inr(overview.get('total_remaining'))}")
    if overview.get("needs_setup"):
        lines.extend(["", "Budget needs setup in the web app."])
    return "\n".join(lines).rstrip()


def envelopes_summary_text() -> str:
    """Plain-text envelope balances."""
    from services import envelope_service

    overview = envelope_service.get_envelopes_overview()
    label = overview.get("month_label") or today_local().strftime("%B %Y")
    joint = overview.get("joint_account")
    joint_name = joint.name if joint else "Cash"
    lines = [f"Envelopes — {label}", ""]
    rows = overview.get("rows") or []
    if not rows:
        lines.append("No envelopes set up yet.")
        return "\n".join(lines)
    for row in rows:
        env = row["envelope"]
        bal = row.get("balance") or 0
        spent = row.get("spent") or 0
        allocated = row.get("allocated") or 0
        lines.append(f"{env.name}")
        lines.append(f"  Balance  {format_inr(bal)}")
        if allocated or spent:
            lines.append(
                f"  This month  in {format_inr(allocated)}  spent {format_inr(spent)}"
            )
        lines.append("")
    lines.append(f"Pots total  {format_inr(overview.get('total'))}")
    lines.append(f"{joint_name}  {format_inr(overview.get('joint_balance'))}")
    diff = Decimal(str(overview.get("difference") or 0))
    if diff != 0:
        lines.append(f"Unlabelled  {format_inr(diff)}")
    return "\n".join(lines).rstrip()


def status_text() -> str:
    from sqlalchemy import text

    from telegram_bot.formatting import bold, card, code

    pending_msgs = TelegramMessage.query.filter(
        TelegramMessage.status.in_(("received", "processing"))
    ).count()
    pending_drafts = TelegramPendingTransaction.query.filter_by(status="pending").count()
    last = (
        TelegramMessage.query.filter(TelegramMessage.processed_at.isnot(None))
        .order_by(TelegramMessage.processed_at.desc())
        .first()
    )
    last_ts = last.processed_at.strftime("%d %b %Y, %H:%M") if last and last.processed_at else "—"
    linked = TelegramUser.query.filter_by(is_active=True).count()
    try:
        db.session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return card(
        "🛰 Bot status",
        [
            f"Bot · {bold('Connected')}",
            f"Database · {bold('Connected') if db_ok else bold('Error')}",
            f"Linked users · {code(str(linked))}",
            f"Pending inbox · {code(str(pending_msgs))}",
            f"Open drafts · {code(str(pending_drafts))}",
            f"Last processed · {code(last_ts)}",
        ],
    )


def expense_categories(limit: int | None = None) -> list[Category]:
    """All active top-level expense categories (with envelope relationship loaded)."""
    from sqlalchemy.orm import joinedload

    q = (
        Category.query.options(joinedload(Category.envelope))
        .filter_by(is_active=True, parent_id=None, category_type="expense")
        .order_by(Category.sort_order, Category.name)
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()


def category_button_label(category: Category) -> str:
    """Category name plus envelope pot, truncated for Telegram button text."""
    env_name = None
    if getattr(category, "envelope", None) is not None:
        env_name = category.envelope.name
    elif category.envelope_id:
        from models import Envelope

        env = db.session.get(Envelope, category.envelope_id)
        env_name = env.name if env else None
    if env_name:
        label = f"{category.name} · {env_name}"
    else:
        label = category.name
    return label[:64]


def spending_accounts() -> list[Account]:
    return (
        Account.query.filter(
            Account.is_active.is_(True),
            Account.account_type.in_(("bank", "salary", "joint", "cash")),
        )
        .order_by(Account.sort_order, Account.name)
        .all()
    )


def pending_summary_lines(pending: TelegramPendingTransaction) -> str:
    from telegram_bot.formatting import bold, card, code, esc, italic, money

    labels = profile_service.get_owner_labels()
    cat = pending.category.name if pending.category else "—"
    env = "—"
    if pending.category and pending.category.envelope_id:
        from models import Envelope

        envelope = (
            pending.category.envelope
            if getattr(pending.category, "envelope", None) is not None
            else db.session.get(Envelope, pending.category.envelope_id)
        )
        if envelope:
            env = envelope.name
    acc = pending.account.name if pending.account else "—"
    payer = labels.get(pending.paid_by, pending.paid_by)
    sym = _sym()
    body = [
        f"💰 {bold(money(pending.amount, sym))}",
    ]
    if pending.merchant:
        body.append(f"🏪 Merchant · {esc(pending.merchant)}")
    body.extend(
        [
            f"📝 {esc(pending.description)}",
            f"🏷 Category · {bold(cat)}",
            f"📦 Envelope · {esc(env)}",
            f"👤 Paid by · {esc(payer)}",
            f"🏦 Account · {esc(acc)}",
            f"📅 Date · {code(pending.transaction_date.isoformat())}",
        ]
    )
    return card("New transaction", body, italic("Confirm to save to Finance OS"))


def today_expenses_text(txns: list[Transaction]) -> str:
    from telegram_bot.formatting import bold, card, esc, italic, money

    sym = _sym()
    if not txns:
        return card("📅 Today", [italic("No expenses yet.")])
    total = sum((Decimal(t.amount or 0) for t in txns), Decimal("0"))
    body = []
    for t in txns:
        cat = t.category.name if t.category else "—"
        body.append(
            f"{bold(money(t.amount, sym))} · {esc(cat)}\n"
            f"   {esc(t.description)}"
        )
    body.append("")
    body.append(f"{bold('Total')} · {money(total, sym)} · {len(txns)} txn(s)")
    return card("📅 Today", body)


def recent_expenses_text(txns: list[Transaction]) -> str:
    from telegram_bot.formatting import bold, card, code, esc, italic, money

    sym = _sym()
    if not txns:
        return card("📋 Recent", [italic("No expenses yet.")])
    body = []
    for i, t in enumerate(txns, 1):
        cat = t.category.name if t.category else "—"
        body.append(
            f"{bold(f'{i}.')} {bold(money(t.amount, sym))} · {esc(cat)}\n"
            f"   {esc(t.description)} · {code(f'#{t.id}')}"
        )
    return card("📋 Recent", body)


def undo_preview_text(txn: Transaction) -> str:
    from telegram_bot.formatting import bold, card, esc, italic, money

    cat = txn.category.name if txn.category else "—"
    return card(
        "Undo last Telegram expense?",
        [
            f"💰 {bold(money(txn.amount, _sym()))}",
            f"🏷 {esc(cat)}",
            f"📝 {esc(txn.description)}",
        ],
        italic("This permanently removes it from the ledger."),
    )


def success_added_text(txn: Transaction) -> str:
    from telegram_bot.formatting import bold, card, esc, money

    cat = txn.category.name if txn.category else "—"
    return card(
        "✅ Saved",
        [
            f"{bold(money(txn.amount, _sym()))} · {bold(cat)}",
            esc(txn.description),
        ],
    )
