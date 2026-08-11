"""Insurance policies — cover, premium, renewal tracking."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Insurance(db.Model):
    __tablename__ = "insurances"

    POLICY_TYPES = (
        "health",
        "term",
        "life",
        "vehicle",
        "home",
        "other",
    )
    PREMIUM_FREQUENCIES = ("monthly", "quarterly", "yearly", "one_time")
    OWNERS = ("self", "wife", "joint")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    policy_type = db.Column(db.String(30), nullable=False, default="health")
    insurer = db.Column(db.String(120), nullable=True)
    policy_number = db.Column(db.String(80), nullable=True)
    cover_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    premium_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    premium_frequency = db.Column(db.String(20), nullable=False, default="yearly")
    next_renewal_date = db.Column(db.Date, nullable=True)
    owner = db.Column(db.String(20), nullable=False, default="joint")
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        db.CheckConstraint("cover_amount >= 0", name="ck_insurance_cover_nonneg"),
        db.CheckConstraint("premium_amount >= 0", name="ck_insurance_premium_nonneg"),
    )

    def __repr__(self) -> str:
        return f"<Insurance {self.name}>"

    @property
    def days_to_renewal(self) -> int | None:
        if not self.next_renewal_date:
            return None
        return (self.next_renewal_date - date.today()).days

    @property
    def renewal_status(self) -> str:
        days = self.days_to_renewal
        if days is None:
            return "unknown"
        if days < 0:
            return "overdue"
        if days <= 30:
            return "due_soon"
        if days <= 90:
            return "upcoming"
        return "ok"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "policy_type": self.policy_type,
            "cover_amount": float(self.cover_amount or 0),
            "premium_amount": float(self.premium_amount or 0),
            "next_renewal_date": (
                self.next_renewal_date.isoformat() if self.next_renewal_date else None
            ),
            "owner": self.owner,
            "is_active": self.is_active,
        }
