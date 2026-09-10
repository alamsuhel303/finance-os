"""Expense split (friends / Splitwise-lite) regression tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from werkzeug.datastructures import MultiDict

from extensions import db
from models import Account, Category, EnvelopeEntry, Transaction
from services import envelope_service, net_worth_service, split_service, transaction_service
from services.transaction_service import TransactionValidationError


def _joint_and_dining(ctx):
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
    return joint, dining


def test_split_expense_cash_full_budget_household(ctx):
    joint, dining = _joint_and_dining(ctx)
    cash_before = Decimal(joint.current_balance)
    friend = split_service.create_friend({"name": "Rahul"})

    env = envelope_service.resolve_envelope_for_expense(
        envelope_id=None, category_id=dining.id
    ) or envelope_service.get_essentials_envelope()

    form = MultiDict(
        [
            ("amount", "2000"),
            ("description", "Lunch with friends"),
            ("transaction_type", "expense"),
            ("account_id", str(joint.id)),
            ("category_id", str(dining.id)),
            ("date", transaction_service._parse_date(None).isoformat()),
            ("paid_by", "self"),
            ("payment_mode", "upi"),
            ("need_want", "want"),
            ("split_enabled", "1"),
            ("split_household_share", "500"),
            ("split_friend_id", str(friend.id)),
            ("split_friend_amount", "1500"),
        ]
    )

    txn, _ = transaction_service.create_transaction(form)
    db.session.refresh(joint)
    assert Decimal(joint.current_balance) == cash_before - Decimal("2000")
    assert txn.household_share_amount == Decimal("500")
    assert txn.expense_split is not None
    assert txn.expense_split.outstanding_total == Decimal("1500")

    if env:
        entry = EnvelopeEntry.query.filter_by(
            transaction_id=txn.id, entry_type="spend"
        ).first()
        assert entry
        assert Decimal(entry.amount) == Decimal("500")

    assert split_service.friend_outstanding(friend.id) == Decimal("1500")
    live = net_worth_service.compute_live_net_worth()
    assert live["friends_receivable"] >= Decimal("1500")


def test_settle_reduces_receivable_no_budget_hit(ctx):
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Priya"})

    form = MultiDict(
        [
            ("amount", "1200"),
            ("description", "Dinner"),
            ("transaction_type", "expense"),
            ("account_id", str(joint.id)),
            ("category_id", str(dining.id)),
            ("date", transaction_service._parse_date(None).isoformat()),
            ("paid_by", "self"),
            ("split_enabled", "1"),
            ("split_household_share", "400"),
            ("split_friend_id", str(friend.id)),
            ("split_friend_amount", "800"),
        ]
    )
    txn, _ = transaction_service.create_transaction(form)
    cash_mid = Decimal(joint.current_balance)
    assert split_service.friend_outstanding(friend.id) == Decimal("800")

    settlement = split_service.settle_friend_share(
        friend_id=friend.id,
        amount=Decimal("800"),
        account_id=joint.id,
    )
    db.session.refresh(joint)
    assert Decimal(joint.current_balance) == cash_mid + Decimal("800")
    assert split_service.friend_outstanding(friend.id) == Decimal("0")
    assert settlement.transaction_id
    settle_txn = db.session.get(Transaction, settlement.transaction_id)
    assert settle_txn.is_excluded_from_budget
    split = split_service.get_split_for_transaction(txn.id)
    assert split.status == "settled"


def test_equal_split_math(ctx):
    hh, friends = split_service.equal_split_amounts(
        Decimal("100"), friend_count=3, include_household=True
    )
    assert hh + sum(friends) == Decimal("100")
    assert len(friends) == 3


def test_proportion_split_math(ctx):
    amounts = split_service.proportion_split_amounts(
        Decimal("2000"), [Decimal("2"), Decimal("1"), Decimal("1")]
    )
    assert amounts == [Decimal("1000.00"), Decimal("500.00"), Decimal("500.00")]
    assert sum(amounts) == Decimal("2000.00")


def test_telegram_split_parser_modes(ctx):
    from telegram_bot.parser import parse_expense_text

    rahul = split_service.create_friend({"name": "Rahul"})
    priya = split_service.create_friend({"name": "Priya"})

    normal = parse_expense_text("450 dinner")
    assert normal.error is None
    assert normal.split_payload is None

    equal = parse_expense_text("2000 dinner split equal Rahul Priya")
    assert equal.error is None
    assert equal.split_payload is not None
    assert equal.split_payload["household_share"] == Decimal("666.67") or sum(
        [equal.split_payload["household_share"]]
        + [f["amount"] for f in equal.split_payload["friends"]]
    ) == Decimal("2000")

    amounts = parse_expense_text("2000 dinner split Rahul 500 Priya 500")
    assert amounts.error is None
    assert amounts.split_payload["household_share"] == Decimal("1000")
    assert {f["friend_id"]: f["amount"] for f in amounts.split_payload["friends"]} == {
        rahul.id: Decimal("500"),
        priya.id: Decimal("500"),
    }

    parts = parse_expense_text("2000 dinner split parts me 2 Rahul 1 Priya 1")
    assert parts.error is None
    assert parts.split_payload["household_share"] == Decimal("1000.00")
    assert sum(f["amount"] for f in parts.split_payload["friends"]) == Decimal("1000.00")


def test_telegram_confirm_with_split(ctx):
    from services import telegram_service

    code = telegram_service.generate_link_code("self")
    user = telegram_service.redeem_link_code(code.code, 444001)
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Karan"})
    cash_before = Decimal(joint.current_balance)

    payload = split_service.parse_telegram_split_clause(
        Decimal("900"), "Karan 300"
    )
    assert payload["household_share"] == Decimal("600")

    msg, _ = telegram_service.record_incoming_update(
        update_id=9101,
        message_id=91,
        telegram_user_id=444001,
        chat_id=444001,
        text="/add 900 dinner split Karan 300",
    )
    pending = telegram_service.create_pending(
        user=user,
        chat_id=444001,
        message_row=msg,
        amount=Decimal("900"),
        description="dinner",
        category_id=dining.id,
        account_id=joint.id,
        txn_date=telegram_service.today_local(),
        paid_by="self",
        split_payload=payload,
    )
    assert pending.split_json
    txn = telegram_service.confirm_pending(pending, 444001)
    db.session.refresh(joint)
    assert Decimal(joint.current_balance) == cash_before - Decimal("900")
    assert txn.household_share_amount == Decimal("600")
    assert split_service.friend_outstanding(friend.id) == Decimal("300")


def test_split_validation_mismatch(ctx):
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Amit"})

    form = MultiDict(
        [
            ("amount", "1000"),
            ("description", "Bad split"),
            ("transaction_type", "expense"),
            ("account_id", str(joint.id)),
            ("category_id", str(dining.id)),
            ("date", transaction_service._parse_date(None).isoformat()),
            ("split_enabled", "1"),
            ("split_household_share", "400"),
            ("split_friend_id", str(friend.id)),
            ("split_friend_amount", "400"),
        ]
    )
    with pytest.raises(TransactionValidationError, match="must equal"):
        transaction_service.create_transaction(form)


def test_delete_split_auto_undoes_cross_split_settlement(ctx):
    """Settlement from general page can apply across splits; delete must still undo."""
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Vikram"})

    def _split_expense(desc: str, friend_amt: str):
        form = MultiDict(
            [
                ("amount", str(int(friend_amt) * 2)),
                ("description", desc),
                ("transaction_type", "expense"),
                ("account_id", str(joint.id)),
                ("category_id", str(dining.id)),
                ("date", transaction_service._parse_date(None).isoformat()),
                ("split_enabled", "1"),
                ("split_household_share", friend_amt),
                ("split_friend_id", str(friend.id)),
                ("split_friend_amount", friend_amt),
            ]
        )
        return transaction_service.create_transaction(form)[0]

    txn_a = _split_expense("Lunch A", "300")
    _split_expense("Lunch B", "200")
    cash_before = Decimal(joint.current_balance) + Decimal("1000")

    split_service.settle_friend_share(
        friend_id=friend.id, amount=Decimal("500"), account_id=joint.id
    )
    assert split_service.friend_outstanding(friend.id) == Decimal("0")

    transaction_service.delete_transaction(txn_a)
    db.session.refresh(joint)
    assert split_service.friend_outstanding(friend.id) == Decimal("200")
    # Full settlement reversed (+500) and expense A restored (+600); expense B (-400) remains
    assert Decimal(joint.current_balance) == cash_before - Decimal("400")


def test_delete_split_auto_undoes_settlements(ctx):
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Neha"})

    form = MultiDict(
        [
            ("amount", "600"),
            ("description", "Coffee"),
            ("transaction_type", "expense"),
            ("account_id", str(joint.id)),
            ("category_id", str(dining.id)),
            ("date", transaction_service._parse_date(None).isoformat()),
            ("split_enabled", "1"),
            ("split_household_share", "300"),
            ("split_friend_id", str(friend.id)),
            ("split_friend_amount", "300"),
        ]
    )
    txn, _ = transaction_service.create_transaction(form)
    cash_before = Decimal(joint.current_balance) + Decimal("600")
    cash_after_expense = Decimal(joint.current_balance)
    split_service.settle_friend_share(
        friend_id=friend.id, amount=Decimal("300"), account_id=joint.id
    )
    db.session.refresh(joint)
    assert split_service.friend_outstanding(friend.id) == Decimal("0")
    assert Decimal(joint.current_balance) == cash_after_expense + Decimal("300")

    transaction_service.delete_transaction(txn)
    db.session.refresh(joint)
    assert Transaction.query.get(txn.id) is None
    assert split_service.friend_outstanding(friend.id) == Decimal("0")
    assert Decimal(joint.current_balance) == cash_before


def test_delete_split_with_mismatched_legacy_settlement(ctx):
    """Stale settlement rows (amount != share settled) still allow delete."""
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Abhishek"})

    form = MultiDict(
        [
            ("amount", "2000"),
            ("description", "test"),
            ("transaction_type", "expense"),
            ("account_id", str(joint.id)),
            ("category_id", str(dining.id)),
            ("date", transaction_service._parse_date(None).isoformat()),
            ("split_enabled", "1"),
            ("split_household_share", "1000"),
            ("split_friend_id", str(friend.id)),
            ("split_friend_amount", "1000"),
        ]
    )
    txn, _ = transaction_service.create_transaction(form)
    settlement = split_service.settle_friend_share(
        friend_id=friend.id, amount=Decimal("400"), account_id=joint.id
    )
    # Simulate legacy / edited state: allocation missing, share settled out of sync
    settlement.share_allocations = None
    share = settlement.share
    if share:
        share.settled_amount = Decimal("66.67")
        share.refresh_status()
    db.session.commit()

    transaction_service.delete_transaction(txn)
    assert split_service.get_split_for_transaction(txn.id) is None
    """Older settlements without share_allocations JSON can still be reversed."""
    joint, dining = _joint_and_dining(ctx)
    friend = split_service.create_friend({"name": "Legacy Pal"})

    form = MultiDict(
        [
            ("amount", "600"),
            ("description", "Old split"),
            ("transaction_type", "expense"),
            ("account_id", str(joint.id)),
            ("category_id", str(dining.id)),
            ("date", transaction_service._parse_date(None).isoformat()),
            ("split_enabled", "1"),
            ("split_household_share", "300"),
            ("split_friend_id", str(friend.id)),
            ("split_friend_amount", "300"),
        ]
    )
    txn, _ = transaction_service.create_transaction(form)
    settlement = split_service.settle_friend_share(
        friend_id=friend.id, amount=Decimal("300"), account_id=joint.id
    )
    settlement.share_allocations = None
    db.session.commit()

    transaction_service.delete_transaction(txn)
    assert split_service.friend_outstanding(friend.id) == Decimal("0")
