"""Virtual envelope service — allocations, spends, overview."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from extensions import db
from models import Account, Category, Envelope, EnvelopeEntry, Transaction
from utils.helpers import parse_amount, parse_nonneg_amount


class EnvelopeValidationError(ValueError):
    pass


def reallocate(
    *,
    from_envelope_id: int,
    to_envelope_id: int,
    amount: Any,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Move purpose labels between Joint pots — no bank transfer.
    Use when Fund Joint already posted but the split needs a correction.
    """
    amount_dec = parse_nonneg_amount(amount)
    if amount_dec is None or amount_dec <= 0:
        raise EnvelopeValidationError("Enter an amount greater than zero.")
    if from_envelope_id == to_envelope_id:
        raise EnvelopeValidationError("Choose two different envelopes.")

    source = get_envelope(from_envelope_id)
    target = get_envelope(to_envelope_id)
    if not source or not source.is_active:
        raise EnvelopeValidationError("Source envelope is invalid.")
    if not target or not target.is_active:
        raise EnvelopeValidationError("Destination envelope is invalid.")
    available = Decimal(source.current_balance or 0)
    if available < amount_dec:
        raise EnvelopeValidationError(
            f"{source.name} only has ₹{available:,.2f} left to move."
        )

    label = (notes or "").strip() or f"Reallocate {source.name} → {target.name}"
    out_entry = EnvelopeEntry(
        envelope_id=source.id,
        transaction_id=None,
        entry_type="reallocation_out",
        amount=amount_dec,
        notes=label,
    )
    in_entry = EnvelopeEntry(
        envelope_id=target.id,
        transaction_id=None,
        entry_type="reallocation_in",
        amount=amount_dec,
        notes=label,
    )
    db.session.add(out_entry)
    db.session.add(in_entry)
    db.session.flush()
    _apply_entry_to_balance(out_entry)
    _apply_entry_to_balance(in_entry)
    db.session.commit()
    return {
        "from": source,
        "to": target,
        "amount": amount_dec,
        "notes": label,
    }


def list_envelopes(*, active_only: bool = True) -> list[Envelope]:
    query = Envelope.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Envelope.sort_order, Envelope.name).all()


def get_envelope(envelope_id: int) -> Optional[Envelope]:
    return db.session.get(Envelope, envelope_id)


def get_envelope_ledger(
    envelope_id: int,
    *,
    year: int | None = None,
    month: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """
    Entries for one envelope, newest first, with linked transactions.

    Optional year/month filters to the selected calendar month.
    """
    envelope = get_envelope(envelope_id)
    if not envelope:
        return {"envelope": None, "rows": [], "totals": {}}

    today = date.today()
    year = year or today.year
    month = month or today.month
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])

    q = (
        db.session.query(EnvelopeEntry, Transaction)
        .outerjoin(Transaction, EnvelopeEntry.transaction_id == Transaction.id)
        .filter(EnvelopeEntry.envelope_id == envelope_id)
    )
    rows_raw = q.order_by(EnvelopeEntry.id.desc()).all()

    rows: list[dict[str, Any]] = []
    allocated = Decimal("0")
    spent = Decimal("0")
    matched: list[dict[str, Any]] = []
    for entry, txn in rows_raw:
        eff = _entry_effective_date(entry, txn.date if txn else None)
        if not (start <= eff <= end):
            continue
        amount = Decimal(entry.amount or 0)
        if entry.entry_type == "spend":
            spent += amount
            signed = -amount
        elif entry.entry_type == "refund":
            spent -= amount
            signed = amount
        else:
            allocated += amount
            signed = amount
        matched.append(
            {
                "entry": entry,
                "transaction": txn,
                "date": eff,
                "entry_type": entry.entry_type,
                "amount": amount,
                "signed": signed,
                "description": (
                    txn.description
                    if txn
                    else (entry.notes or entry.entry_type.capitalize())
                ),
            }
        )

    matched.sort(key=lambda r: (r["date"], r["entry"].id), reverse=True)
    truncated = len(matched) > limit
    rows = matched[:limit]

    overview = get_envelopes_overview(year, month)
    row_meta = next(
        (r for r in overview["rows"] if r["envelope"].id == envelope_id), None
    )

    return {
        "envelope": envelope,
        "rows": rows,
        "year": year,
        "month": month,
        "month_label": start.strftime("%B %Y"),
        "totals": {
            "allocated": allocated,
            "spent": spent,
            "net": allocated - spent,
        },
        "meta": row_meta,
        "overview": overview,
        "truncated": truncated,
    }


def _entry_effective_date(entry: EnvelopeEntry, txn_date: date | None) -> date:
    if txn_date:
        return txn_date
    created = entry.created_at
    if isinstance(created, datetime):
        return created.date()
    if isinstance(created, date):
        return created
    return date.today()


def get_envelopes_overview(
    year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    """Joint-account purpose pots with monthly allocated / spent / carry-forward."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])

    envelopes = list_envelopes(active_only=True)
    env_ids = [e.id for e in envelopes]
    total = sum((Decimal(e.current_balance or 0) for e in envelopes), Decimal("0"))
    joint = resolve_envelope_cash_account()
    joint_balance = Decimal(joint.current_balance or 0) if joint else Decimal("0")
    difference = joint_balance - total  # +ve = cash not labelled yet

    # Per-envelope monthly stats from ledger entries
    stats: dict[int, dict[str, Decimal]] = {
        eid: {
            "carry_forward": Decimal("0"),
            "allocated": Decimal("0"),
            "spent": Decimal("0"),
            "lifetime_allocated": Decimal("0"),
            "lifetime_spent": Decimal("0"),
        }
        for eid in env_ids
    }

    if env_ids:
        rows_q = (
            db.session.query(EnvelopeEntry, Transaction.date)
            .outerjoin(Transaction, EnvelopeEntry.transaction_id == Transaction.id)
            .filter(EnvelopeEntry.envelope_id.in_(env_ids))
            .all()
        )
        for entry, txn_date in rows_q:
            bucket = stats.get(entry.envelope_id)
            if not bucket:
                continue
            amount = Decimal(entry.amount or 0)
            eff = _entry_effective_date(entry, txn_date)
            etype = entry.entry_type
            is_spend = etype == "spend"
            is_refund = etype == "refund"
            is_realloc_out = etype == "reallocation_out"
            is_credit = etype in ("allocation", "adjustment", "reallocation_in")

            if is_spend:
                bucket["lifetime_spent"] += amount
            elif is_refund:
                bucket["lifetime_spent"] -= amount
            elif is_credit:
                bucket["lifetime_allocated"] += amount
            # reallocation_out: lifetime nets via balance; not "spent"

            if eff < start:
                if is_spend or is_realloc_out:
                    bucket["carry_forward"] -= amount
                elif is_refund or is_credit:
                    bucket["carry_forward"] += amount
            elif start <= eff <= end:
                if is_spend:
                    bucket["spent"] += amount
                elif is_refund:
                    bucket["spent"] -= amount
                elif is_credit:
                    bucket["allocated"] += amount
                elif is_realloc_out:
                    # Label moved away — reduces what this pot received this month
                    bucket["allocated"] -= amount

    rows = []
    total_allocated = Decimal("0")
    total_spent = Decimal("0")
    total_carry = Decimal("0")

    for env in envelopes:
        bal = Decimal(env.current_balance or 0)
        s = stats[env.id]
        # Prefer ledger carry; if ledger empty but balance exists, treat as carry
        carry = s["carry_forward"]
        if (
            s["lifetime_allocated"] == 0
            and s["lifetime_spent"] == 0
            and bal != 0
        ):
            carry = bal
        allocated = s["allocated"]
        spent = s["spent"]
        available = carry + allocated  # what you could spend this period
        cat_names = [c.name for c in env.categories.filter_by(is_active=True).all()]

        total_allocated += allocated
        total_spent += spent
        total_carry += carry

        rows.append(
            {
                "envelope": env,
                "balance": bal,
                "remaining": bal,
                "carry_forward": carry,
                "allocated": allocated,
                "spent": spent,
                "available": available,
                "lifetime_allocated": s["lifetime_allocated"],
                "lifetime_spent": s["lifetime_spent"],
                "categories": cat_names,
                "pct_of_total": (
                    round(float((bal / total) * 100), 1) if total > 0 else 0.0
                ),
                "pct_of_joint": (
                    round(float((bal / joint_balance) * 100), 1)
                    if joint_balance > 0
                    else 0.0
                ),
                "spend_pct": (
                    round(float((spent / available) * 100), 1)
                    if available > 0
                    else (100.0 if spent > 0 else 0.0)
                ),
            }
        )

    total_available = total_carry + total_allocated
    month_left = total_available - total_spent
    month_util_pct = (
        round(float((total_spent / total_available) * 100), 1)
        if total_available > 0
        else (100.0 if total_spent > 0 else 0.0)
    )

    return {
        "rows": rows,
        "total": total,
        "total_remaining": total,
        "total_allocated": total_allocated,
        "total_spent": total_spent,
        "total_carry_forward": total_carry,
        "total_available": total_available,
        "month_left": month_left,
        "month_util_pct": month_util_pct,
        "count": len(rows),
        "joint_account": joint,
        "joint_balance": joint_balance,
        "difference": difference,
        "is_balanced": difference == 0,
        "year": year,
        "month": month,
        "month_label": start.strftime("%B %Y"),
    }


def resolve_envelope_for_expense(
    *,
    envelope_id: int | None,
    category_id: int | None,
) -> Envelope | None:
    """Explicit envelope wins; else category's default envelope."""
    if envelope_id:
        env = get_envelope(envelope_id)
        if not env or not env.is_active:
            raise EnvelopeValidationError("Selected envelope is invalid.")
        return env
    if category_id:
        category = db.session.get(Category, category_id)
        if category and category.envelope_id:
            return get_envelope(category.envelope_id)
    return None


def parse_transfer_splits(data: Any) -> list[tuple[int, Decimal]]:
    """Parse split_envelope_id[] + split_amount[] from form data."""
    if hasattr(data, "getlist"):
        env_ids = data.getlist("split_envelope_id")
        amounts = data.getlist("split_amount")
    else:
        env_ids = data.get("split_envelope_id") or []
        amounts = data.get("split_amount") or []
        if not isinstance(env_ids, list):
            env_ids = [env_ids] if env_ids else []
        if not isinstance(amounts, list):
            amounts = [amounts] if amounts else []

    splits: list[tuple[int, Decimal]] = []
    for raw_id, raw_amt in zip(env_ids, amounts):
        if raw_id in (None, "", "none") and str(raw_amt or "").strip() in ("",):
            continue
        try:
            eid = int(raw_id)
        except (TypeError, ValueError):
            raise EnvelopeValidationError("Invalid envelope in split.") from None
        amount = parse_amount(raw_amt)
        if amount is None:
            # skip blank rows
            if str(raw_amt or "").strip() in ("", "0", "0.0", "0.00"):
                continue
            raise EnvelopeValidationError("Split amounts must be greater than zero.")
        splits.append((eid, amount))
    return splits


def get_essentials_envelope() -> Envelope | None:
    return Envelope.query.filter_by(slug="essentials", is_active=True).first()


def default_essentials_split(amount: Decimal) -> list[tuple[int, Decimal]]:
    """Full transfer amount → Essentials when no split was provided."""
    env = get_essentials_envelope()
    if not env:
        raise EnvelopeValidationError(
            "Essentials envelope is missing — cannot auto-allocate transfer."
        )
    return [(env.id, amount)]


def resolve_envelope_cash_account() -> Account | None:
    """Account whose cash envelopes label — Joint (couple) or Expenses (solo)."""
    from services import profile_service

    if profile_service.is_couple_mode():
        return (
            Account.query.filter_by(name="Joint Account", is_active=True).first()
            or Account.query.filter_by(account_type="joint", is_active=True).first()
            or Account.query.filter_by(owner="joint", is_active=True).first()
        )

    # Solo: "{Name} Expenses"
    expenses = (
        Account.query.filter(
            Account.owner == "self",
            Account.is_active.is_(True),
        )
        .order_by(Account.sort_order, Account.id)
        .all()
    )
    for acc in expenses:
        if "expense" in (acc.name or "").lower():
            return acc
    return None


def is_joint_account(account: Account | None) -> bool:
    """True if envelopes apply to spends/transfers for this account (Joint or solo Expenses)."""
    if not account:
        return False
    if (account.owner or "").lower() == "joint" or account.account_type == "joint":
        return True
    if account.name == "Joint Account":
        return True
    from services import profile_service

    if not profile_service.is_couple_mode():
        return (
            (account.owner or "").lower() == "self"
            and "expense" in (account.name or "").lower()
        )
    return False


def validate_splits_against_total(
    splits: list[tuple[int, Decimal]], total: Decimal
) -> None:
    if not splits:
        return
    split_sum = sum((a for _, a in splits), Decimal("0"))
    if split_sum != total:
        raise EnvelopeValidationError(
            f"Split total ({split_sum}) must equal transfer amount ({total})."
        )
    seen: set[int] = set()
    for eid, _ in splits:
        if eid in seen:
            raise EnvelopeValidationError("Each envelope can only appear once in a split.")
        seen.add(eid)
        env = get_envelope(eid)
        if not env or not env.is_active:
            raise EnvelopeValidationError("One of the split envelopes is invalid.")


def apply_envelope_entries_for_transaction(
    txn: Transaction,
    *,
    splits: list[tuple[int, Decimal]] | None = None,
    expense_envelope: Envelope | None = None,
) -> None:
    """Create envelope ledger lines for a newly applied transaction."""
    if txn.transaction_type == "transfer" and splits:
        for eid, amount in splits:
            _credit(eid, amount, txn, entry_type="allocation")
    elif txn.transaction_type == "expense" and expense_envelope:
        _debit(expense_envelope.id, Decimal(txn.amount or 0), txn, entry_type="spend")
        txn.envelope_id = expense_envelope.id
    elif txn.transaction_type == "refund" and expense_envelope:
        # Restore the pot that the original expense drained
        _credit(
            expense_envelope.id,
            Decimal(txn.amount or 0),
            txn,
            entry_type="refund",
        )
        txn.envelope_id = expense_envelope.id


def expense_envelope_warning(
    expense_envelope: Envelope | None, amount: Decimal | None
) -> str | None:
    """
    Soft warning when spending more than the envelope has left.
    Real cash still leaves the bank account; envelopes are purpose labels
    and may go negative (overspent) so Spent totals stay accurate.
    """
    if not expense_envelope or amount is None:
        return None
    available = Decimal(expense_envelope.current_balance or 0)
    if available >= amount:
        return None
    short = amount - available
    return (
        f"{expense_envelope.name} only had ₹{available:,.2f} left — "
        f"this expense overspent it by ₹{short:,.2f}. "
        f"Allocate more into that envelope on the next Joint transfer "
        f"(or choose a different envelope)."
    )


def category_envelope_mismatch_warning(
    *,
    category_id: int | None,
    envelope_id: int | None,
) -> str | None:
    """
    Soft warning when an explicit envelope differs from the category default.
    Prevents Budget (category) vs Essentials (pot) haircut-style mismatches.
    """
    if not category_id or not envelope_id:
        return None
    category = db.session.get(Category, category_id)
    if not category or not category.envelope_id:
        return None
    if int(category.envelope_id) == int(envelope_id):
        return None
    chosen = get_envelope(int(envelope_id))
    default = get_envelope(int(category.envelope_id))
    if not chosen or not default:
        return None
    return (
        f"“{category.name}” normally uses {default.name}, but this expense "
        f"was booked to {chosen.name}. Budget tracks the category; the pot "
        f"tracks {chosen.name} — totals may diverge."
    )


def merge_warnings(*parts: str | None) -> str | None:
    messages = [p.strip() for p in parts if p and str(p).strip()]
    if not messages:
        return None
    return " ".join(messages)


def reverse_envelope_entries_for_transaction(txn: Transaction) -> None:
    """Undo all envelope effects tied to this transaction."""
    entries = list(txn.envelope_entries.all())
    for entry in entries:
        _apply_entry_to_balance(entry, reverse=True)
        db.session.delete(entry)
    txn.envelope_id = None


def backfill_missing_refund_envelope_entries() -> int:
    """
    Credit pots for past refunds that updated cash but never wrote envelope lines.
    Safe to re-run: skips refunds that already have envelope entries.
    """
    from models import Transaction

    fixed = 0
    refunds = (
        Transaction.query.filter_by(transaction_type="refund")
        .order_by(Transaction.id.asc())
        .all()
    )
    for txn in refunds:
        if txn.envelope_entries.count() > 0:
            continue
        account = db.session.get(Account, txn.account_id)
        if not is_joint_account(account):
            continue
        expense_env = resolve_envelope_for_expense(
            envelope_id=txn.envelope_id,
            category_id=txn.category_id,
        )
        if not expense_env:
            expense_env = get_essentials_envelope()
        if not expense_env:
            continue
        apply_envelope_entries_for_transaction(txn, expense_envelope=expense_env)
        fixed += 1
    if fixed:
        db.session.commit()
    return fixed


def _credit(
    envelope_id: int,
    amount: Decimal,
    txn: Transaction | None,
    *,
    entry_type: str,
    notes: str | None = None,
) -> None:
    entry = EnvelopeEntry(
        envelope_id=envelope_id,
        transaction_id=txn.id if txn else None,
        entry_type=entry_type,
        amount=amount,
        notes=notes,
    )
    db.session.add(entry)
    db.session.flush()
    _apply_entry_to_balance(entry)


def _debit(
    envelope_id: int,
    amount: Decimal,
    txn: Transaction | None,
    *,
    entry_type: str,
    notes: str | None = None,
) -> None:
    _credit(envelope_id, amount, txn, entry_type=entry_type, notes=notes)


def _apply_entry_to_balance(entry: EnvelopeEntry, reverse: bool = False) -> None:
    envelope = db.session.get(Envelope, entry.envelope_id)
    if not envelope:
        raise EnvelopeValidationError("Envelope not found for entry.")
    delta = entry.signed_amount
    if reverse:
        delta = -delta
    envelope.current_balance = Decimal(envelope.current_balance or 0) + delta


def investments_by_purpose() -> list[dict[str, Any]]:
    """Aggregate investment current value by linked goal purpose."""
    from models import Goal, Investment
    from sqlalchemy import func

    rows = (
        db.session.query(
            Goal.id,
            Goal.name,
            Goal.goal_type,
            Goal.color,
            func.coalesce(func.sum(Investment.current_value), 0),
            func.count(Investment.id),
        )
        .join(Investment, Investment.goal_id == Goal.id)
        .filter(Investment.is_active.is_(True), Goal.is_active.is_(True))
        .group_by(Goal.id)
        .order_by(func.sum(Investment.current_value).desc())
        .all()
    )
    untagged = (
        db.session.query(func.coalesce(func.sum(Investment.current_value), 0))
        .filter(Investment.is_active.is_(True), Investment.goal_id.is_(None))
        .scalar()
    )
    result = [
        {
            "goal_id": gid,
            "name": name,
            "goal_type": gtype,
            "color": color or "#64748b",
            "current": float(total or 0),
            "count": count,
        }
        for gid, name, gtype, color, total, count in rows
    ]
    if untagged and Decimal(untagged or 0) > 0:
        result.append(
            {
                "goal_id": None,
                "name": "Untagged",
                "goal_type": "custom",
                "color": "#64748b",
                "current": float(untagged or 0),
                "count": 0,
            }
        )
    return result
