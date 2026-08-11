"""Liability model — loans and other debts for net-worth calculation."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Liability(db.Model):
    __tablename__ = "liabilities"

    LIABILITY_TYPES = (
        "home_loan",
        "car_loan",
        "personal_loan",
        "credit_card",
        "education_loan",
        "other",
    )
    OWNERS = ("self", "wife", "joint")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    liability_type = db.Column(db.String(30), nullable=False, default="other")
    outstanding_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    interest_rate = db.Column(db.Numeric(6, 2), nullable=True)
    owner = db.Column(db.String(20), nullable=False, default="joint")
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        db.CheckConstraint(
            "outstanding_amount >= 0", name="ck_liability_amount_nonneg"
        ),
    )

    def __repr__(self) -> str:
        return f"<Liability {self.name}>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "liability_type": self.liability_type,
            "outstanding_amount": float(self.outstanding_amount or 0),
            "owner": self.owner,
            "is_active": self.is_active,
        }
