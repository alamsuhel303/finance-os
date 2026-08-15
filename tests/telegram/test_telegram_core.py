"""Telegram authorization, linking, parser, confirm, idempotency tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from extensions import db
from models import (
    Account,
    Category,
    TelegramLinkCode,
    TelegramMessage,
    TelegramPendingTransaction,
    TelegramUser,
    Transaction,
)
from services import telegram_service
from services.telegram_service import TelegramServiceError
from telegram_bot.authorization import require_user
from telegram_bot.parser import parse_expense_text


def test_unauthorized_user(ctx):
    assert require_user(999999001) is None


def test_link_code_success_and_reuse(ctx):
    code = telegram_service.generate_link_code("self")
    user = telegram_service.redeem_link_code(
        code.code, 111001, username="suhel_tg", first_name="Suhel"
    )
    assert user.owner == "self"
    assert user.telegram_user_id == 111001
    assert require_user(111001) is not None

    with pytest.raises(TelegramServiceError, match="already used"):
        telegram_service.redeem_link_code(code.code, 111002)


def test_link_code_expiry(ctx):
    row = telegram_service.generate_link_code("wife")
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.session.commit()
    with pytest.raises(TelegramServiceError, match="expired"):
        telegram_service.redeem_link_code(row.code, 222001)


def test_two_users_map_to_owners(ctx):
    c1 = telegram_service.generate_link_code("self")
    c2 = telegram_service.generate_link_code("wife")
    u1 = telegram_service.redeem_link_code(c1.code, 111)
    u2 = telegram_service.redeem_link_code(c2.code, 222)
    assert u1.owner == "self"
    assert u2.owner == "wife"
    # same telegram id on "another device" is the same row
    again = telegram_service.redeem_link_code(
        telegram_service.generate_link_code("self").code, 111
    )
    assert again.id == u1.id


def test_parser_amount_and_alias(ctx):
    parsed = parse_expense_text("450 dinner")
    assert parsed.amount == Decimal("450")
    assert parsed.error is None
    assert parsed.category_id is not None
    assert parsed.category_confidence == "high"
    cat = db.session.get(Category, parsed.category_id)
    assert cat and "Dining" in cat.name


def test_parser_personal_and_yesterday(ctx):
    parsed = parse_expense_text("2500 headphones personal yesterday")
    assert parsed.amount == Decimal("2500")
    assert parsed.account_hint == "personal"
    assert parsed.txn_date == telegram_service.today_local() - timedelta(days=1)


def test_parser_invalid(ctx):
    parsed = parse_expense_text("no amount here")
    assert parsed.error


def test_confirm_creates_one_transaction(ctx):
    code = telegram_service.generate_link_code("self")
    user = telegram_service.redeem_link_code(code.code, 333)
    joint = (
        Account.query.filter(
            (Account.owner == "joint") | (Account.account_type == "joint")
        )
        .filter_by(is_active=True)
        .first()
    )
    assert joint
    joint.current_balance = Decimal("100000")
    db.session.commit()
    dining = Category.query.filter(Category.name.ilike("%dining%")).first()
    assert dining

    msg, is_new = telegram_service.record_incoming_update(
        update_id=9001,
        message_id=1,
        telegram_user_id=333,
        chat_id=333,
        text="/add 450 dinner",
    )
    assert is_new
    pending = telegram_service.create_pending(
        user=user,
        chat_id=333,
        message_row=msg,
        amount=Decimal("450"),
        description="dinner",
        category_id=dining.id,
        account_id=joint.id,
        txn_date=telegram_service.today_local(),
        paid_by="self",
    )
    before = Transaction.query.count()
    txn = telegram_service.confirm_pending(pending, 333)
    assert Transaction.query.count() == before + 1
    assert txn.source == "telegram"
    assert txn.paid_by == "self"
    assert txn.amount == Decimal("450")
    msg = db.session.get(TelegramMessage, msg.id)
    assert msg.status == "processed"
    assert msg.transaction_id == txn.id

    # double confirm idempotent
    txn2 = telegram_service.confirm_pending(pending, 333)
    assert txn2.id == txn.id
    assert Transaction.query.count() == before + 1


def test_duplicate_update_id(ctx):
    m1, new1 = telegram_service.record_incoming_update(
        update_id=8001, message_id=1, telegram_user_id=1, chat_id=1, text="hi"
    )
    m2, new2 = telegram_service.record_incoming_update(
        update_id=8001, message_id=1, telegram_user_id=1, chat_id=1, text="hi"
    )
    assert new1 is True
    assert new2 is False
    assert m1.id == m2.id


def test_cancel_no_transaction(ctx):
    code = telegram_service.generate_link_code("self")
    user = telegram_service.redeem_link_code(code.code, 444)
    dining = Category.query.filter(Category.name.ilike("%dining%")).first()
    joint = Account.query.filter_by(is_active=True).first()
    pending = telegram_service.create_pending(
        user=user,
        chat_id=444,
        message_row=None,
        amount=Decimal("100"),
        description="x",
        category_id=dining.id,
        account_id=joint.id,
        txn_date=telegram_service.today_local(),
        paid_by="self",
    )
    before = Transaction.query.count()
    telegram_service.cancel_pending(pending, 444)
    assert pending.status == "cancelled"
    assert Transaction.query.count() == before


def test_unauthorized_callback_owner(ctx):
    code = telegram_service.generate_link_code("self")
    user = telegram_service.redeem_link_code(code.code, 555)
    dining = Category.query.filter(Category.name.ilike("%dining%")).first()
    joint = Account.query.filter_by(is_active=True).first()
    pending = telegram_service.create_pending(
        user=user,
        chat_id=555,
        message_row=None,
        amount=Decimal("50"),
        description="x",
        category_id=dining.id,
        account_id=joint.id,
        txn_date=telegram_service.today_local(),
        paid_by="self",
    )
    with pytest.raises(TelegramServiceError, match="not available"):
        telegram_service.confirm_pending(pending, 999)


def test_undo_telegram_transaction(ctx):
    code = telegram_service.generate_link_code("self")
    user = telegram_service.redeem_link_code(code.code, 666)
    dining = Category.query.filter(Category.name.ilike("%dining%")).first()
    joint = (
        Account.query.filter(
            (Account.owner == "joint") | (Account.name.ilike("%joint%"))
        )
        .filter_by(is_active=True)
        .first()
        or Account.query.filter_by(is_active=True).first()
    )
    joint.current_balance = Decimal("100000")
    db.session.commit()
    msg, _ = telegram_service.record_incoming_update(
        update_id=7001, message_id=2, telegram_user_id=666, chat_id=666, text="x"
    )
    pending = telegram_service.create_pending(
        user=user,
        chat_id=666,
        message_row=msg,
        amount=Decimal("80"),
        description="undo-me",
        category_id=dining.id,
        account_id=joint.id,
        txn_date=telegram_service.today_local(),
        paid_by="self",
    )
    txn = telegram_service.confirm_pending(pending, 666)
    tid = txn.id
    telegram_service.undo_telegram_transaction(txn, 666)
    assert db.session.get(Transaction, tid) is None


def test_failed_parse_marks_failed_path(ctx):
    # service-level: mark_message failed with no txn
    msg, _ = telegram_service.record_incoming_update(
        update_id=6001, message_id=3, telegram_user_id=1, chat_id=1, text="junk"
    )
    telegram_service.mark_message(msg, "failed", error="could not parse")
    assert msg.status == "failed"
    assert Transaction.query.filter_by(telegram_message_id=msg.id).count() == 0
