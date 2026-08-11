"""Virtual envelope models — purpose pots that sit alongside real accounts."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Envelope(db.Model):
    """Virtual spending pot (Essentials, Travel, Shopping, …)."""

    __tablename__ = "envelopes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    icon = db.Column(db.String(50), default="bi-envelope")
    color = db.Column(db.String(20), default="#5eead4")
    current_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    is_system = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    entries = db.relationship(
        "EnvelopeEntry",
        back_populates="envelope",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    categories = db.relationship(
        "Category",
        back_populates="envelope",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return f"<Envelope {self.name}>"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "current_balance": float(self.current_balance or 0),
            "color": self.color,
            "icon": self.icon,
        }


class EnvelopeEntry(db.Model):
    """Ledger line for an envelope (allocation from transfer, or spend)."""

    __tablename__ = "envelope_entries"

    ENTRY_TYPES = (
        "allocation",
        "spend",
        "adjustment",
        "reallocation_in",
        "reallocation_out",
    )

    id = db.Column(db.Integer, primary_key=True)
    envelope_id = db.Column(
        db.Integer, db.ForeignKey("envelopes.id"), nullable=False, index=True
    )
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True
    )
    entry_type = db.Column(db.String(20), nullable=False, default="allocation")
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    envelope = db.relationship("Envelope", back_populates="entries")
    transaction = db.relationship(
        "Transaction",
        back_populates="envelope_entries",
    )

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_envelope_entry_amount_positive"),
    )

    def __repr__(self) -> str:
        return f"<EnvelopeEntry {self.entry_type} {self.amount}>"

    @property
    def signed_amount(self) -> Decimal:
        value = Decimal(self.amount or 0)
        if self.entry_type in ("spend", "reallocation_out"):
            return -value
        return value  # allocation / adjustment / reallocation_in
