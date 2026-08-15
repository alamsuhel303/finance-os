"""Pretty Telegram message formatting (HTML parse mode)."""

from __future__ import annotations

import html
from decimal import Decimal
from typing import Any

from telegram.constants import ParseMode


PARSE_MODE = ParseMode.HTML


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def money(amount: Decimal | float | int | None, symbol: str = "₹") -> str:
    try:
        return f"{symbol}{float(amount or 0):,.0f}"
    except (TypeError, ValueError):
        return f"{symbol}0"


def bold(text: str) -> str:
    return f"<b>{esc(text)}</b>"


def code(text: str) -> str:
    return f"<code>{esc(text)}</code>"


def italic(text: str) -> str:
    return f"<i>{esc(text)}</i>"


def divider() -> str:
    return "────────────"


def card(title: str, body_lines: list[str], footer: str | None = None) -> str:
    parts = [bold(title), divider()]
    parts.extend(body_lines)
    if footer:
        parts.append(divider())
        parts.append(footer)
    return "\n".join(parts)


START_AUTHORIZED = card(
    "Finance OS",
    [
        "Quick commands:",
        "",
        f"➕ {code('/add 450 dinner')}",
        f"📅 {code('/today')}  ·  📋 {code('/recent')}",
        f"📊 {code('/month')}  ·  💰 {code('/budget')}",
        f"📦 {code('/envelopes')}  ·  ↩️ {code('/undo')}",
        f"ℹ️ {code('/help')}",
    ],
    italic("Tip: you can also send plain text like “850 dinner”."),
)

HELP_TEXT = "\n".join(
    [
        bold("Help"),
        divider(),
        bold("Add expenses"),
        code("/add 450 dinner"),
        code("/add 1200 groceries"),
        code("/add 2500 headphones personal"),
        "",
        bold("Or plain text"),
        "• 850 dinner",
        "• Spent 850 at Zomato for dinner",
        "• 1200 groceries yesterday",
        "",
        bold("Commands"),
        f"{code('/today')} — today’s spending",
        f"{code('/recent')} — latest expenses",
        f"{code('/month')} — monthly summary",
        f"{code('/budget')} — budget vs spent",
        f"{code('/envelopes')} — envelope balances",
        f"{code('/undo')} — undo last Telegram expense",
        f"{code('/status')} — bot health",
        f"{code('/link CODE')} — link this account",
        f"{code('/unlink')} — unlink this account",
        "",
        italic("Every expense asks for Confirm before saving."),
    ]
)

UNAUTHORIZED = "\n".join(
    [
        bold("Not authorized"),
        divider(),
        "This Telegram account is not linked to Finance OS.",
        "",
        "Ask your admin to open",
        bold("Settings → Telegram"),
        "and generate a link code, then send:",
        "",
        code("/link ABC123"),
    ]
)
