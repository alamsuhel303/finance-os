"""Transaction model — the core ledger entry."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Transaction(db.Model):
    __tablename__ = "transactions"

    TRANSACTION_TYPES = (
        "expense",
        "income",
        "investment",
        "transfer",
        "refund",
    )
    NEED_WANT_CHOICES = ("need", "want", "n/a")
    PAYMENT_MODES = (
        "upi",
        "card",
        "netbanking",
        "cash",
        "auto_debit",
        "cheque",
        "other",
    )
    PAID_BY_CHOICES = ("self", "wife", "joint")
    SOURCES = ("web", "telegram", "import")

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, index=True, default=date.today)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    transaction_type = db.Column(
        db.String(20), nullable=False, default="expense", index=True
    )

    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True
    )
    subcategory_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=True
    )
    account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True
    )
    # For transfers: destination account
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True
    )

    paid_by = db.Column(db.String(20), nullable=False, default="self")
    payment_mode = db.Column(db.String(30), nullable=False, default="upi")
    need_want = db.Column(db.String(10), nullable=False, default="need")
    notes = db.Column(db.Text)

    is_recurring = db.Column(db.Boolean, nullable=False, default=False)
    is_excluded_from_budget = db.Column(db.Boolean, nullable=False, default=False)

    # Optional: which virtual envelope an expense draws from
    envelope_id = db.Column(
        db.Integer, db.ForeignKey("envelopes.id"), nullable=True, index=True
    )
    # Optional: which investment holding an investment installment funds
    investment_id = db.Column(
        db.Integer, db.ForeignKey("investments.id"), nullable=True, index=True
    )
    # False for salary-deducted contributions (EPF) — no bank account debit
    skip_cash_impact = db.Column(db.Boolean, nullable=False, default=False)

    # Origin channel — web UI, Telegram bot, or Excel import
    source = db.Column(db.String(20), nullable=False, default="web", index=True)
    # Soft link to inbox row (no FK — avoids circular create with telegram_messages)
    telegram_message_id = db.Column(db.Integer, nullable=True, index=True)

    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    category = db.relationship(
        "Category",
        foreign_keys=[category_id],
        back_populates="transactions",
    )
    subcategory = db.relationship(
        "Category",
        foreign_keys=[subcategory_id],
    )
    account = db.relationship(
        "Account",
        foreign_keys=[account_id],
        back_populates="transactions",
    )
    to_account = db.relationship(
        "Account",
        foreign_keys=[to_account_id],
    )
    envelope = db.relationship("Envelope", foreign_keys=[envelope_id])
    investment = db.relationship("Investment", foreign_keys=[investment_id])
    envelope_entries = db.relationship(
        "EnvelopeEntry",
        back_populates="transaction",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        db.Index("ix_transactions_date_type", "date", "transaction_type"),
    )

    def __repr__(self) -> str:
        return f"<Transaction {self.id} {self.transaction_type} {self.amount}>"

    @property
    def signed_amount(self) -> Decimal:
        """Amount signed relative to cashflow: income/refund +, expense/investment -."""
        value = Decimal(self.amount or 0)
        if self.transaction_type in ("income", "refund"):
            return value
        if self.transaction_type in ("expense", "investment"):
            return -value
        return Decimal("0")  # transfers are neutral at household level

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "amount": float(self.amount or 0),
            "description": self.description,
            "transaction_type": self.transaction_type,
            "category_id": self.category_id,
            "category_name": self.category.name if self.category else None,
            "subcategory_id": self.subcategory_id,
            "account_id": self.account_id,
            "account_name": self.account.name if self.account else None,
            "paid_by": self.paid_by,
            "payment_mode": self.payment_mode,
            "need_want": self.need_want,
            "notes": self.notes,
        }
