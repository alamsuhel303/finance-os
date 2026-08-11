"""Goal service — CRUD and progress calculations."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import Account, Goal, Investment, Transaction
from utils.helpers import parse_nonneg_amount


class GoalValidationError(ValueError):
    pass


def list_goals(*, active_only: bool = True) -> list[Goal]:
    query = Goal.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Goal.sort_order, Goal.name).all()


def get_goal(goal_id: int) -> Optional[Goal]:
    return db.session.get(Goal, goal_id)


def get_goals_overview() -> dict[str, Any]:
    goals = list_goals(active_only=True)
    rows = []
    total_target = Decimal("0")
    total_current = Decimal("0")

    for goal in goals:
        current = goal.effective_current
        target = Decimal(goal.target_amount or 0)
        remaining = goal.remaining
        progress = goal.progress_pct
        eta = _estimate_completion(goal)

        total_target += target
        total_current += current

        holdings = (
            Investment.query.filter_by(goal_id=goal.id, is_active=True).count()
        )
        rows.append(
            {
                "goal": goal,
                "current": current,
                "target": target,
                "remaining": remaining,
                "progress": round(progress, 1),
                "eta": eta,
                "holding_count": holdings,
                "status": (
                    "done"
                    if progress >= 100
                    else "on_track"
                    if progress >= 50
                    else "building"
                ),
            }
        )

    return {
        "rows": rows,
        "count": len(rows),
        "total_target": total_target,
        "total_current": total_current,
        "overall_progress": (
            round(float((total_current / total_target) * 100), 1)
            if total_target > 0
            else 0.0
        ),
    }


def get_goal_detail(goal_id: int) -> Optional[dict[str, Any]]:
    """
    Goal ledger: tagged holdings + contribution history (how purpose accumulated).

    Investment Purpose total = sum(holding.current_value).
    History rows = investment txns linked to those holdings, plus cash movements
    on a linked fund account when present.
    """
    goal = get_goal(goal_id)
    if not goal:
        return None

    holdings = (
        Investment.query.filter_by(goal_id=goal.id, is_active=True)
        .order_by(Investment.sort_order, Investment.name)
        .all()
    )
    holding_ids = [h.id for h in holdings]
    invested = sum((Decimal(h.invested_amount or 0) for h in holdings), Decimal("0"))
    current = sum((Decimal(h.current_value or 0) for h in holdings), Decimal("0"))

    inv_txns: list[Transaction] = []
    if holding_ids:
        inv_txns = (
            Transaction.query.options(
                joinedload(Transaction.account),
                joinedload(Transaction.investment),
            )
            .filter(
                Transaction.investment_id.in_(holding_ids),
                Transaction.transaction_type == "investment",
            )
            .order_by(Transaction.date.asc(), Transaction.id.asc())
            .all()
        )

    cash_txns: list[Transaction] = []
    if goal.linked_account_id:
        cash_txns = (
            Transaction.query.options(
                joinedload(Transaction.account),
                joinedload(Transaction.to_account),
            )
            .filter(
                or_(
                    Transaction.account_id == goal.linked_account_id,
                    Transaction.to_account_id == goal.linked_account_id,
                )
            )
            .order_by(Transaction.date.asc(), Transaction.id.asc())
            .all()
        )

    # Unified chronological ledger for display (newest first after build)
    ledger = _build_goal_ledger_rows(
        investment_txns=inv_txns,
        cash_txns=cash_txns,
        linked_account_id=goal.linked_account_id,
    )

    contrib_total = sum(
        (row["amount"] for row in ledger if row["kind"] == "investment"),
        Decimal("0"),
    )
    prior_basis = max(Decimal("0"), invested - contrib_total)

    return {
        "goal": goal,
        "holdings": holdings,
        "holding_count": len(holdings),
        "invested": invested,
        "current": current,
        "gain": current - invested,
        "target": Decimal(goal.target_amount or 0),
        "progress": round(goal.progress_pct, 1),
        "remaining": goal.remaining,
        "effective_current": goal.effective_current,
        "contribution_total": contrib_total,
        "prior_basis": prior_basis,
        "ledger": list(reversed(ledger)),  # newest first
        "ledger_count": len(ledger),
    }


def _build_goal_ledger_rows(
    *,
    investment_txns: list[Transaction],
    cash_txns: list[Transaction],
    linked_account_id: int | None,
) -> list[dict[str, Any]]:
    """Chronological rows with running contribution (investments) / cash balance."""
    events: list[tuple[date, int, str, Transaction]] = []
    for txn in investment_txns:
        events.append((txn.date, txn.id, "investment", txn))
    for txn in cash_txns:
        events.append((txn.date, txn.id, "cash", txn))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    running_invested = Decimal("0")
    running_cash = Decimal("0")
    rows: list[dict[str, Any]] = []

    for _, _, kind, txn in events:
        amount = Decimal(txn.amount or 0)
        if kind == "investment":
            running_invested += amount
            rows.append(
                {
                    "kind": "investment",
                    "txn": txn,
                    "date": txn.date,
                    "description": txn.description,
                    "holding_name": txn.investment.name if txn.investment else "—",
                    "account_name": txn.account.name if txn.account else "—",
                    "amount": amount,
                    "is_inflow": True,
                    "running_after": running_invested,
                    "running_label": "Invested after",
                }
            )
        else:
            delta = _linked_account_delta(txn, linked_account_id)
            if delta == 0:
                continue
            running_cash += delta
            rows.append(
                {
                    "kind": "cash",
                    "txn": txn,
                    "date": txn.date,
                    "description": txn.description,
                    "holding_name": "—",
                    "account_name": _cash_account_label(txn, linked_account_id),
                    "amount": abs(delta),
                    "is_inflow": delta > 0,
                    "running_after": running_cash,
                    "running_label": "Cash after",
                }
            )
    return rows


def _linked_account_delta(txn: Transaction, linked_account_id: int | None) -> Decimal:
    if not linked_account_id:
        return Decimal("0")
    amount = Decimal(txn.amount or 0)
    if txn.transaction_type in ("income", "refund"):
        return amount if txn.account_id == linked_account_id else Decimal("0")
    if txn.transaction_type in ("expense", "investment"):
        return -amount if txn.account_id == linked_account_id else Decimal("0")
    if txn.transaction_type == "transfer":
        if txn.to_account_id == linked_account_id:
            return amount
        if txn.account_id == linked_account_id:
            return -amount
    return Decimal("0")


def _cash_account_label(txn: Transaction, linked_account_id: int | None) -> str:
    if txn.transaction_type == "transfer":
        src = txn.account.name if txn.account else "—"
        dst = txn.to_account.name if txn.to_account else "—"
        if txn.to_account_id == linked_account_id:
            return f"{src} → {dst}"
        return f"{src} → {dst}"
    return txn.account.name if txn.account else "—"


def create_goal(data: dict[str, Any]) -> Goal:
    goal = Goal()
    _populate(goal, data, is_new=True)
    db.session.add(goal)
    db.session.commit()
    return goal


def update_goal(goal: Goal, data: dict[str, Any]) -> Goal:
    _populate(goal, data, is_new=False)
    db.session.commit()
    return goal


def delete_goal(goal: Goal) -> None:
    # Unlink investments first
    for inv in goal.investments:
        inv.goal_id = None
    db.session.delete(goal)
    db.session.commit()


def _populate(goal: Goal, data: dict[str, Any], *, is_new: bool) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise GoalValidationError("Name is required.")

    existing = Goal.query.filter(Goal.name == name).first()
    if existing and (is_new or existing.id != goal.id):
        raise GoalValidationError("A goal with this name already exists.")

    goal_type = (data.get("goal_type") or "custom").strip().lower()
    if goal_type not in Goal.GOAL_TYPES:
        raise GoalValidationError("Invalid goal type.")

    target = _nonneg_amount(data.get("target_amount"), field="Target amount")
    current = _nonneg_amount(data.get("current_amount"), field="Current amount")
    contrib = _nonneg_amount(
        data.get("monthly_contribution"), field="Monthly contribution"
    )

    owner = (data.get("owner") or "joint").lower()
    if owner not in Goal.OWNERS:
        owner = "joint"

    linked_account_id = _optional_int(data.get("linked_account_id"))
    if linked_account_id:
        account = db.session.get(Account, linked_account_id)
        if not account:
            raise GoalValidationError("Linked account is invalid.")

    slug = _slugify(name)
    # Ensure unique slug
    slug_owner = Goal.query.filter(Goal.slug == slug).first()
    if slug_owner and (is_new or slug_owner.id != goal.id):
        slug = f"{slug}-{int(datetime.utcnow().timestamp())}"

    goal.name = name
    if is_new or not goal.slug:
        goal.slug = slug
    goal.goal_type = goal_type
    goal.target_amount = target
    goal.current_amount = current
    goal.monthly_contribution = contrib
    goal.target_date = _parse_date(data.get("target_date"), required=False)
    goal.linked_account_id = linked_account_id
    goal.owner = owner
    goal.icon = (data.get("icon") or "bi-flag").strip() or "bi-flag"
    goal.color = (data.get("color") or "#5eead4").strip() or "#5eead4"
    goal.notes = (data.get("notes") or "").strip() or None
    goal.is_active = str(data.get("is_active", "1")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def _estimate_completion(goal: Goal) -> date | None:
    remaining = goal.remaining
    if remaining <= 0:
        return date.today()
    contrib = Decimal(goal.monthly_contribution or 0)
    if contrib > 0:
        months = max(1, math.ceil(float(remaining / contrib)))
        y, m = date.today().year, date.today().month + months
        while m > 12:
            m -= 12
            y += 1
        day = min(date.today().day, 28)
        return date(y, m, day)
    return goal.target_date


def _nonneg_amount(value, *, field: str) -> Decimal:
    amount = parse_nonneg_amount(value)
    if amount is None:
        raise GoalValidationError(f"{field} must be a valid non-negative number.")
    return amount


def _optional_int(value) -> Optional[int]:
    if value in (None, "", "none"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value, *, required: bool = False) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        if required:
            raise GoalValidationError("Date is required.")
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise GoalValidationError("Invalid date format. Use YYYY-MM-DD.") from exc


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "goal"
