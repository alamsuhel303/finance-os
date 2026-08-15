"""Inline keyboards for confirm / edit / undo flows."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from models import Account, Category
from services import telegram_service


def confirm_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"txn:confirm:{pending_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"txn:editmenu:{pending_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"txn:cancel:{pending_id}"),
            ]
        ]
    )


def edit_menu_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Amount", callback_data=f"txn:edit:amount:{pending_id}"),
                InlineKeyboardButton("Category", callback_data=f"txn:edit:category:{pending_id}"),
            ],
            [
                InlineKeyboardButton("Account", callback_data=f"txn:edit:account:{pending_id}"),
                InlineKeyboardButton("Date", callback_data=f"txn:edit:date:{pending_id}"),
            ],
            [
                InlineKeyboardButton("Description", callback_data=f"txn:edit:description:{pending_id}"),
            ],
            [
                InlineKeyboardButton("← Back", callback_data=f"txn:back:{pending_id}"),
            ],
        ]
    )


def category_keyboard(pending_id: int, categories: list[Category]) -> InlineKeyboardMarkup:
    """One button per category; label includes envelope pot when mapped."""
    rows: list[list[InlineKeyboardButton]] = []
    for cat in categories:
        label = telegram_service.category_button_label(cat)
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"txn:setcat:{pending_id}:{cat.id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("← Back", callback_data=f"txn:back:{pending_id}")]
    )
    return InlineKeyboardMarkup(rows)


def account_keyboard(pending_id: int, accounts: list[Account]) -> InlineKeyboardMarkup:
    rows = []
    for acc in accounts:
        rows.append(
            [
                InlineKeyboardButton(
                    acc.name[:40],
                    callback_data=f"txn:setacc:{pending_id}:{acc.id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("← Back", callback_data=f"txn:back:{pending_id}")]
    )
    return InlineKeyboardMarkup(rows)


def undo_keyboard(txn_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("↩️ Undo", callback_data=f"txn:undo:{txn_id}"),
                InlineKeyboardButton("Keep", callback_data="txn:undokeep"),
            ]
        ]
    )


def category_pick_keyboard(
    pending_id: int, categories: list[Category]
) -> InlineKeyboardMarkup:
    """Used when parse confidence is low — pick category before confirm."""
    return category_keyboard(pending_id, categories)
