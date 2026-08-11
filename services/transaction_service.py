"""Transaction service — CRUD plus account balance + envelope updates."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import or_

from extensions import db
from models import Account, Category, Investment, Transaction
from services import envelope_service
from services.envelope_service import EnvelopeValidationError
from utils.helpers import parse_amount


class TransactionValidationError(ValueError):
    pass


def _apply_balance_delta(account: Account, delta: Decimal) -> None:
    account.current_balance = Decimal(account.current_balance or 0) + delta


def _signed_cash_impact(txn: Transaction) -> Decimal:
    """How this transaction changes the source account balance."""
    amount = Decimal(txn.amount or 0)
    if txn.transaction_type in ("income", "refund"):
        return amount
    if txn.transaction_type in ("expense", "investment", "transfer"):
        return -amount
    return Decimal("0")


def apply_transaction_to_balances(txn: Transaction, reverse: bool = False) -> None:
    """Update account balances for a transaction (or reverse on delete/edit)."""
    if not txn.skip_cash_impact:
        factor = Decimal("-1") if reverse else Decimal("1")
        source = db.session.get(Account, txn.account_id)
        if not source:
            raise TransactionValidationError("Source account not found.")

        delta = _signed_cash_impact(txn) * factor
        _apply_balance_delta(source, delta)

        if txn.transaction_type == "transfer" and txn.to_account_id:
            destination = db.session.get(Account, txn.to_account_id)
            if not destination:
                raise TransactionValidationError("Destination account not found.")
            _apply_balance_delta(destination, Decimal(txn.amount or 0) * factor)

    _apply_investment_installment(txn, reverse=reverse)


def _apply_investment_installment(txn: Transaction, *, reverse: bool = False) -> None:
    """Bump invested/current when an investment txn is linked to a holding."""
    if txn.transaction_type != "investment" or not txn.investment_id:
        return
    inv = db.session.get(Investment, txn.investment_id)
    if not inv:
        return
    amount = Decimal(txn.amount or 0)
    delta = -amount if reverse else amount
    invested = Decimal(inv.invested_amount or 0) + delta
    current = Decimal(inv.current_value or 0) + delta
    inv.invested_amount = max(invested, Decimal("0"))
    inv.current_value = max(current, Decimal("0"))


def _expense_warnings(
    data: Any, expense_env, amount
) -> str | None:
    return envelope_service.merge_warnings(
        envelope_service.category_envelope_mismatch_warning(
            category_id=_optional_int(data.get("category_id")),
            envelope_id=_optional_int(data.get("envelope_id")),
        ),
        envelope_service.expense_envelope_warning(expense_env, amount),
    )


def create_transaction(data: Any) -> tuple[Transaction, str | None]:
    try:
        splits, expense_env = _prepare_envelope_side_effects(data)
        amount = parse_amount(data.get("amount"))
        warning = _expense_warnings(data, expense_env, amount)
        txn = _build_transaction(data)
        _ensure_sufficient_funds(txn)
        db.session.add(txn)
        db.session.flush()
        apply_transaction_to_balances(txn)
        envelope_service.apply_envelope_entries_for_transaction(
            txn,
            splits=splits,
            expense_envelope=expense_env,
        )
        db.session.commit()
        return txn, warning
    except EnvelopeValidationError as exc:
        db.session.rollback()
        raise TransactionValidationError(str(exc)) from exc


def update_transaction(txn: Transaction, data: Any) -> tuple[Transaction, str | None]:
    try:
        splits, expense_env = _prepare_envelope_side_effects(data)
        amount = parse_amount(data.get("amount"))
        # After reverse, balance is restored — warn against that restored balance
        envelope_service.reverse_envelope_entries_for_transaction(txn)
        warning = _expense_warnings(data, expense_env, amount)
        apply_transaction_to_balances(txn, reverse=True)
        _populate_transaction(txn, data)
        _ensure_sufficient_funds(txn)
        apply_transaction_to_balances(txn)
        envelope_service.apply_envelope_entries_for_transaction(
            txn,
            splits=splits,
            expense_envelope=expense_env,
        )
        db.session.commit()
        return txn, warning
    except EnvelopeValidationError as exc:
        db.session.rollback()
        raise TransactionValidationError(str(exc)) from exc


def _ensure_sufficient_funds(txn: Transaction) -> None:
    """Block expense / transfer / investment if source account can't cover it."""
    if txn.skip_cash_impact:
        return
    if txn.transaction_type not in ("expense", "transfer", "investment"):
        return
    account = db.session.get(Account, txn.account_id)
    if not account:
        raise TransactionValidationError("Source account not found.")
    available = Decimal(account.current_balance or 0)
    amount = Decimal(txn.amount or 0)
    if available < amount:
        raise TransactionValidationError(
            f"Insufficient balance in {account.name}. "
            f"Available {available}, tried to use {amount}."
        )


def delete_transaction(txn: Transaction) -> None:
    envelope_service.reverse_envelope_entries_for_transaction(txn)
    apply_transaction_to_balances(txn, reverse=True)
    db.session.delete(txn)
    db.session.commit()


def _prepare_envelope_side_effects(data: Any):
    """Validate and return (transfer_splits, expense_envelope)."""
    txn_type = (data.get("transaction_type") or "expense").strip().lower()
    amount = parse_amount(data.get("amount"))
    splits = None
    expense_env = None

    if txn_type == "transfer":
        splits = envelope_service.parse_transfer_splits(data)
        if amount is None and splits:
            raise EnvelopeValidationError("Enter a valid transfer amount.")

        # Salary → spending pot with no split: default 100% to Essentials
        if not splits and amount is not None:
            from_acc = db.session.get(Account, _optional_int(data.get("account_id")))
            to_acc = db.session.get(Account, _optional_int(data.get("to_account_id")))
            from_owner = (from_acc.owner or "").lower() if from_acc else ""
            if (
                envelope_service.is_joint_account(to_acc)
                and from_owner in ("self", "wife")
            ):
                splits = envelope_service.default_essentials_split(amount)

        if splits:
            envelope_service.validate_splits_against_total(splits, amount)
    elif txn_type == "expense":
        from_acc = db.session.get(Account, _optional_int(data.get("account_id")))
        # Envelopes label Joint cash only. Personal account spends hit Budget, not pots.
        if envelope_service.is_joint_account(from_acc):
            expense_env = envelope_service.resolve_envelope_for_expense(
                envelope_id=_optional_int(data.get("envelope_id")),
                category_id=_optional_int(data.get("category_id")),
            )
            # Joint cash leaving the bank must reduce a pot label too,
            # otherwise Joint drops while envelopes stay high → "over-allocated".
            if not expense_env:
                expense_env = envelope_service.get_essentials_envelope()

    return splits, expense_env


def _account_cash_delta(txn: Transaction, account_id: int) -> Decimal:
    """Signed effect of txn on a specific account's cash balance."""
    amount = Decimal(txn.amount or 0)
    if txn.transaction_type in ("income", "refund"):
        return amount if txn.account_id == account_id else Decimal("0")
    if txn.transaction_type in ("expense", "investment"):
        return -amount if txn.account_id == account_id else Decimal("0")
    if txn.transaction_type == "transfer":
        if txn.account_id == account_id:
            return -amount
        if txn.to_account_id == account_id:
            return amount
    return Decimal("0")


def build_account_running_balances(account_id: int) -> dict[int, Decimal]:
    """
    Balance AFTER each transaction for an account (newest→oldest walk).
    Keys are transaction ids.
    """
    account = db.session.get(Account, account_id)
    if not account:
        return {}

    txns = (
        Transaction.query.filter(
            or_(
                Transaction.account_id == account_id,
                Transaction.to_account_id == account_id,
            )
        )
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    bal = Decimal(account.current_balance or 0)
    out: dict[int, Decimal] = {}
    for txn in txns:
        out[txn.id] = bal
        bal -= _account_cash_delta(txn, account_id)
    return out


def get_transaction(txn_id: int) -> Optional[Transaction]:
    return db.session.get(Transaction, txn_id)


def list_transactions(
    *,
    page: int = 1,
    per_page: int = 25,
    search: str | None = None,
    transaction_type: str | None = None,
    category_id: int | None = None,
    account_id: int | None = None,
    paid_by: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    query = Transaction.query

    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Transaction.description.ilike(like),
                Transaction.notes.ilike(like),
            )
        )
    if transaction_type:
        query = query.filter(Transaction.transaction_type == transaction_type)
    if category_id:
        query = query.filter(Transaction.category_id == category_id)
    if account_id:
        # Include transfers where this account is source or destination
        query = query.filter(
            or_(
                Transaction.account_id == account_id,
                Transaction.to_account_id == account_id,
            )
        )
    if paid_by:
        query = query.filter(Transaction.paid_by == paid_by)
    if date_from:
        query = query.filter(Transaction.date >= date_from)
    if date_to:
        query = query.filter(Transaction.date <= date_to)

    query = query.order_by(Transaction.date.desc(), Transaction.id.desc())
    return query.paginate(page=page, per_page=per_page, error_out=False)


def _build_transaction(data: Any) -> Transaction:
    txn = Transaction()
    _populate_transaction(txn, data)
    return txn


def _populate_transaction(txn: Transaction, data: Any) -> None:
    amount = parse_amount(data.get("amount"))
    if amount is None:
        raise TransactionValidationError("Enter a valid amount greater than zero.")

    description = (data.get("description") or "").strip()
    if not description:
        raise TransactionValidationError("Description is required.")

    txn_type = (data.get("transaction_type") or "expense").strip().lower()
    if txn_type not in Transaction.TRANSACTION_TYPES:
        raise TransactionValidationError("Invalid transaction type.")

    try:
        account_id = int(data.get("account_id"))
    except (TypeError, ValueError):
        raise TransactionValidationError("Select an account.") from None

    account = db.session.get(Account, account_id)
    if not account or not account.is_active:
        raise TransactionValidationError("Selected account is invalid.")

    to_account_id = None
    if txn_type == "transfer":
        try:
            to_account_id = int(data.get("to_account_id"))
        except (TypeError, ValueError):
            raise TransactionValidationError(
                "Select a destination account for transfers."
            ) from None
        if to_account_id == account_id:
            raise TransactionValidationError(
                "Source and destination accounts must differ."
            )
        to_account = db.session.get(Account, to_account_id)
        if not to_account or not to_account.is_active:
            raise TransactionValidationError("Destination account is invalid.")

    category_id = _optional_int(data.get("category_id"))
    subcategory_id = _optional_int(data.get("subcategory_id"))

    category = None
    if category_id:
        category = db.session.get(Category, category_id)
        if not category:
            raise TransactionValidationError("Selected category is invalid.")

    txn_date = _parse_date(data.get("date"))

    # Derive paid_by from account owner when not a joint spending account
    account_owner = (account.owner or "joint").lower()
    paid_by = (data.get("paid_by") or "").lower()
    if txn_type in ("income", "transfer", "investment"):
        paid_by = account_owner if account_owner in Transaction.PAID_BY_CHOICES else "joint"
    elif account_owner in ("self", "wife"):
        paid_by = account_owner
    elif paid_by not in Transaction.PAID_BY_CHOICES:
        paid_by = "joint"

    if txn_type == "income":
        payment_mode = "other"
    else:
        payment_mode = (data.get("payment_mode") or "upi").lower()
        if payment_mode not in Transaction.PAYMENT_MODES:
            payment_mode = "other"

    need_want = (data.get("need_want") or "need").lower()
    if need_want not in Transaction.NEED_WANT_CHOICES:
        need_want = "n/a"
    if txn_type in ("income", "transfer", "refund", "investment"):
        need_want = "n/a"

    # Categories: expense/refund use expense cats; income uses income cats
    if txn_type in ("expense", "refund"):
        if category and category.category_type not in ("expense", "refund"):
            raise TransactionValidationError(
                "Category must be an expense category for this transaction type."
            )
    elif txn_type == "income":
        if category and category.category_type != "income":
            raise TransactionValidationError(
                "Category must be an income category (e.g. Salary)."
            )
        subcategory_id = None
    else:
        category_id = None
        subcategory_id = None

    txn.date = txn_date
    txn.amount = amount
    txn.description = description
    txn.transaction_type = txn_type
    txn.category_id = category_id
    txn.subcategory_id = subcategory_id
    txn.account_id = account_id
    txn.to_account_id = to_account_id
    txn.paid_by = paid_by
    txn.payment_mode = payment_mode
    txn.need_want = need_want
    txn.notes = (data.get("notes") or "").strip() or None
    if txn_type != "expense":
        txn.envelope_id = None

    # Parents / family support sits outside the ~₹1.5L Essentials household budget
    if "is_excluded_from_budget" in data:
        txn.is_excluded_from_budget = str(data.get("is_excluded_from_budget")).lower() in (
            "1",
            "true",
            "on",
            "yes",
        )
    elif txn_type == "expense" and category:
        from utils.seed import BUDGET_EXCLUDED_CATEGORY_SLUGS

        slug = (category.slug or "").strip().lower()
        txn.is_excluded_from_budget = slug in BUDGET_EXCLUDED_CATEGORY_SLUGS
    elif txn_type != "expense":
        txn.is_excluded_from_budget = False

    investment_id = _optional_int(data.get("investment_id"))
    if txn_type == "investment" and investment_id:
        inv = db.session.get(Investment, investment_id)
        if not inv:
            raise TransactionValidationError("Selected investment is invalid.")
        txn.investment_id = investment_id
    else:
        txn.investment_id = None

    txn.skip_cash_impact = str(data.get("skip_cash_impact", "")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def _optional_int(value) -> Optional[int]:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return date.today()
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise TransactionValidationError("Invalid date format. Use YYYY-MM-DD.") from exc
