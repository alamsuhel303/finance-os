"""Account model — every rupee lives in an account."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    account_type = db.Column(db.String(50), nullable=False, default="cash")
    # cash | salary | joint | emergency | goal | investment
    owner = db.Column(db.String(50), nullable=False, default="joint")
    # self | wife | joint
    opening_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    current_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    # Virtual earmark: how much of current_balance counts as Emergency Fund
    emergency_tagged = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="INR")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    transactions = db.relationship(
        "Transaction",
        back_populates="account",
        lazy="dynamic",
        foreign_keys="Transaction.account_id",
    )

    def __repr__(self) -> str:
        return f"<Account {self.name}>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "account_type": self.account_type,
            "owner": self.owner,
            "opening_balance": float(self.opening_balance or 0),
            "current_balance": float(self.current_balance or 0),
            "is_active": self.is_active,
        }
