"""Telegram integration models — users, link codes, inbox, pending drafts, aliases."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelegramUser(db.Model):
    """Maps a Telegram account to a household owner (self / wife)."""

    __tablename__ = "telegram_users"

    OWNERS = ("self", "wife")

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.BigInteger, nullable=False, unique=True, index=True)
    owner = db.Column(db.String(20), nullable=False, index=True)
    telegram_username = db.Column(db.String(120))
    telegram_first_name = db.Column(db.String(120))
    telegram_last_name = db.Column(db.String(120))
    default_account_id = db.Column(
        db.Integer, db.ForeignKey("accounts.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    last_seen_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    default_account = db.relationship("Account", foreign_keys=[default_account_id])

    def __repr__(self) -> str:
        return f"<TelegramUser {self.telegram_user_id} owner={self.owner}>"


class TelegramLinkCode(db.Model):
    """One-time, expiring code to link a Telegram account to an owner."""

    __tablename__ = "telegram_link_codes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    owner = db.Column(db.String(20), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime)
    used_by_telegram_user_id = db.Column(db.BigInteger)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    def __repr__(self) -> str:
        return f"<TelegramLinkCode {self.code} owner={self.owner}>"


class TelegramMessage(db.Model):
    """Audit / inbox row for each Telegram update (idempotency key = update_id)."""

    __tablename__ = "telegram_messages"

    STATUSES = ("received", "processing", "processed", "failed", "ignored")

    id = db.Column(db.Integer, primary_key=True)
    telegram_update_id = db.Column(db.BigInteger, nullable=False, unique=True, index=True)
    telegram_message_id = db.Column(db.BigInteger)
    telegram_user_id = db.Column(db.BigInteger, nullable=False, index=True)
    chat_id = db.Column(db.BigInteger, nullable=False, index=True)
    message_text = db.Column(db.Text)
    received_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    processed_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="received", index=True)
    error_message = db.Column(db.Text)
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    transaction = db.relationship("Transaction", foreign_keys=[transaction_id])

    def __repr__(self) -> str:
        return f"<TelegramMessage update={self.telegram_update_id} status={self.status}>"


class TelegramPendingTransaction(db.Model):
    """Draft expense awaiting Confirm / Edit / Cancel."""

    __tablename__ = "telegram_pending_transactions"

    STATUSES = ("pending", "confirmed", "cancelled", "expired")

    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.BigInteger, nullable=False, index=True)
    chat_id = db.Column(db.BigInteger, nullable=False)
    telegram_message_row_id = db.Column(
        db.Integer, db.ForeignKey("telegram_messages.id"), nullable=True, index=True
    )
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    merchant = db.Column(db.String(120))
    description = db.Column(db.String(255), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=True)
    transaction_date = db.Column(db.Date, nullable=False)
    paid_by = db.Column(db.String(20), nullable=False, default="self")
    edit_field = db.Column(db.String(40))  # awaiting text for amount/description/date
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    confirmed_transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True
    )

    category = db.relationship("Category", foreign_keys=[category_id])
    account = db.relationship("Account", foreign_keys=[account_id])
    message = db.relationship("TelegramMessage", foreign_keys=[telegram_message_row_id])

    def __repr__(self) -> str:
        return f"<TelegramPendingTransaction id={self.id} status={self.status}>"


class TelegramCategoryAlias(db.Model):
    """Maps free-text keywords to existing Finance OS categories."""

    __tablename__ = "telegram_category_aliases"

    id = db.Column(db.Integer, primary_key=True)
    alias = db.Column(db.String(80), nullable=False, unique=True, index=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    category = db.relationship("Category", foreign_keys=[category_id])

    def __repr__(self) -> str:
        return f"<TelegramCategoryAlias {self.alias}>"
