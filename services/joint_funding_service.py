"""Monthly funding — couple: Person1/Person2 → Joint; solo: Salary → Expenses."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import Any

from sqlalchemy import extract
from sqlalchemy.orm import joinedload

from extensions import db
from models import Account, Envelope, JointFundingPlan, JointFundingSplit, Transaction
from services import transaction_service
from utils.helpers import parse_nonneg_amount


class JointFundingValidationError(ValueError):
    pass


OWNER_SELF = "self"
OWNER_WIFE = "wife"


def _owner_labels() -> dict[str, str]:
    from services import profile_service
    return profile_service.get_owner_labels()


def _is_couple() -> bool:
    from services import profile_service
    return profile_service.is_couple_mode()


def funding_ui() -> dict[str, str]:
    """Mode-aware labels for checklist, settings, and flash messages."""
    dest = resolve_joint_account()
    dest_name = dest.name if dest else ("Joint Account" if _is_couple() else "Expenses")
    if _is_couple():
        return {
            "title": "Fund Joint",
            "plan_title": "Joint Funding Plan",
            "section_title": "Joint Funding",
            "short": "Joint",
            "destination_name": dest_name,
            "description_prefix": "Joint funding",
            "blurb": "Move household cash + label envelopes",
            "setup_detail": "Set contribution amounts and envelope plan in Settings.",
            "contrib_hint": "Who pays into Joint · one shared envelope plan",
        }
    return {
        "title": "Fund Expenses",
        "plan_title": "Expenses Funding Plan",
        "section_title": "Expenses Funding",
        "short": "Expenses",
        "destination_name": dest_name,
        "description_prefix": "Expenses funding",
        "blurb": "Move monthly cash from salary → expenses + label envelopes",
        "setup_detail": "Set the monthly salary → expenses transfer and envelope plan.",
        "contrib_hint": "Monthly transfer from salary into Expenses · one shared envelope plan",
    }


def get_plan() -> JointFundingPlan | None:
    return (
        JointFundingPlan.query.options(
            joinedload(JointFundingPlan.splits).joinedload(JointFundingSplit.envelope)
        )
        .order_by(JointFundingPlan.id)
        .first()
    )


def get_or_init_plan() -> JointFundingPlan:
    """Return existing plan, or an unsaved default with account prefills."""
    plan = get_plan()
    if plan:
        return plan
    plan = JointFundingPlan(
        self_account_id=_default_source_account_id(OWNER_SELF),
        wife_account_id=_default_source_account_id(OWNER_WIFE) if _is_couple() else None,
        self_amount=Decimal("0"),
        wife_amount=Decimal("0"),
        day_of_month=1,
        is_active=True,
    )
    return plan


def resolve_joint_account() -> Account | None:
    """Destination pool: Joint (couple) or Expenses (solo)."""
    from services import envelope_service

    return envelope_service.resolve_envelope_cash_account()


def save_plan(data: dict[str, Any]) -> JointFundingPlan:
    plan = get_plan()
    is_new = plan is None
    if is_new:
        plan = JointFundingPlan()

    couple = _is_couple()
    ui = funding_ui()
    self_amount = parse_nonneg_amount(data.get("self_amount"))
    wife_amount = parse_nonneg_amount(data.get("wife_amount") if couple else "0")
    if self_amount is None or wife_amount is None:
        raise JointFundingValidationError("Contribution amounts must be non-negative.")

    self_account_id = _optional_int(data.get("self_account_id"))
    wife_account_id = _optional_int(data.get("wife_account_id")) if couple else None
    if self_amount > 0 and not self_account_id:
        labels = _owner_labels()
        raise JointFundingValidationError(f"Choose {labels.get('self', 'Person 1')}'s account.")
    if couple and wife_amount > 0 and not wife_account_id:
        labels = _owner_labels()
        raise JointFundingValidationError(f"Choose {labels.get('wife', 'Person 2')}'s account.")
    if self_account_id and not _active_account(self_account_id):
        labels = _owner_labels()
        raise JointFundingValidationError(f"{labels.get('self', 'Person 1')} account not found.")
    if wife_account_id and not _active_account(wife_account_id):
        labels = _owner_labels()
        raise JointFundingValidationError(f"{labels.get('wife', 'Person 2')} account not found.")
    dest = resolve_joint_account()
    if self_account_id and dest and self_account_id == dest.id and self_amount > 0:
        raise JointFundingValidationError(
            f"Source account must be different from {ui['destination_name']}."
        )
    if (
        couple
        and self_account_id
        and wife_account_id
        and self_account_id == wife_account_id
        and (self_amount > 0 or wife_amount > 0)
    ):
        labels = _owner_labels()
        raise JointFundingValidationError(
            f"{labels.get('self', 'Person 1')} and {labels.get('wife', 'Person 2')} must use different accounts."
        )

    day = 1
    try:
        day = int(data.get("day_of_month") or 1)
    except (TypeError, ValueError) as exc:
        raise JointFundingValidationError("Day of month must be 1–28.") from exc
    if day < 1 or day > 28:
        raise JointFundingValidationError("Day of month must be 1–28.")

    splits = _parse_split_rows(data)
    total = self_amount + (wife_amount if couple else Decimal("0"))
    splits = _with_unallocated_remainder(splits, total)

    plan.self_account_id = self_account_id
    plan.wife_account_id = wife_account_id if couple else None
    plan.self_amount = self_amount
    plan.wife_amount = wife_amount if couple else Decimal("0")
    plan.day_of_month = day
    plan.notes = (data.get("notes") or "").strip() or None
    plan.is_active = str(data.get("is_active") or "").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )

    if is_new:
        db.session.add(plan)
        db.session.flush()
    else:
        for existing in list(plan.splits):
            db.session.delete(existing)
        db.session.flush()

    for index, (envelope_id, amount) in enumerate(splits):
        db.session.add(
            JointFundingSplit(
                plan_id=plan.id,
                envelope_id=envelope_id,
                amount=amount,
                sort_order=index,
            )
        )

    db.session.commit()
    return get_plan() or plan


def get_month_status(
    year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    today = date.today()
    year = year or today.year
    month = month or today.month
    plan = get_plan()
    label = date(year, month, 1).strftime("%B %Y")

    if not plan or not plan.is_active:
        return {
            "year": year,
            "month": month,
            "label": label,
            "plan": plan,
            "plan_configured": False,
            "ready_count": 0,
            "posted_count": 0,
            "ready_total": Decimal("0"),
            "posted_total": Decimal("0"),
            "rows": [],
            "joint_account": resolve_joint_account(),
            "ui": funding_ui(),
        }

    joint = resolve_joint_account()
    rows = []
    ready = 0
    posted = 0
    ready_total = Decimal("0")
    posted_total = Decimal("0")
    contribs = [
        (OWNER_SELF, Decimal(plan.self_amount or 0), plan.self_account_id),
    ]
    if _is_couple():
        contribs.append(
            (OWNER_WIFE, Decimal(plan.wife_amount or 0), plan.wife_account_id)
        )

    for owner, amount, account_id in contribs:
        already = posted_for_month(owner, year, month, account_id=account_id)
        if already:
            status = "posted"
            posted += 1
            posted_total += amount
        elif amount <= 0 or not account_id or not joint:
            status = "skipped"
        else:
            status = "ready"
            ready += 1
            ready_total += amount
        rows.append(
            {
                "owner": owner,
                "label": _owner_labels()[owner],
                "amount": amount,
                "account_id": account_id,
                "status": status,
                "description": description_for(owner, year, month),
                "post_date": _post_date(plan, year, month),
            }
        )

    return {
        "year": year,
        "month": month,
        "label": label,
        "plan": plan,
        "plan_configured": True,
        "ready_count": ready,
        "posted_count": posted,
        "ready_total": ready_total,
        "posted_total": posted_total,
        "rows": rows,
        "joint_account": joint,
        "ui": funding_ui(),
        "split_total": sum(
            (Decimal(s.amount or 0) for s in plan.splits), Decimal("0")
        ),
    }


def description_for(owner: str, year: int, month: int) -> str:
    label = _owner_labels().get(owner, owner)
    month_label = date(year, month, 1).strftime("%b %Y")
    return f"{funding_ui()['description_prefix']} · {label} · {month_label}"


def posted_for_month(
    owner: str,
    year: int,
    month: int,
    *,
    account_id: int | None = None,
) -> bool:
    desc = description_for(owner, year, month)
    query = Transaction.query.filter(
        Transaction.transaction_type == "transfer",
        Transaction.description == desc,
        extract("year", Transaction.date) == year,
        extract("month", Transaction.date) == month,
    )
    if account_id:
        query = query.filter(Transaction.account_id == account_id)
    return query.first() is not None


def post_month(
    year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    status = get_month_status(year, month)
    plan = status["plan"]
    joint = status["joint_account"]
    ui = funding_ui()
    if not plan or not plan.is_active:
        raise JointFundingValidationError(
            f"Set up an {ui['section_title']} plan in Settings first."
        )
    if not joint:
        raise JointFundingValidationError(f"{ui['destination_name']} account not found.")

    total = Decimal(plan.self_amount or 0)
    if _is_couple():
        total += Decimal(plan.wife_amount or 0)
    plan_splits = effective_plan_splits(plan)
    if status["ready_count"] == 0:
        return {
            "created_count": 0,
            "skipped_count": len(status["rows"]),
            "created": [],
            "label": status["label"],
            "total": Decimal("0"),
            "ui": ui,
        }
    if total <= 0:
        raise JointFundingValidationError("Plan amounts are zero — nothing to post.")
    if not plan_splits:
        raise JointFundingValidationError(
            "Could not build envelope split — is the Unallocated envelope missing?"
        )

    created = []
    skipped = []
    for row in status["rows"]:
        if row["status"] != "ready":
            skipped.append(row["label"])
            continue
        person_amount = Decimal(row["amount"])
        person_splits = _prorate_splits(plan_splits, person_amount, total)
        payload: dict[str, Any] = {
            "date": row["post_date"].isoformat(),
            "amount": str(person_amount),
            "description": row["description"],
            "transaction_type": "transfer",
            "account_id": row["account_id"],
            "to_account_id": joint.id,
            "paid_by": row["owner"],
            "payment_mode": "netbanking",
            "need_want": "n/a",
            "notes": plan.notes or f"Posted from {ui['section_title']} plan",
            "split_envelope_id": [eid for eid, _ in person_splits],
            "split_amount": [str(amt) for _, amt in person_splits],
        }
        txn, _ = transaction_service.create_transaction(payload)
        created.append(txn)

    return {
        "created_count": len(created),
        "skipped_count": len(skipped),
        "created": created,
        "label": status["label"],
        "total": sum((Decimal(t.amount or 0) for t in created), Decimal("0")),
        "ui": ui,
    }


def _prorate_splits(
    plan_splits: list[tuple[int, Decimal]],
    person_amount: Decimal,
    total_amount: Decimal,
) -> list[tuple[int, Decimal]]:
    """Allocate plan envelope lines to one contribution; cents via largest remainder."""
    if person_amount <= 0 or total_amount <= 0 or not plan_splits:
        return []

    exact = []
    for eid, plan_amt in plan_splits:
        share = (plan_amt * person_amount) / total_amount
        exact.append((eid, share))

    floored: list[tuple[int, Decimal, Decimal]] = []
    for eid, share in exact:
        base = share.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        frac = share - base
        floored.append((eid, base, frac))

    assigned = sum((b for _, b, _ in floored), Decimal("0"))
    remainder_cents = int(((person_amount - assigned) * 100).to_integral_value())
    floored.sort(key=lambda row: row[2], reverse=True)

    amounts = {eid: base for eid, base, _ in floored}
    order = [eid for eid, _, _ in floored]
    for i in range(max(remainder_cents, 0)):
        amounts[order[i % len(order)]] += Decimal("0.01")

    result = [(eid, amounts[eid]) for eid in order if amounts[eid] > 0]
    # Keep original plan order for readability
    plan_order = {eid: i for i, (eid, _) in enumerate(plan_splits)}
    result.sort(key=lambda row: plan_order.get(row[0], 999))

    # Final safety: force sum == person_amount on last line
    result_sum = sum((a for _, a in result), Decimal("0"))
    if result and result_sum != person_amount:
        eid, amt = result[-1]
        result[-1] = (eid, amt + (person_amount - result_sum))
    return result


def get_unallocated_envelope() -> Envelope | None:
    return Envelope.query.filter_by(slug="unallocated", is_active=True).first()


def editable_split_rows(plan: JointFundingPlan | None) -> list[dict[str, Any]]:
    """Split lines shown in the form — Unallocated is computed, not edited."""
    if not plan or not plan.id:
        return []
    unalloc = get_unallocated_envelope()
    rows = []
    for s in plan.splits or []:
        if unalloc and s.envelope_id == unalloc.id:
            continue
        if Decimal(s.amount or 0) <= 0:
            continue
        rows.append({"envelope_id": s.envelope_id, "amount": str(s.amount)})
    return rows


def effective_plan_splits(plan: JointFundingPlan) -> list[tuple[int, Decimal]]:
    """Stored splits with Unallocated remainder filled to match contribution total."""
    total = Decimal(plan.self_amount or 0)
    if _is_couple():
        total += Decimal(plan.wife_amount or 0)
    raw = [
        (s.envelope_id, Decimal(s.amount or 0))
        for s in (plan.splits or [])
        if Decimal(s.amount or 0) > 0
    ]
    return _with_unallocated_remainder(raw, total)


def _with_unallocated_remainder(
    splits: list[tuple[int, Decimal]], total: Decimal
) -> list[tuple[int, Decimal]]:
    """
    One shared envelope plan: named pots + leftover → Unallocated.

    Example: total 1,00,000 · Essentials 50k · Shopping 15k · Travel 15k
    → Unallocated auto 20k. Contributions (30k / 70k) are separate.
    """
    unalloc = get_unallocated_envelope()
    unalloc_id = unalloc.id if unalloc else None
    ui = funding_ui()

    cleaned: list[tuple[int, Decimal]] = []
    seen: set[int] = set()
    for eid, amt in splits:
        if unalloc_id and eid == unalloc_id:
            continue  # remainder is always computed
        if eid in seen:
            raise JointFundingValidationError("Each envelope can only appear once.")
        if amt <= 0:
            continue
        seen.add(eid)
        cleaned.append((eid, amt))

    named_sum = sum((amt for _, amt in cleaned), Decimal("0"))
    if total < 0:
        raise JointFundingValidationError("Contribution total is invalid.")
    if named_sum > total:
        labels = _owner_labels()
        p1 = labels.get("self", "Person 1")
        if _is_couple():
            p2 = labels.get("wife", "Person 2")
            raise_hint = f"Lower the envelope lines or raise {p1} + {p2} contributions."
        else:
            raise_hint = f"Lower the envelope lines or raise the {ui['short']} transfer amount."
        raise JointFundingValidationError(
            f"Envelope amounts ({named_sum}) exceed {ui['short']} total ({total}). "
            + raise_hint
        )

    remainder = total - named_sum
    if remainder > 0:
        if not unalloc:
            raise JointFundingValidationError(
                "Unallocated envelope is missing — needed for the leftover amount."
            )
        cleaned.append((unalloc.id, remainder))
    return cleaned


def _parse_split_rows(data: dict[str, Any]) -> list[tuple[int, Decimal]]:
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
    seen: set[int] = set()
    for raw_id, raw_amt in zip(env_ids, amounts):
        if str(raw_id or "").strip() == "" and str(raw_amt or "").strip() == "":
            continue
        try:
            eid = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise JointFundingValidationError("Invalid envelope in split.") from exc
        if eid in seen:
            raise JointFundingValidationError("Each envelope can only appear once.")
        amount = parse_nonneg_amount(raw_amt)
        if amount is None:
            raise JointFundingValidationError("Split amounts must be valid numbers.")
        if amount == 0:
            continue
        env = db.session.get(Envelope, eid)
        if not env or not env.is_active:
            raise JointFundingValidationError("One of the split envelopes is invalid.")
        seen.add(eid)
        splits.append((eid, amount))
    return splits


def _default_source_account_id(owner: str) -> int | None:
    """Prefer a salary/bank source — never the Expenses destination in solo."""
    accounts = (
        Account.query.filter_by(owner=owner, is_active=True)
        .filter(Account.account_type.in_(("bank", "salary", "cash")))
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    dest = resolve_joint_account()
    dest_id = dest.id if dest else None
    candidates = [a for a in accounts if a.id != dest_id]
    if not candidates:
        return None
    for acc in candidates:
        if "salary" in (acc.name or "").lower() or acc.account_type == "salary":
            return acc.id
    return candidates[0].id


def _active_account(account_id: int) -> Account | None:
    return Account.query.filter_by(id=account_id, is_active=True).first()


def _optional_int(value: Any) -> int | None:
    if value in (None, "", "0"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _post_date(plan: JointFundingPlan, year: int, month: int) -> date:
    last = monthrange(year, month)[1]
    day = min(int(plan.day_of_month or 1), last, 28)
    return date(year, month, day)
