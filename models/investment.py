"""Investment model — mutual funds, SIPs, stocks, RSU, EPF, FD, NPS, gold."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Investment(db.Model):
    __tablename__ = "investments"

    ASSET_TYPES = (
        "mutual_fund",
        "sip",
        "stock",
        "rsu",
        "epf",
        "fd",
        "nps",
        "gold",
        "other",
    )
    OWNERS = ("self", "wife", "joint")

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    asset_type = db.Column(db.String(30), nullable=False, default="mutual_fund", index=True)
    invested_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    current_value = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    monthly_sip = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    # SIP schedule: debit day of month (1–28), source cash account, pause flag
    sip_day = db.Column(db.Integer, nullable=True)
    source_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True, index=True
    )
    sip_active = db.Column(db.Boolean, nullable=False, default=True)
    # MF NAV tracking (AMFI scheme code via mfapi.in)
    scheme_code = db.Column(db.String(20), nullable=True, index=True)
    units = db.Column(db.Numeric(18, 4), nullable=False, default=0)
    last_nav = db.Column(db.Numeric(14, 4), nullable=True)
    last_nav_date = db.Column(db.Date, nullable=True)
    owner = db.Column(db.String(20), nullable=False, default="joint")
    goal_id = db.Column(db.Integer, db.ForeignKey("goals.id"), nullable=True, index=True)
    start_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    goal = db.relationship("Goal", back_populates="investments")
    source_account = db.relationship("Account", foreign_keys=[source_account_id])

    __table_args__ = (
        db.CheckConstraint("invested_amount >= 0", name="ck_investment_invested_nonneg"),
        db.CheckConstraint("current_value >= 0", name="ck_investment_current_nonneg"),
        db.CheckConstraint("monthly_sip >= 0", name="ck_investment_sip_nonneg"),
        db.CheckConstraint("units >= 0", name="ck_investment_units_nonneg"),
    )

    @property
    def has_sip_plan(self) -> bool:
        return Decimal(self.monthly_sip or 0) > 0

    def __repr__(self) -> str:
        return f"<Investment {self.name}>"

    @property
    def gain(self) -> Decimal:
        return Decimal(self.current_value or 0) - Decimal(self.invested_amount or 0)

    @property
    def return_pct(self) -> float:
        invested = Decimal(self.invested_amount or 0)
        if invested <= 0:
            return 0.0
        return float((self.gain / invested) * 100)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "asset_type": self.asset_type,
            "invested_amount": float(self.invested_amount or 0),
            "current_value": float(self.current_value or 0),
            "gain": float(self.gain),
            "return_pct": round(self.return_pct, 2),
            "monthly_sip": float(self.monthly_sip or 0),
            "sip_day": self.sip_day,
            "source_account_id": self.source_account_id,
            "sip_active": self.sip_active,
            "scheme_code": self.scheme_code,
            "units": float(self.units or 0),
            "last_nav": float(self.last_nav) if self.last_nav is not None else None,
            "last_nav_date": self.last_nav_date.isoformat() if self.last_nav_date else None,
            "owner": self.owner,
            "goal_id": self.goal_id,
            "is_active": self.is_active,
        }
