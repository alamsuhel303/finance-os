"""Refunds must restore envelopes and reduce budget actuals."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from extensions import db
from models import Account, Category
from services import budget_service, envelope_service, transaction_service


def _joint_and_category(ctx):
    joint = envelope_service.resolve_envelope_cash_account()
    assert joint is not None
    cat = (
        Category.query.filter_by(is_active=True, parent_id=None, category_type="expense")
        .filter(Category.envelope_id.isnot(None))
        .order_by(Category.sort_order)
        .first()
    )
    assert cat is not None
    env = envelope_service.get_envelope(cat.envelope_id)
    assert env is not None

    joint.current_balance = Decimal("10000")
    env.current_balance = Decimal("5000")
    db.session.commit()
    return joint, cat, env


def test_refund_restores_envelope_and_budget(ctx):
    joint, cat, env = _joint_and_category(ctx)
    today = date.today()
    start_bal = Decimal(env.current_balance or 0)
    start_cash = Decimal(joint.current_balance or 0)

    expense, _ = transaction_service.create_transaction(
        {
            "date": today.isoformat(),
            "amount": "1000",
            "description": "Test spend",
            "transaction_type": "expense",
            "category_id": str(cat.id),
            "account_id": str(joint.id),
            "paid_by": "self",
            "payment_mode": "upi",
            "envelope_id": str(env.id),
        }
    )
    assert expense.envelope_id == env.id

    env = envelope_service.get_envelope(env.id)
    joint = db.session.get(Account, joint.id)
    assert Decimal(env.current_balance or 0) == start_bal - Decimal("1000")
    assert Decimal(joint.current_balance or 0) == start_cash - Decimal("1000")

    overview_after_spend = budget_service.get_budget_overview(today.year, today.month)
    row = next(r for r in overview_after_spend["rows"] if r["category"].id == cat.id)
    actual_after_spend = Decimal(row["actual"])

    refund, _ = transaction_service.create_transaction(
        {
            "date": today.isoformat(),
            "amount": "400",
            "description": "Test refund",
            "transaction_type": "refund",
            "category_id": str(cat.id),
            "account_id": str(joint.id),
            "paid_by": "self",
            "payment_mode": "upi",
            "envelope_id": str(env.id),
        }
    )
    assert refund.envelope_id == env.id
    assert any(e.entry_type == "refund" for e in refund.envelope_entries)

    env = envelope_service.get_envelope(env.id)
    joint = db.session.get(Account, joint.id)
    assert Decimal(env.current_balance or 0) == start_bal - Decimal("600")
    assert Decimal(joint.current_balance or 0) == start_cash - Decimal("600")

    overview = budget_service.get_budget_overview(today.year, today.month)
    row = next(r for r in overview["rows"] if r["category"].id == cat.id)
    assert Decimal(row["actual"]) == actual_after_spend - Decimal("400")

    pots = envelope_service.get_envelopes_overview(today.year, today.month)
    pot_row = next(r for r in pots["rows"] if r["envelope"].id == env.id)
    assert Decimal(pot_row["balance"]) == Decimal(env.current_balance or 0)
    assert Decimal(pot_row["spent"]) >= Decimal("600")
