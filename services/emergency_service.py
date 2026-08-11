"""Virtual Emergency Fund — tag cash / investments without transfers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from extensions import db
from models import Account, Goal, Investment


class EmergencyValidationError(ValueError):
    pass


def get_emergency_goal() -> Goal | None:
    return (
        Goal.query.filter(
            Goal.is_active.is_(True),
            Goal.goal_type == "emergency",
        )
        .order_by(Goal.sort_order, Goal.id)
        .first()
    )


def effective_tagged(account: Account) -> Decimal:
    """Tagged amount capped at the account's current balance."""
    tagged = Decimal(account.emergency_tagged or 0)
    balance = Decimal(account.current_balance or 0)
    if tagged < 0:
        tagged = Decimal("0")
    if balance < 0:
        return Decimal("0")
    return min(tagged, balance)


def get_breakdown(goal: Goal | None = None) -> dict[str, Any]:
    """
    Emergency cover = cash tags on accounts + investments linked to Emergency goal.

    No separate Emergency bank account and no transfer required.
    """
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    account_rows = []
    cash_total = Decimal("0")
    for acc in accounts:
        amount = effective_tagged(acc)
        if amount > 0 or Decimal(acc.emergency_tagged or 0) > 0:
            account_rows.append(
                {
                    "account": acc,
                    "tagged": Decimal(acc.emergency_tagged or 0),
                    "effective": amount,
                    "balance": Decimal(acc.current_balance or 0),
                }
            )
        cash_total += amount

    goal = goal if goal is not None else get_emergency_goal()
    investment_rows = []
    invest_total = Decimal("0")
    if goal:
        holdings = (
            Investment.query.filter_by(is_active=True, goal_id=goal.id)
            .order_by(Investment.sort_order, Investment.name)
            .all()
        )
        for inv in holdings:
            value = Decimal(inv.current_value or 0)
            investment_rows.append({"investment": inv, "value": value})
            invest_total += value

    total = cash_total + invest_total
    return {
        "goal": goal,
        "account_rows": account_rows,
        "investment_rows": investment_rows,
        "cash_total": cash_total,
        "invest_total": invest_total,
        "total": total,
    }


def get_total(goal: Goal | None = None) -> Decimal:
    return get_breakdown(goal)["total"]


def save_account_tags(raw_tags: dict[int, Any]) -> int:
    """
    Update emergency_tagged for accounts.

    raw_tags: {account_id: amount}
    Returns number of accounts updated.
    """
    updated = 0
    for account_id, raw_amount in raw_tags.items():
        account = db.session.get(Account, int(account_id))
        if not account or not account.is_active:
            raise EmergencyValidationError("Invalid account for emergency tag.")
        try:
            amount = Decimal(str(raw_amount or "0").strip() or "0")
        except (InvalidOperation, ValueError) as exc:
            raise EmergencyValidationError(
                f"Invalid emergency amount for {account.name}."
            ) from exc
        if amount < 0:
            raise EmergencyValidationError(
                f"Emergency tag for {account.name} cannot be negative."
            )
        balance = Decimal(account.current_balance or 0)
        if amount > balance:
            raise EmergencyValidationError(
                f"Cannot tag {amount} on {account.name} — balance is only {balance}."
            )
        if Decimal(account.emergency_tagged or 0) != amount:
            account.emergency_tagged = amount
            updated += 1

    if updated:
        db.session.commit()
    return updated
