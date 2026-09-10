"""Friends and expense splits — Splitwise-lite for group bills you paid."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Friend(db.Model):
    __tablename__ = "friends"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    shares = db.relationship("ExpenseSplitShare", back_populates="friend", lazy="dynamic")
    settlements = db.relationship(
        "ExpenseSplitSettlement", back_populates="friend", lazy="dynamic"
    )

    def __repr__(self) -> str:
        return f"<Friend {self.name}>"


class ExpenseSplit(db.Model):
    """One split bill linked to the paying expense transaction."""

    __tablename__ = "expense_splits"

    STATUSES = ("open", "partial", "settled")

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer,
        db.ForeignKey("transactions.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    total_amount = db.Column(db.Numeric(14, 2), nullable=False)
    household_share = db.Column(db.Numeric(14, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    transaction = db.relationship(
        "Transaction",
        back_populates="expense_split",
        foreign_keys=[transaction_id],
    )
    shares = db.relationship(
        "ExpenseSplitShare",
        back_populates="split",
        cascade="all, delete-orphan",
        order_by="ExpenseSplitShare.id",
    )
    settlements = db.relationship(
        "ExpenseSplitSettlement",
        back_populates="split",
        cascade="all, delete-orphan",
        order_by="ExpenseSplitSettlement.date.desc()",
    )

    __table_args__ = (
        db.CheckConstraint("total_amount > 0", name="ck_expense_split_total_positive"),
        db.CheckConstraint(
            "household_share >= 0", name="ck_expense_split_household_nonneg"
        ),
    )

    @property
    def friends_total(self) -> Decimal:
        return sum(
            (Decimal(s.amount or 0) for s in self.shares if not s.is_household),
            Decimal("0"),
        )

    @property
    def outstanding_total(self) -> Decimal:
        return sum(
            (s.outstanding for s in self.shares if not s.is_household),
            Decimal("0"),
        )

    def __repr__(self) -> str:
        return f"<ExpenseSplit id={self.id} status={self.status}>"


class ExpenseSplitShare(db.Model):
    """One line on a split: household share or a friend's share."""

    __tablename__ = "expense_split_shares"

    STATUSES = ("open", "partial", "settled")

    id = db.Column(db.Integer, primary_key=True)
    split_id = db.Column(
        db.Integer, db.ForeignKey("expense_splits.id"), nullable=False, index=True
    )
    friend_id = db.Column(
        db.Integer, db.ForeignKey("friends.id"), nullable=True, index=True
    )
    is_household = db.Column(db.Boolean, nullable=False, default=False)
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    settled_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)

    split = db.relationship("ExpenseSplit", back_populates="shares")
    friend = db.relationship("Friend", back_populates="shares")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_expense_split_share_amount_positive"),
        db.CheckConstraint(
            "settled_amount >= 0", name="ck_expense_split_share_settled_nonneg"
        ),
    )

    @property
    def outstanding(self) -> Decimal:
        if self.is_household:
            return Decimal("0")
        return max(Decimal(self.amount or 0) - Decimal(self.settled_amount or 0), Decimal("0"))

    def refresh_status(self) -> None:
        if self.is_household:
            self.status = "settled"
            self.settled_amount = Decimal(self.amount or 0)
            return
        settled = Decimal(self.settled_amount or 0)
        total = Decimal(self.amount or 0)
        if settled <= 0:
            self.status = "open"
        elif settled >= total:
            self.status = "settled"
            self.settled_amount = total
        else:
            self.status = "partial"

    def __repr__(self) -> str:
        who = "household" if self.is_household else f"friend={self.friend_id}"
        return f"<ExpenseSplitShare {who} {self.amount}>"


class ExpenseSplitSettlement(db.Model):
    """Cash repayment from a friend against open split share(s)."""

    __tablename__ = "expense_split_settlements"

    id = db.Column(db.Integer, primary_key=True)
    split_id = db.Column(
        db.Integer, db.ForeignKey("expense_splits.id"), nullable=False, index=True
    )
    friend_id = db.Column(
        db.Integer, db.ForeignKey("friends.id"), nullable=False, index=True
    )
    share_id = db.Column(
        db.Integer, db.ForeignKey("expense_split_shares.id"), nullable=True, index=True
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True
    )
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    # Income-like credit that restores cash (no budget/envelope impact)
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True
    )
    notes = db.Column(db.Text)
    share_allocations = db.Column(db.Text)  # JSON [{share_id, amount}, …]
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    split = db.relationship("ExpenseSplit", back_populates="settlements")
    friend = db.relationship("Friend", back_populates="settlements")
    share = db.relationship("ExpenseSplitShare", foreign_keys=[share_id])
    account = db.relationship("Account", foreign_keys=[account_id])
    transaction = db.relationship("Transaction", foreign_keys=[transaction_id])

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_expense_split_settlement_positive"),
    )

    def __repr__(self) -> str:
        return f"<ExpenseSplitSettlement friend={self.friend_id} {self.amount}>"
