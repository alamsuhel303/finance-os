"""Insurance policies — cover, premium, renewal tracking."""

from __future__ import annotations

from datetime import date, datetime, timezone

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
    # Account debited when posting premium from Month checklist
    source_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True
    )
    # Health / multi-year prepaid cover window (e.g. pay once for 3 years)
    coverage_start_date = db.Column(db.Date, nullable=True)
    coverage_end_date = db.Column(db.Date, nullable=True)
    # Term life: years you pay premium + total cover term
    premium_paying_term_years = db.Column(db.Integer, nullable=True)
    policy_term_years = db.Column(db.Integer, nullable=True)
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
    def action_date(self) -> date | None:
        """Soonest date that needs attention (premium due or cover ending)."""
        candidates = [d for d in (self.next_renewal_date, self.coverage_end_date) if d]
        return min(candidates) if candidates else None

    @property
    def days_to_renewal(self) -> int | None:
        action = self.action_date
        if not action:
            return None
        return (action - date.today()).days

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

    @property
    def coverage_years(self) -> float | None:
        if self.coverage_start_date and self.coverage_end_date:
            days = (self.coverage_end_date - self.coverage_start_date).days
            if days > 0:
                return max(days / 365.25, 1 / 12)
        if self.policy_term_years and self.policy_term_years > 0:
            return float(self.policy_term_years)
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "policy_type": self.policy_type,
            "cover_amount": float(self.cover_amount or 0),
            "premium_amount": float(self.premium_amount or 0),
            "premium_frequency": self.premium_frequency,
            "next_renewal_date": (
                self.next_renewal_date.isoformat() if self.next_renewal_date else None
            ),
            "coverage_start_date": (
                self.coverage_start_date.isoformat()
                if self.coverage_start_date
                else None
            ),
            "coverage_end_date": (
                self.coverage_end_date.isoformat() if self.coverage_end_date else None
            ),
            "premium_paying_term_years": self.premium_paying_term_years,
            "policy_term_years": self.policy_term_years,
            "owner": self.owner,
            "is_active": self.is_active,
        }
