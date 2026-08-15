"""Telegram command and callback handlers."""

from __future__ import annotations

import logging
from decimal import Decimal

from telegram import Update
from telegram.ext import ContextTypes

from extensions import db
from models import TelegramPendingTransaction
from services import envelope_service, profile_service, telegram_service
from services.telegram_service import TelegramServiceError
from telegram_bot.authorization import UNAUTHORIZED_MESSAGE, require_user
from telegram_bot import keyboards
from telegram_bot.formatting import (
    HELP_TEXT,
    PARSE_MODE,
    START_AUTHORIZED,
    bold,
    card,
    code,
    esc,
    italic,
)
from telegram_bot.parser import parse_add_command, parse_expense_text

logger = logging.getLogger(__name__)


async def _reply(update: Update, text: str, reply_markup=None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode=PARSE_MODE, reply_markup=reply_markup
    )


async def _reply_plain(update: Update, text: str, reply_markup=None) -> None:
    """Send without HTML parse mode (plain lists read better)."""
    await update.effective_message.reply_text(text, reply_markup=reply_markup)


async def _inbox(update: Update, text: str | None):
    """Record update for idempotency; return (row, is_new) or (None, False)."""
    if not update.update_id:
        return None, False
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat:
        return None, False
    return telegram_service.record_incoming_update(
        update_id=update.update_id,
        message_id=msg.message_id if msg else None,
        telegram_user_id=user.id,
        chat_id=chat.id,
        text=text,
    )


def _resolve_account(user, hint: str | None):
    if hint == "personal":
        return telegram_service.resolve_personal_account(user.owner)
    if hint == "joint":
        return envelope_service.resolve_envelope_cash_account()
    return telegram_service.resolve_default_account(user)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, update.message.text if update.message else "/start")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    telegram_service.touch_user(
        user,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
    )
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply(update, START_AUTHORIZED)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/help")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply(update, HELP_TEXT)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/status")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply(update, telegram_service.status_text())


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, update.message.text if update.message else "/link")
    if not update.effective_user:
        return
    args = context.args or []
    code_arg = args[0] if args else ""
    try:
        linked = telegram_service.redeem_link_code(
            code_arg,
            update.effective_user.id,
            username=update.effective_user.username,
            first_name=update.effective_user.first_name,
            last_name=update.effective_user.last_name,
        )
        labels = profile_service.get_owner_labels()
        who = labels.get(linked.owner, linked.owner)
        if row and is_new:
            telegram_service.mark_message(row, "processed")
        await _reply(
            update,
            card(
                "✅ Linked",
                [
                    f"Mapped to {bold(who)}",
                    "",
                    f"Try {code('/add 100 coffee')}",
                ],
            ),
        )
    except TelegramServiceError as exc:
        if row and is_new:
            telegram_service.mark_message(row, "failed", error=str(exc))
        await _reply(update, card("❌ Link failed", [esc(str(exc))]))


async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/unlink")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    try:
        telegram_service.unlink_user(update.effective_user.id)
        if row and is_new:
            telegram_service.mark_message(row, "processed")
        await _reply(
            update,
            card(
                "Unlinked",
                [
                    "This Telegram account is no longer authorized.",
                    italic("Generate a new code in Settings → Telegram to link again."),
                ],
            ),
        )
    except TelegramServiceError as exc:
        if row and is_new:
            telegram_service.mark_message(row, "failed", error=str(exc))
        await _reply(update, card("❌ Unlink failed", [esc(str(exc))]))


async def _offer_pending(update: Update, user, msg_row, parsed) -> None:
    if parsed.error:
        if msg_row:
            telegram_service.mark_message(msg_row, "failed", error=parsed.error)
        await _reply(
            update,
            card(
                "Couldn't understand",
                [
                    esc(parsed.error),
                    "",
                    f"Try {code('/add 450 dinner')}",
                ],
            ),
        )
        return

    account = _resolve_account(user, parsed.account_hint)
    paid_by = parsed.paid_by_override or user.owner
    pending = telegram_service.create_pending(
        user=user,
        chat_id=update.effective_chat.id,
        message_row=msg_row,
        amount=parsed.amount,
        description=parsed.description or "Expense",
        category_id=parsed.category_id,
        account_id=account.id if account else None,
        txn_date=parsed.txn_date or telegram_service.today_local(),
        paid_by=paid_by,
        merchant=parsed.merchant,
    )

    if not pending.category_id or parsed.category_confidence == "low":
        cats = telegram_service.expense_categories()
        text = telegram_service.pending_summary_lines(pending)
        text += f"\n\n{bold('Pick a category')}"
        await _reply(
            update,
            text,
            reply_markup=keyboards.category_pick_keyboard(pending.id, cats),
        )
        return

    await _reply(
        update,
        telegram_service.pending_summary_lines(pending),
        reply_markup=keyboards.confirm_keyboard(pending.id),
    )


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text if update.message else ""
    row, is_new = await _inbox(update, text)
    if row and not is_new:
        return
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    telegram_service.touch_user(
        user,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
    )
    args = " ".join(context.args or [])
    parsed = parse_add_command(args)
    await _offer_pending(update, user, row, parsed)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return

    row, is_new = await _inbox(update, text)
    if row and not is_new:
        # Allow edit follow-ups even on duplicate? No — edit uses new messages with new update_ids
        return

    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return

    # Edit-field follow-up
    pending = telegram_service.get_active_pending_for_user(user.telegram_user_id)
    if pending and pending.edit_field:
        await _apply_edit_text(update, user, pending, text)
        if row and is_new:
            telegram_service.mark_message(row, "processed")
        return

    telegram_service.touch_user(
        user,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
    )
    parsed = parse_expense_text(text)
    await _offer_pending(update, user, row, parsed)


async def _apply_edit_text(update, user, pending, text: str) -> None:
    field = pending.edit_field
    pending.edit_field = None
    try:
        if field == "amount":
            pending.amount = Decimal(text.replace(",", "").replace("₹", "").strip())
            if pending.amount <= 0:
                raise ValueError("Amount must be > 0")
        elif field == "description":
            pending.description = text.strip()[:255]
        elif field == "date":
            from datetime import date as date_cls

            pending.transaction_date = date_cls.fromisoformat(text.strip())
        else:
            await _reply(update, card("Edit", ["Unknown edit field."]))
            db.session.commit()
            return
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        pending = telegram_service.get_pending(pending.id)
        if pending:
            pending.edit_field = None
            db.session.commit()
        await _reply(update, card("❌ Could not update", [esc(str(exc))]))
        return

    pending = telegram_service.get_pending(pending.id)
    await _reply(
        update,
        telegram_service.pending_summary_lines(pending),
        reply_markup=keyboards.confirm_keyboard(pending.id),
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/today")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    txns = telegram_service.list_today_expenses()
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply(update, telegram_service.today_expenses_text(txns))


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/recent")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    txns = telegram_service.list_recent_expenses()
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply(update, telegram_service.recent_expenses_text(txns))


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/month")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply(update, telegram_service.month_summary_text())


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/budget")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply_plain(update, telegram_service.budget_summary_text())


async def cmd_envelopes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/envelopes")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    await _reply_plain(update, telegram_service.envelopes_summary_text())


async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    row, is_new = await _inbox(update, "/undo")
    user = require_user(update.effective_user.id) if update.effective_user else None
    if not user:
        if row and is_new:
            telegram_service.mark_message(row, "ignored")
        await _reply(update, UNAUTHORIZED_MESSAGE)
        return
    txn = telegram_service.last_telegram_transaction_for_user(user.telegram_user_id)
    if row and is_new:
        telegram_service.mark_message(row, "processed")
    if not txn:
        await _reply(
            update,
            card("Undo", [italic("No recent Telegram transaction to undo.")]),
        )
        return
    await _reply(
        update,
        telegram_service.undo_preview_text(txn),
        reply_markup=keyboards.undo_keyboard(txn.id),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not query.from_user:
        return
    await query.answer()
    data = query.data
    tg_id = query.from_user.id
    user = require_user(tg_id)
    if not user:
        await _callback_reply(query, UNAUTHORIZED_MESSAGE)
        return

    try:
        if data == "txn:undokeep":
            await _callback_reply(query, card("Kept", [italic("No changes.")]))
            return

        parts = data.split(":")
        if len(parts) < 2 or parts[0] != "txn":
            return

        action = parts[1]

        if action == "undo" and len(parts) == 3:
            txn_id = int(parts[2])
            from models import Transaction

            txn = db.session.get(Transaction, txn_id)
            if not txn:
                await _callback_reply(query, card("Undo", ["Transaction not found."]))
                return
            telegram_service.undo_telegram_transaction(txn, tg_id)
            await _callback_reply(
                query, card("↩️ Undone", [italic("Transaction removed.")])
            )
            return

        if len(parts) < 3:
            return

        if action == "confirm":
            pending = telegram_service.get_pending(int(parts[2]))
            txn = telegram_service.confirm_pending(pending, tg_id)
            await _callback_reply(query, telegram_service.success_added_text(txn))
            return

        if action == "cancel":
            pending = telegram_service.get_pending(int(parts[2]))
            if not pending:
                await _callback_reply(query, card("Cancel", ["Draft not found."]))
                return
            telegram_service.cancel_pending(pending, tg_id)
            await _callback_reply(
                query, card("❌ Cancelled", [italic("Draft discarded.")])
            )
            return

        if action == "editmenu":
            pending = telegram_service.require_editable_pending(int(parts[2]), tg_id)
            await _callback_reply(
                query,
                card("Edit", [italic("What do you want to change?")]),
                reply_markup=keyboards.edit_menu_keyboard(pending.id),
            )
            return

        if action == "back":
            pending = telegram_service.require_editable_pending(int(parts[2]), tg_id)
            pending.edit_field = None
            db.session.commit()
            await _callback_reply(
                query,
                telegram_service.pending_summary_lines(pending),
                reply_markup=keyboards.confirm_keyboard(pending.id),
            )
            return

        if action == "edit" and len(parts) == 4:
            field, pending_id = parts[2], int(parts[3])
            pending = telegram_service.require_editable_pending(pending_id, tg_id)
            if field == "category":
                cats = telegram_service.expense_categories()
                await _callback_reply(
                    query,
                    card("Category", [italic("Choose category (name · envelope):")]),
                    reply_markup=keyboards.category_keyboard(pending.id, cats),
                )
                return
            if field == "account":
                accs = telegram_service.spending_accounts()
                await _callback_reply(
                    query,
                    card("Account", [italic("Choose account:")]),
                    reply_markup=keyboards.account_keyboard(pending.id, accs),
                )
                return
            pending.edit_field = field
            db.session.commit()
            prompts = {
                "amount": f"Send the new amount (e.g. {code('450')})",
                "description": "Send the new description",
                "date": f"Send the date as {code('YYYY-MM-DD')}",
            }
            await _callback_reply(
                query,
                card("Edit", [prompts.get(field, "Send the new value")]),
            )
            return

        if action == "setcat" and len(parts) == 4:
            pending_id, cat_id = int(parts[2]), int(parts[3])
            pending = telegram_service.require_editable_pending(pending_id, tg_id)
            pending.category_id = cat_id
            pending.edit_field = None
            db.session.commit()
            pending = telegram_service.get_pending(pending_id)
            await _callback_reply(
                query,
                telegram_service.pending_summary_lines(pending),
                reply_markup=keyboards.confirm_keyboard(pending.id),
            )
            return

        if action == "setacc" and len(parts) == 4:
            pending_id, acc_id = int(parts[2]), int(parts[3])
            pending = telegram_service.require_editable_pending(pending_id, tg_id)
            pending.account_id = acc_id
            pending.edit_field = None
            db.session.commit()
            pending = telegram_service.get_pending(pending_id)
            await _callback_reply(
                query,
                telegram_service.pending_summary_lines(pending),
                reply_markup=keyboards.confirm_keyboard(pending.id),
            )
            return

    except TelegramServiceError as exc:
        await _callback_reply(query, card("❌ Error", [esc(str(exc))]))
    except Exception:
        logger.exception("Callback failed")
        await _callback_reply(
            query,
            card(
                "❌ Something went wrong",
                [italic("Try again — or resend the expense.")],
            ),
        )


async def _callback_reply(query, text: str, reply_markup=None) -> None:
    """Edit the callback message; if that fails, send a fresh reply so UI doesn't vanish."""
    try:
        await query.edit_message_text(
            text, parse_mode=PARSE_MODE, reply_markup=reply_markup
        )
    except Exception as exc:
        logger.warning("edit_message_text failed (%s); sending new message", exc)
        if query.message:
            await query.message.reply_text(
                text, parse_mode=PARSE_MODE, reply_markup=reply_markup
            )


def _ensure_pending_owner(pending: TelegramPendingTransaction | None, tg_id: int) -> None:
    if not pending:
        raise TelegramServiceError("Draft not found. Send the expense again.")
    telegram_service.require_editable_pending(pending.id, tg_id)