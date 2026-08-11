"""Budget model — monthly category envelopes."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Budget(db.Model):
    __tablename__ = "budgets"

    id = db.Column(db.Integer, primary_key=True)
    year = db.Column(db.Integer, nullable=False, index=True)
    month = db.Column(db.Integer, nullable=False, index=True)  # 1–12
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    category = db.relationship("Category", backref=db.backref("budgets", lazy="dynamic"))

    __table_args__ = (
        db.UniqueConstraint(
            "year", "month", "category_id", name="uq_budget_year_month_category"
        ),
        db.CheckConstraint("month >= 1 AND month <= 12", name="ck_budget_month"),
        db.CheckConstraint("amount >= 0", name="ck_budget_amount_nonneg"),
    )

    def __repr__(self) -> str:
        return f"<Budget {self.year}-{self.month:02d} cat={self.category_id}>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "year": self.year,
            "month": self.month,
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else None,
            "amount": float(self.amount or 0),
            "notes": self.notes,
        }
