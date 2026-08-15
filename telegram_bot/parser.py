"""Heuristic parser for /add and free-text expenses (no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from services import profile_service, telegram_service


AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?\s*)?((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?)",
    re.IGNORECASE,
)

DATE_ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
STOP_WORDS = {
    "spent",
    "spend",
    "paid",
    "bought",
    "buy",
    "for",
    "at",
    "on",
    "the",
    "a",
    "an",
    "rs",
    "inr",
    "rupees",
    "rupee",
    "today",
    "yesterday",
    "personal",
    "joint",
    "add",
}


@dataclass
class ParseResult:
    amount: Decimal | None = None
    description: str | None = None
    merchant: str | None = None
    category_id: int | None = None
    category_confidence: str = "low"  # high | low
    account_hint: str | None = None  # personal | joint | None
    paid_by_override: str | None = None  # self | wife
    txn_date: date | None = None
    error: str | None = None
    raw_tokens: list[str] | None = None


def parse_expense_text(text: str, *, default_date: date | None = None) -> ParseResult:
    """
    Parse structured or light natural-language expense text.

    Examples:
      450 dinner
      /add 1200 groceries
      Spent 850 at Zomato for dinner
      2500 headphones personal
      500 dinner yesterday
    """
    raw = (text or "").strip()
    if raw.startswith("/"):
        parts = raw.split(maxsplit=1)
        raw = parts[1] if len(parts) > 1 else ""

    if not raw:
        return ParseResult(error="Try: /add 450 dinner")

    result = ParseResult(txn_date=default_date or telegram_service.today_local())
    lower = raw.lower()

    # Date hints
    if "yesterday" in lower:
        result.txn_date = (default_date or telegram_service.today_local()) - timedelta(days=1)
        raw = re.sub(r"\byesterday\b", " ", raw, flags=re.IGNORECASE)
    mdate = DATE_ISO_RE.search(raw)
    if mdate:
        try:
            result.txn_date = date.fromisoformat(mdate.group(1))
            raw = raw[: mdate.start()] + " " + raw[mdate.end() :]
        except ValueError:
            pass

    # Account / payer hints
    if re.search(r"\bpersonal\b", raw, re.IGNORECASE):
        result.account_hint = "personal"
        raw = re.sub(r"\bpersonal\b", " ", raw, flags=re.IGNORECASE)
    if re.search(r"\bjoint\b", raw, re.IGNORECASE):
        result.account_hint = "joint"
        raw = re.sub(r"\bjoint\b", " ", raw, flags=re.IGNORECASE)

    labels = profile_service.get_owner_labels()
    for owner, label in labels.items():
        if not label:
            continue
        # @seema or @personname
        if re.search(rf"@{re.escape(label)}\b", raw, re.IGNORECASE):
            result.paid_by_override = owner
            raw = re.sub(rf"@{re.escape(label)}\b", " ", raw, flags=re.IGNORECASE)
        if re.search(rf"\bfor\s+{re.escape(label)}\b", raw, re.IGNORECASE):
            result.paid_by_override = owner
            raw = re.sub(rf"\bfor\s+{re.escape(label)}\b", " ", raw, flags=re.IGNORECASE)

    # Amount — prefer first number that looks like money
    am = AMOUNT_RE.search(raw)
    if not am:
        return ParseResult(error="I couldn't find an amount. Try: /add 450 dinner")
    try:
        amount = Decimal(am.group(1).replace(",", ""))
    except (InvalidOperation, ValueError):
        return ParseResult(error="Invalid amount.")
    if amount <= 0:
        return ParseResult(error="Amount must be greater than zero.")
    result.amount = amount
    rest = (raw[: am.start()] + " " + raw[am.end() :]).strip()
    rest = re.sub(r"\s+", " ", rest).strip(" -·,")

    # Strip common verbs
    rest_clean = rest
    for w in ("spent", "paid", "bought", "buy"):
        rest_clean = re.sub(rf"^\s*{w}\s+", "", rest_clean, flags=re.IGNORECASE)
    rest_clean = re.sub(r"\s+at\s+", " ", rest_clean, flags=re.IGNORECASE)
    rest_clean = re.sub(r"\s+for\s+", " ", rest_clean, flags=re.IGNORECASE)
    rest_clean = re.sub(r"\s+", " ", rest_clean).strip()

    tokens = [t for t in re.split(r"\s+", rest_clean) if t]
    result.raw_tokens = tokens

    # Category from aliases
    cat = telegram_service.find_alias_in_tokens([t.lower() for t in tokens])
    if cat:
        result.category_id = cat.id
        result.category_confidence = "high"

    # Merchant / description heuristics
    # If "at X" style already flattened: first capitalized token as merchant
    desc_tokens = []
    merchant = None
    for t in tokens:
        tl = t.lower().strip(",.")
        if tl in STOP_WORDS:
            continue
        if telegram_service.resolve_category_alias(tl) and result.category_id:
            # skip alias token from description if we already mapped category
            continue
        if merchant is None and t[:1].isupper() and len(t) > 2:
            merchant = t.strip(",.")
            continue
        desc_tokens.append(t.strip(",."))

    if not desc_tokens and tokens:
        desc_tokens = [t for t in tokens if t.lower() not in STOP_WORDS]

    result.merchant = merchant
    result.description = " ".join(desc_tokens).strip() or (merchant or "Expense")
    if len(result.description) > 200:
        result.description = result.description[:200]

    return result


def parse_add_command(args: str, *, default_date: date | None = None) -> ParseResult:
    return parse_expense_text(args or "", default_date=default_date)
