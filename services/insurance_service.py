"""Insurance policy tracking — cover, premiums, renewals."""

from __future__ import annotations

import calendar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import extract

from extensions import db
from models import Account, Category, Insurance, Transaction
from utils.helpers import parse_nonneg_amount


class InsuranceValidationError(ValueError):
    pass


POLICY_LABELS = {
    "health": "Health",
    "term": "Term life",
    "life": "Life",
    "vehicle": "Vehicle",
    "home": "Home",
    "other": "Other",
}

FREQUENCY_LABELS = {
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "yearly": "Yearly",
    "one_time": "One-time (multi-year prepaid)",
}


def list_policies(*, active_only: bool = True) -> list[Insurance]:
    query = Insurance.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Insurance.sort_order, Insurance.name).all()


def get_policy(policy_id: int) -> Optional[Insurance]:
    return db.session.get(Insurance, policy_id)


def _annualized_premium(policy: Insurance) -> float:
    prem = float(policy.premium_amount or 0)
    freq = policy.premium_frequency or "yearly"
    if freq == "monthly":
        return prem * 12
    if freq == "quarterly":
        return prem * 4
    if freq == "yearly":
        return prem
    # one_time — spread over cover years when known (e.g. 3-year health prepaid)
    years = policy.coverage_years
    if years and years > 0:
        return prem / years
    return 0.0


def get_overview() -> dict[str, Any]:
    policies = list_policies(active_only=False)
    active = [p for p in policies if p.is_active]
    total_cover = sum((float(p.cover_amount or 0) for p in active), 0.0)
    annual_premium = sum((_annualized_premium(p) for p in active), 0.0)

    due_soon = [
        p
        for p in active
        if p.renewal_status in ("overdue", "due_soon")
    ]
    return {
        "policies": policies,
        "active": active,
        "count": len(active),
        "total_cover": total_cover,
        "annual_premium": annual_premium,
        "due_soon": due_soon,
        "due_soon_count": len(due_soon),
    }


def create_policy(data: dict[str, Any]) -> Insurance:
    item = Insurance()
    _populate(item, data)
    db.session.add(item)
    db.session.commit()
    return item


def update_policy(item: Insurance, data: dict[str, Any]) -> Insurance:
    _populate(item, data)
    db.session.commit()
    return item


def delete_policy(item: Insurance) -> None:
    db.session.delete(item)
    db.session.commit()


def _optional_int(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _premium_marker(policy: Insurance, year: int, month: int) -> str:
    label = date(year, month, 1).strftime("%b %Y")
    return f"Premium · {policy.name} · {label}"


def premium_posted_for_month(policy: Insurance, year: int, month: int) -> bool:
    marker = _premium_marker(policy, year, month)
    return (
        Transaction.query.filter(
            Transaction.transaction_type == "expense",
            Transaction.description == marker,
            extract("year", Transaction.date) == year,
            extract("month", Transaction.date) == month,
        ).first()
        is not None
    )


def _resolve_premium_account(policy: Insurance) -> Account | None:
    if policy.source_account_id:
        return db.session.get(Account, policy.source_account_id)
    from services import envelope_service

    return envelope_service.resolve_envelope_cash_account()


def _insurance_category() -> Category | None:
    return (
        Category.query.filter_by(is_active=True, parent_id=None, category_type="expense")
        .filter(Category.name == "Insurance")
        .first()
    )


def _is_due_in_month(policy: Insurance, year: int, month: int) -> bool:
    """True when next premium/renewal falls in or before this month."""
    due = policy.next_renewal_date
    if not due:
        return False
    return (due.year, due.month) <= (year, month)


def get_premium_month_status(
    year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    today = date.today()
    year = year or today.year
    month = month or today.month
    policies = list_policies(active_only=True)

    rows = []
    ready = posted = skipped = 0
    total_ready = total_posted = Decimal("0")

    for policy in policies:
        amount = Decimal(policy.premium_amount or 0)
        if amount <= 0:
            continue

        missing: list[str] = []
        account = _resolve_premium_account(policy)
        if not account:
            missing.append("no pay-from account")
        elif not account.is_active:
            missing.append("pay-from account inactive")

        already = premium_posted_for_month(policy, year, month)
        due = _is_due_in_month(policy, year, month)

        if already:
            status = "posted"
            posted += 1
            total_posted += amount
        elif not due:
            status = "idle"
            skipped += 1
        elif missing:
            status = "skipped"
            skipped += 1
        else:
            status = "ready"
            ready += 1
            total_ready += amount

        rows.append(
            {
                "policy": policy,
                "amount": amount,
                "status": status,
                "reasons": missing,
                "account": account,
                "due_date": policy.next_renewal_date,
            }
        )

    plan_count = len(rows)

    return {
        "year": year,
        "month": month,
        "label": date(year, month, 1).strftime("%B %Y"),
        "rows": rows,
        "ready_count": ready,
        "posted_count": posted,
        "skipped_count": skipped,
        "ready_total": total_ready,
        "posted_total": total_posted,
        "plan_count": plan_count,
        "due_count": ready + posted + sum(1 for r in rows if r["status"] == "skipped"),
    }


def _add_months(d: date, months: int) -> date:
    month_idx = d.month - 1 + months
    year = d.year + month_idx // 12
    month = month_idx % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance_next_renewal(policy: Insurance, *, paid_on: date) -> None:
    """Move next due date forward after posting a premium."""
    freq = policy.premium_frequency or "yearly"
    base = policy.next_renewal_date or paid_on

    if freq == "one_time":
        # Multi-year prepaid: next action is cover end (renewal), else clear
        if policy.coverage_end_date and policy.coverage_end_date > paid_on:
            if policy.next_renewal_date and policy.next_renewal_date < policy.coverage_end_date:
                policy.next_renewal_date = policy.coverage_end_date
            else:
                policy.next_renewal_date = None
        else:
            policy.next_renewal_date = None
        return

    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(freq, 12)
    nxt = _add_months(base, months)
    # Catch up if several periods were overdue
    guard = 0
    while nxt <= paid_on and guard < 36:
        nxt = _add_months(nxt, months)
        guard += 1
    policy.next_renewal_date = nxt


def post_month_premiums(
    *, year: int | None = None, month: int | None = None
) -> dict[str, Any]:
    """
    Create Insurance expense transactions for premiums due this month.
    Advances each policy's next_renewal_date. Idempotent per policy/month.
    """
    from services import transaction_service
    from services.transaction_service import TransactionValidationError

    today = date.today()
    year = year or today.year
    month = month or today.month
    status = get_premium_month_status(year, month)
    category = _insurance_category()

    created: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for row in status["rows"]:
        policy: Insurance = row["policy"]
        if row["status"] == "posted":
            skipped.append(f"{policy.name}: already posted")
            continue
        if row["status"] != "ready":
            if row["status"] == "skipped":
                skipped.append(
                    f"{policy.name}: {', '.join(row['reasons']) or 'not ready'}"
                )
            continue

        account: Account = row["account"]
        amount = row["amount"]
        due = row["due_date"] or today
        # Keep txn in the posting month
        post_day = min(due.day, calendar.monthrange(year, month)[1])
        post_date = date(year, month, post_day)
        if post_date > today:
            post_date = today

        paid_by = policy.owner if policy.owner in ("self", "wife", "joint") else "self"
        marker = _premium_marker(policy, year, month)
        payload = {
            "date": post_date.isoformat(),
            "amount": str(amount),
            "description": marker,
            "transaction_type": "expense",
            "account_id": str(account.id),
            "paid_by": paid_by,
            "payment_mode": "auto_debit",
            "need_want": "need",
        }
        if category:
            payload["category_id"] = str(category.id)
            if category.envelope_id:
                payload["envelope_id"] = str(category.envelope_id)

        try:
            txn, warning = transaction_service.create_transaction(payload)
            advance_next_renewal(policy, paid_on=post_date)
            db.session.commit()
            created.append(policy.name)
            if warning:
                errors.append(f"{policy.name}: {warning}")
        except (TransactionValidationError, InsuranceValidationError) as exc:
            db.session.rollback()
            errors.append(f"{policy.name}: {exc}")
        except Exception as exc:
            db.session.rollback()
            errors.append(f"{policy.name}: {exc}")

    return {
        "year": year,
        "month": month,
        "label": status["label"],
        "created": created,
        "created_count": len(created),
        "skipped": skipped,
        "errors": errors,
    }


def _parse_optional_date(raw: Any, *, label: str):
    text = (raw or "").strip() if raw is not None else ""
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise InsuranceValidationError(f"Invalid {label}.") from exc


def _parse_optional_years(raw: Any, *, label: str, required: bool = False) -> int | None:
    text = str(raw or "").strip()
    if not text:
        if required:
            raise InsuranceValidationError(f"{label} is required.")
        return None
    try:
        years = int(text)
    except (TypeError, ValueError) as exc:
        raise InsuranceValidationError(f"Enter a valid {label.lower()}.") from exc
    if years <= 0:
        raise InsuranceValidationError(f"{label} must be at least 1 year.")
    if years > 100:
        raise InsuranceValidationError(f"{label} looks too large.")
    return years


def _populate(item: Insurance, data: dict[str, Any]) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise InsuranceValidationError("Policy name is required.")

    policy_type = (data.get("policy_type") or "health").strip().lower()
    if policy_type not in Insurance.POLICY_TYPES:
        raise InsuranceValidationError("Invalid policy type.")

    cover = parse_nonneg_amount(data.get("cover_amount"))
    if cover is None:
        raise InsuranceValidationError("Enter a valid cover amount.")

    premium = parse_nonneg_amount(data.get("premium_amount"))
    if premium is None:
        raise InsuranceValidationError("Enter a valid premium amount.")

    freq = (data.get("premium_frequency") or "yearly").strip().lower()
    if freq not in Insurance.PREMIUM_FREQUENCIES:
        raise InsuranceValidationError("Invalid premium frequency.")

    owner = (data.get("owner") or "joint").lower()
    if owner not in Insurance.OWNERS:
        owner = "joint"

    renewal = _parse_optional_date(data.get("next_renewal_date"), label="next due date")
    coverage_start = _parse_optional_date(
        data.get("coverage_start_date"), label="coverage start date"
    )
    coverage_end = _parse_optional_date(
        data.get("coverage_end_date"), label="coverage end date"
    )

    if coverage_start and coverage_end and coverage_end <= coverage_start:
        raise InsuranceValidationError(
            "Coverage end date must be after the start date."
        )

    # Health multi-year: require both dates when paying one-time
    if policy_type == "health" and freq == "one_time":
        if not coverage_start or not coverage_end:
            raise InsuranceValidationError(
                "Health one-time / multi-year policies need coverage start and end dates "
                "(e.g. 3 years of cover)."
            )

    # If cover end is set and no next due date, treat end as the renewal action date
    if coverage_end and not renewal:
        renewal = coverage_end

    ppt_required = policy_type == "term"
    premium_paying_term_years = _parse_optional_years(
        data.get("premium_paying_term_years"),
        label="Premium paying term",
        required=ppt_required,
    )
    policy_term_years = _parse_optional_years(
        data.get("policy_term_years"),
        label="Policy term",
        required=False,
    )
    if (
        premium_paying_term_years
        and policy_term_years
        and premium_paying_term_years > policy_term_years
    ):
        raise InsuranceValidationError(
            "Premium paying term cannot be longer than the policy term."
        )

    try:
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0

    # Type-specific cleanup
    if policy_type not in ("term", "life"):
        premium_paying_term_years = None
        policy_term_years = None
    if policy_type != "health":
        coverage_start = None
        coverage_end = None

    source_account_id = _optional_int(data.get("source_account_id"))
    if source_account_id is not None:
        account = db.session.get(Account, source_account_id)
        if not account or not account.is_active:
            raise InsuranceValidationError("Pay-from account is invalid.")

    item.name = name
    item.policy_type = policy_type
    item.insurer = (data.get("insurer") or "").strip() or None
    item.policy_number = (data.get("policy_number") or "").strip() or None
    item.cover_amount = cover
    item.premium_amount = premium
    item.premium_frequency = freq
    item.next_renewal_date = renewal
    item.coverage_start_date = coverage_start
    item.coverage_end_date = coverage_end
    item.premium_paying_term_years = premium_paying_term_years
    item.policy_term_years = policy_term_years
    item.source_account_id = source_account_id
    item.owner = owner
    item.notes = (data.get("notes") or "").strip() or None
    item.sort_order = sort_order
    item.is_active = str(data.get("is_active", "1")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
