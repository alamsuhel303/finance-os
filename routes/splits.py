"""Friends and expense-split routes."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db
from models import Account, ExpenseSplitSettlement
from services import split_service
from services.split_service import SplitValidationError
from utils.helpers import parse_amount

splits_bp = Blueprint("splits", __name__, url_prefix="/splits")


def _parse_date(value):
    if not value:
        from datetime import date

        return date.today()
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError as exc:
        raise SplitValidationError("Invalid date. Use YYYY-MM-DD.") from exc


@splits_bp.route("/")
def index():
    overview = split_service.settlement_overview()
    return render_template(
        "splits/index.html",
        overview=overview,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Splits",
        active_nav="splits",
    )


@splits_bp.route("/friends")
def friends():
    return render_template(
        "splits/friends.html",
        friends=split_service.list_friends(active_only=False),
        balances={
            b["friend"].id: b["outstanding"] for b in split_service.friends_balances()
        },
        page_title="Friends",
        active_nav="splits",
    )


@splits_bp.route("/friends/new", methods=["GET", "POST"])
def friend_create():
    if request.method == "POST":
        try:
            friend = split_service.create_friend(request.form.to_dict())
            flash(f"Added friend “{friend.name}”.", "success")
            return redirect(url_for("splits.friends"))
        except SplitValidationError as exc:
            flash(str(exc), "danger")
    return render_template(
        "splits/friend_form.html",
        friend=None,
        form_data=request.form if request.method == "POST" else {},
        page_title="Add Friend",
        active_nav="splits",
    )


@splits_bp.route("/friends/<int:friend_id>/edit", methods=["GET", "POST"])
def friend_edit(friend_id: int):
    friend = split_service.get_friend(friend_id)
    if not friend:
        flash("Friend not found.", "danger")
        return redirect(url_for("splits.friends"))
    if request.method == "POST":
        try:
            split_service.update_friend(friend, request.form.to_dict())
            flash("Friend updated.", "success")
            return redirect(url_for("splits.friends"))
        except SplitValidationError as exc:
            flash(str(exc), "danger")
    form_data = (
        request.form
        if request.method == "POST"
        else {
            "name": friend.name,
            "notes": friend.notes or "",
            "is_active": "1" if friend.is_active else "0",
        }
    )
    return render_template(
        "splits/friend_form.html",
        friend=friend,
        form_data=form_data,
        page_title="Edit Friend",
        active_nav="splits",
    )


@splits_bp.route("/friends/<int:friend_id>/deactivate", methods=["POST"])
def friend_deactivate(friend_id: int):
    friend = split_service.get_friend(friend_id)
    if not friend:
        flash("Friend not found.", "danger")
        return redirect(url_for("splits.friends"))
    try:
        split_service.deactivate_friend(friend)
        flash(f"“{friend.name}” deactivated.", "success")
    except SplitValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("splits.friends"))


@splits_bp.route("/<int:split_id>")
def detail(split_id: int):
    split = split_service.get_split(split_id)
    if not split:
        flash("Split not found.", "danger")
        return redirect(url_for("splits.index"))
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    return render_template(
        "splits/detail.html",
        split=split,
        accounts=accounts,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Split detail",
        active_nav="splits",
    )


@splits_bp.route("/settlements/<int:settlement_id>/undo", methods=["POST"])
def settlement_undo(settlement_id: int):
    settlement = db.session.get(ExpenseSplitSettlement, settlement_id)
    if not settlement:
        flash("Settlement not found.", "danger")
        return redirect(url_for("splits.index"))
    split_id = settlement.split_id
    friend_name = settlement.friend.name if settlement.friend else "Friend"
    amount = settlement.amount
    try:
        split_service.undo_settlement(settlement)
        flash(
            f"Undid {amount} settlement from {friend_name}. Receivable restored.",
            "success",
        )
    except SplitValidationError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("splits.detail", split_id=split_id))


@splits_bp.route("/settle", methods=["GET", "POST"])
def settle():
    accounts = (
        Account.query.filter_by(is_active=True)
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    friends = [
        b for b in split_service.friends_balances() if b["outstanding"] > 0
    ]
    prefill_friend = request.args.get("friend_id", type=int)
    prefill_split = request.args.get("split_id", type=int)
    prefill_amount = request.args.get("amount")

    if request.method == "POST":
        try:
            friend_id = int(request.form.get("friend_id") or 0)
            account_id = int(request.form.get("account_id") or 0)
            amount = parse_amount(request.form.get("amount"))
            if amount is None:
                raise SplitValidationError("Enter a valid settlement amount.")
            split_id = request.form.get("split_id")
            split_id_int = int(split_id) if split_id else None
            settlement = split_service.settle_friend_share(
                friend_id=friend_id,
                amount=amount,
                account_id=account_id,
                settle_date=_parse_date(request.form.get("date")),
                notes=(request.form.get("notes") or "").strip() or None,
                split_id=split_id_int,
            )
            flash(
                f"Recorded {amount} settlement from {settlement.friend.name}.",
                "success",
            )
            return redirect(url_for("splits.index"))
        except (TypeError, ValueError) as exc:
            flash("Pick a friend and account.", "danger")
        except SplitValidationError as exc:
            flash(str(exc), "danger")

    form_data = request.form if request.method == "POST" else {
        "friend_id": prefill_friend or "",
        "split_id": prefill_split or "",
        "amount": prefill_amount or "",
        "account_id": "",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "notes": "",
    }
    return render_template(
        "splits/settle.html",
        friends=friends,
        accounts=accounts,
        form_data=form_data,
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Record settlement",
        active_nav="splits",
    )
