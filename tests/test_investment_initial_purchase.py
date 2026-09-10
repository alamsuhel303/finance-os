"""Initial lump-sum investment purchases debit source account."""

from __future__ import annotations

from decimal import Decimal

from models import Account, Investment, Transaction
from services.investment_service import create_investment, update_investment


def _fund_bank_account() -> tuple[int, Decimal]:
    from extensions import db

    acct = Account.query.filter_by(account_type="bank").first()
    assert acct is not None
    acct.current_balance = Decimal("500000")
    db.session.commit()
    return acct.id, Decimal(acct.current_balance)


def test_fd_create_debits_source_and_logs_transaction(ctx):
    with ctx.app_context():
        acct_id, start_balance = _fund_bank_account()

        inv, debited = create_investment(
            {
                "name": "HDFC FD",
                "asset_type": "fd",
                "invested_amount": "100000",
                "current_value": "100000",
                "monthly_sip": "0",
                "source_account_id": str(acct_id),
                "owner": "joint",
            }
        )

        assert debited is True
        assert Decimal(inv.invested_amount) == Decimal("100000")
        assert Decimal(inv.current_value) == Decimal("100000")

        txn = Transaction.query.filter_by(
            investment_id=inv.id, transaction_type="investment"
        ).one()
        assert txn.skip_holding_bump is True
        assert Decimal(txn.amount) == Decimal("100000")
        assert txn.account_id == acct_id

        db_acct = Account.query.get(acct_id)
        assert Decimal(db_acct.current_balance) == start_balance - Decimal("100000")


def test_fd_edit_backfills_missing_initial_transaction(ctx):
    with ctx.app_context():
        from extensions import db

        acct_id, start_balance = _fund_bank_account()

        inv = Investment(
            name="ICICI FD",
            asset_type="fd",
            invested_amount=Decimal("50000"),
            current_value=Decimal("50000"),
            monthly_sip=Decimal("0"),
            source_account_id=acct_id,
            owner="joint",
            is_active=True,
            sip_active=True,
        )
        db.session.add(inv)
        db.session.commit()

        inv, debited = update_investment(
            inv,
            {
                "name": inv.name,
                "asset_type": "fd",
                "invested_amount": "50000",
                "current_value": "50000",
                "monthly_sip": "0",
                "source_account_id": str(acct_id),
                "owner": "joint",
                "is_active": "1",
                "sip_active": "1",
            },
        )

        assert debited is True
        assert Transaction.query.filter_by(investment_id=inv.id).count() == 1
        db_acct = Account.query.get(acct_id)
        assert Decimal(db_acct.current_balance) == start_balance - Decimal("50000")


def test_mutual_fund_create_does_not_debit_on_save(ctx):
    with ctx.app_context():
        acct_id, start_balance = _fund_bank_account()

        inv, debited = create_investment(
            {
                "name": "Existing MF",
                "asset_type": "mutual_fund",
                "invested_amount": "200000",
                "current_value": "250000",
                "monthly_sip": "5000",
                "sip_day": "5",
                "source_account_id": str(acct_id),
                "owner": "joint",
            }
        )

        assert debited is False
        assert Transaction.query.filter_by(investment_id=inv.id).count() == 0
        db_acct = Account.query.get(acct_id)
        assert Decimal(db_acct.current_balance) == start_balance
