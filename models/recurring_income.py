"""Recurring income templates — monthly salary / credit suggestions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RecurringIncome(db.Model):
    __tablename__ = "recurring_incomes"

    OWNERS = ("self", "wife", "joint")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True
    )
    # Credit day of month (1–28 to stay safe across months)
    day_of_month = db.Column(db.Integer, nullable=False, default=1)
    owner = db.Column(db.String(20), nullable=False, default="self")
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    account = db.relationship("Account", foreign_keys=[account_id])

    __table_args__ = (
        db.CheckConstraint("amount >= 0", name="ck_recurring_income_amount_nonneg"),
        db.CheckConstraint(
            "day_of_month >= 1 AND day_of_month <= 28",
            name="ck_recurring_income_day",
        ),
    )

    def __repr__(self) -> str:
        return f"<RecurringIncome {self.name}>"

    @property
    def month_description(self) -> str:
        """Stable description used for idempotent monthly posts."""
        from datetime import date

        return f"{self.name} · {date.today().strftime('%b %Y')}"
