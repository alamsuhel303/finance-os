"""Joint funding plan — monthly Suhel/Seema → Joint contributions + envelope split."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JointFundingPlan(db.Model):
    """Singleton-style monthly plan for funding the Joint account."""

    __tablename__ = "joint_funding_plans"

    id = db.Column(db.Integer, primary_key=True)
    self_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True
    )
    wife_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True
    )
    self_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    wife_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    day_of_month = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    self_account = db.relationship("Account", foreign_keys=[self_account_id])
    wife_account = db.relationship("Account", foreign_keys=[wife_account_id])
    splits = db.relationship(
        "JointFundingSplit",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="JointFundingSplit.sort_order",
        lazy="joined",
    )

    __table_args__ = (
        db.CheckConstraint("self_amount >= 0", name="ck_joint_funding_self_nonneg"),
        db.CheckConstraint("wife_amount >= 0", name="ck_joint_funding_wife_nonneg"),
        db.CheckConstraint(
            "day_of_month >= 1 AND day_of_month <= 28",
            name="ck_joint_funding_day",
        ),
    )

    def __repr__(self) -> str:
        return f"<JointFundingPlan id={self.id}>"

    @property
    def total_amount(self) -> Decimal:
        return Decimal(self.self_amount or 0) + Decimal(self.wife_amount or 0)


class JointFundingSplit(db.Model):
    """Envelope allocation line for a joint funding plan."""

    __tablename__ = "joint_funding_splits"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(
        db.Integer, db.ForeignKey("joint_funding_plans.id"), nullable=False, index=True
    )
    envelope_id = db.Column(
        db.Integer, db.ForeignKey("envelopes.id"), nullable=False, index=True
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    plan = db.relationship("JointFundingPlan", back_populates="splits")
    envelope = db.relationship("Envelope")

    __table_args__ = (
        db.CheckConstraint("amount >= 0", name="ck_joint_funding_split_nonneg"),
        db.UniqueConstraint("plan_id", "envelope_id", name="uq_joint_funding_plan_env"),
    )

    def __repr__(self) -> str:
        return f"<JointFundingSplit plan={self.plan_id} env={self.envelope_id}>"
