"""Net-worth monthly snapshot for growth charts."""

from __future__ import annotations

from datetime import date, datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NetWorthSnapshot(db.Model):
    __tablename__ = "net_worth_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    snapshot_date = db.Column(db.Date, nullable=False, unique=True, index=True)
    # First of the month typically
    cash_savings = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    investments = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    other_assets = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    liabilities = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    net_worth = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<NetWorthSnapshot {self.snapshot_date} {self.net_worth}>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "snapshot_date": self.snapshot_date.isoformat() if self.snapshot_date else None,
            "cash_savings": float(self.cash_savings or 0),
            "investments": float(self.investments or 0),
            "other_assets": float(self.other_assets or 0),
            "liabilities": float(self.liabilities or 0),
            "net_worth": float(self.net_worth or 0),
        }
