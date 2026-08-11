"""Insurance policy tracking — cover, premiums, renewals."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from extensions import db
from models import Insurance
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
    "one_time": "One-time",
}


def list_policies(*, active_only: bool = True) -> list[Insurance]:
    query = Insurance.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Insurance.sort_order, Insurance.name).all()


def get_policy(policy_id: int) -> Optional[Insurance]:
    return db.session.get(Insurance, policy_id)


def get_overview() -> dict[str, Any]:
    policies = list_policies(active_only=False)
    active = [p for p in policies if p.is_active]
    total_cover = sum((float(p.cover_amount or 0) for p in active), 0.0)
    annual_premium = 0.0
    for p in active:
        prem = float(p.premium_amount or 0)
        freq = p.premium_frequency or "yearly"
        if freq == "monthly":
            annual_premium += prem * 12
        elif freq == "quarterly":
            annual_premium += prem * 4
        elif freq == "yearly":
            annual_premium += prem
        else:
            annual_premium += prem

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

    renewal = None
    raw_date = (data.get("next_renewal_date") or "").strip()
    if raw_date:
        try:
            renewal = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise InsuranceValidationError("Invalid renewal date.") from exc

    try:
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0

    item.name = name
    item.policy_type = policy_type
    item.insurer = (data.get("insurer") or "").strip() or None
    item.policy_number = (data.get("policy_number") or "").strip() or None
    item.cover_amount = cover
    item.premium_amount = premium
    item.premium_frequency = freq
    item.next_renewal_date = renewal
    item.owner = owner
    item.notes = (data.get("notes") or "").strip() or None
    item.sort_order = sort_order
    item.is_active = str(data.get("is_active", "1")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
