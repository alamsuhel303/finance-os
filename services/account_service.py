"""Account service — manage bank/fund accounts and opening balances."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from extensions import db
from models import Account, Transaction
from utils.helpers import parse_nonneg_amount


class AccountValidationError(ValueError):
    pass


ACCOUNT_TYPES = (
    "bank",
    "cash",
    "joint",
    "emergency",
    "goal",
    "investment",
)
OWNERS = ("self", "wife", "joint")

ACCOUNT_TYPE_LABELS = {
    "bank": "Bank Account",
    "cash": "Cash",
    "joint": "Joint",
    "emergency": "Emergency",
    "goal": "Goal Fund",
    "investment": "Investment",
}


def list_accounts(*, active_only: bool = False) -> list[Account]:
    query = Account.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Account.sort_order, Account.name).all()


def get_account(account_id: int) -> Optional[Account]:
    return db.session.get(Account, account_id)


def create_account(data: dict[str, Any]) -> Account:
    account = Account()
    _populate(account, data, is_new=True)
    # New account: opening = current
    opening = Decimal(account.opening_balance or 0)
    account.current_balance = opening
    db.session.add(account)
    db.session.commit()
    return account


def update_account(account: Account, data: dict[str, Any]) -> Account:
    old_opening = Decimal(account.opening_balance or 0)
    _populate(account, data, is_new=False)
    new_opening = Decimal(account.opening_balance or 0)
    # Adjust current balance by the opening-balance delta so ledger stays consistent
    delta = new_opening - old_opening
    if delta != 0:
        account.current_balance = Decimal(account.current_balance or 0) + delta
    db.session.commit()
    return account


def delete_account(account: Account) -> None:
    txn_count = Transaction.query.filter(
        (Transaction.account_id == account.id)
        | (Transaction.to_account_id == account.id)
    ).count()
    if txn_count:
        raise AccountValidationError(
            f"Cannot delete “{account.name}” — it has {txn_count} transaction(s). "
            "Deactivate it instead."
        )
    from models import Goal

    linked_goals = Goal.query.filter_by(linked_account_id=account.id).count()
    if linked_goals:
        raise AccountValidationError(
            f"Cannot delete “{account.name}” — linked to {linked_goals} goal(s). "
            "Unlink them first or deactivate the account."
        )
    db.session.delete(account)
    db.session.commit()


def rebuild_all_balances() -> list[dict[str, Any]]:
    """
    Set each account's current_balance = opening_balance + ledger effects.

    Fixes drift when balances were edited outside normal transaction flow.
    """
    from models import Transaction
    from services.transaction_service import _account_cash_delta
    from sqlalchemy import or_

    changes: list[dict[str, Any]] = []
    for account in Account.query.order_by(Account.sort_order, Account.name).all():
        expected = Decimal(account.opening_balance or 0)
        txns = (
            Transaction.query.filter(
                or_(
                    Transaction.account_id == account.id,
                    Transaction.to_account_id == account.id,
                )
            )
            .order_by(Transaction.date.asc(), Transaction.id.asc())
            .all()
        )
        for txn in txns:
            if txn.skip_cash_impact:
                continue
            expected += _account_cash_delta(txn, account.id)

        old = Decimal(account.current_balance or 0)
        if old != expected:
            changes.append(
                {
                    "account": account.name,
                    "old": old,
                    "new": expected,
                    "delta": expected - old,
                }
            )
            account.current_balance = expected

    if changes:
        db.session.commit()
    return changes


def apply_statement_balances(
    statement_date: date,
    balances: dict[int, Decimal],
) -> list[dict[str, Any]]:
    """
    Align books to bank/statement balances as of a date.

    For each account with a provided statement balance:
      opening = statement − (ledger effects on or before statement_date)
      current = opening + (all ledger effects)

    So the implied balance on statement_date equals the bank figure, and
    later transactions still apply on top.
    """
    from models import Transaction
    from services.transaction_service import _account_cash_delta
    from sqlalchemy import or_

    if not isinstance(statement_date, date):
        raise AccountValidationError("Statement date is required.")

    changes: list[dict[str, Any]] = []
    for account_id, statement_balance in balances.items():
        account = get_account(account_id)
        if not account:
            raise AccountValidationError(f"Account #{account_id} not found.")
        if statement_balance is None:
            continue

        txns = (
            Transaction.query.filter(
                or_(
                    Transaction.account_id == account.id,
                    Transaction.to_account_id == account.id,
                )
            )
            .order_by(Transaction.date.asc(), Transaction.id.asc())
            .all()
        )
        effects_through = Decimal("0")
        effects_all = Decimal("0")
        for txn in txns:
            if txn.skip_cash_impact:
                continue
            delta = _account_cash_delta(txn, account.id)
            effects_all += delta
            if txn.date <= statement_date:
                effects_through += delta

        new_opening = statement_balance - effects_through
        new_current = new_opening + effects_all
        old_opening = Decimal(account.opening_balance or 0)
        old_current = Decimal(account.current_balance or 0)
        if old_opening == new_opening and old_current == new_current:
            continue

        account.opening_balance = new_opening
        account.current_balance = new_current
        changes.append(
            {
                "account": account.name,
                "statement": statement_balance,
                "old_opening": old_opening,
                "new_opening": new_opening,
                "old_current": old_current,
                "new_current": new_current,
            }
        )

    if changes:
        db.session.commit()
    return changes


def parse_statement_amount(value: Any) -> Decimal | None:
    """Parse a statement balance (zero allowed; blank → None = skip account)."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("₹", "")
    if not text:
        return None
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise AccountValidationError(f"Invalid statement amount: {value}") from exc


def _populate(account: Account, data: dict[str, Any], *, is_new: bool) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise AccountValidationError("Account name is required.")

    existing = Account.query.filter(Account.name == name).first()
    if existing and (is_new or existing.id != account.id):
        raise AccountValidationError("An account with this name already exists.")

    account_type = (data.get("account_type") or "bank").strip().lower()
    # Back-compat: old "salary" type maps to bank
    if account_type == "salary":
        account_type = "bank"
    if account_type not in ACCOUNT_TYPES:
        raise AccountValidationError("Invalid account type.")

    owner = (data.get("owner") or "joint").strip().lower()
    if owner not in OWNERS:
        owner = "joint"

    opening = parse_nonneg_amount(data.get("opening_balance"))
    if opening is None:
        raise AccountValidationError("Opening balance must be a valid non-negative amount.")

    sort_order = 0
    try:
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0

    account.name = name
    account.account_type = account_type
    account.owner = owner
    account.opening_balance = opening
    account.sort_order = sort_order
    account.notes = (data.get("notes") or "").strip() or None
    account.is_active = str(data.get("is_active", "1")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
