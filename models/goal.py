"""Goal model — emergency, home, travel, car, retirement, education, custom."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Goal(db.Model):
    __tablename__ = "goals"

    GOAL_TYPES = (
        "emergency",
        "home",
        "travel",
        "car",
        "retirement",
        "education",
        "custom",
    )
    OWNERS = ("self", "wife", "joint")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    slug = db.Column(db.String(140), nullable=False, unique=True)
    goal_type = db.Column(db.String(30), nullable=False, default="custom", index=True)
    target_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    # Manual override; if linked_account_id set, current is preferred from account
    current_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    monthly_contribution = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    target_date = db.Column(db.Date, nullable=True)
    linked_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True
    )
    owner = db.Column(db.String(20), nullable=False, default="joint")
    icon = db.Column(db.String(50), default="bi-flag")
    color = db.Column(db.String(20), default="#5eead4")
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    linked_account = db.relationship("Account", foreign_keys=[linked_account_id])
    investments = db.relationship("Investment", back_populates="goal", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("target_amount >= 0", name="ck_goal_target_nonneg"),
        db.CheckConstraint("current_amount >= 0", name="ck_goal_current_nonneg"),
        db.CheckConstraint(
            "monthly_contribution >= 0", name="ck_goal_contribution_nonneg"
        ),
    )

    def __repr__(self) -> str:
        return f"<Goal {self.name}>"

    @property
    def effective_current(self) -> Decimal:
        """
        Progress amount for this goal:
        - Emergency: tagged cash + emergency-purpose investments
        - Else: linked account cash (if any) + sum of holdings tagged to this goal
        - If neither is set: manual current_amount
        """
        if self.goal_type == "emergency":
            from services.emergency_service import get_total

            return get_total(self)

        total = Decimal("0")
        has_sources = False

        if self.linked_account is not None:
            has_sources = True
            total += Decimal(self.linked_account.current_balance or 0)

        holdings = self.investments.filter_by(is_active=True).all()
        if holdings:
            has_sources = True
            total += sum(
                (Decimal(h.current_value or 0) for h in holdings),
                Decimal("0"),
            )

        if has_sources:
            return total
        return Decimal(self.current_amount or 0)

    @property
    def remaining(self) -> Decimal:
        return max(Decimal("0"), Decimal(self.target_amount or 0) - self.effective_current)

    @property
    def progress_pct(self) -> float:
        target = Decimal(self.target_amount or 0)
        if target <= 0:
            return 0.0
        return min(100.0, float((self.effective_current / target) * 100))

    @property
    def estimated_completion(self) -> date | None:
        """ETA from remaining / monthly contribution, or stored target_date."""
        import math

        remaining = self.remaining
        if remaining <= 0:
            return date.today()
        contrib = Decimal(self.monthly_contribution or 0)
        if contrib > 0:
            months = max(1, math.ceil(float(remaining / contrib)))
            y, m = date.today().year, date.today().month + months
            while m > 12:
                m -= 12
                y += 1
            return date(y, m, 1)
        return self.target_date

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "goal_type": self.goal_type,
            "target_amount": float(self.target_amount or 0),
            "current_amount": float(self.effective_current),
            "remaining": float(self.remaining),
            "progress_pct": round(self.progress_pct, 1),
            "monthly_contribution": float(self.monthly_contribution or 0),
            "owner": self.owner,
        }
