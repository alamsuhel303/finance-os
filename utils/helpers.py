"""Utility helpers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Optional


def parse_amount(value) -> Optional[Decimal]:
    """Parse a user-entered amount into Decimal (> 0), or None if invalid."""
    if value is None:
        return None
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        amount = Decimal(text)
        if amount <= 0:
            return None
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_nonneg_amount(value) -> Optional[Decimal]:
    """Parse a non-negative amount (zero allowed). Empty → 0; invalid → None."""
    if value is None:
        return Decimal("0")
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return Decimal("0")
        amount = Decimal(text)
        if amount < 0:
            return None
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse_units(value) -> Optional[Decimal]:
    """Parse fund units (up to 4 decimal places). Empty → 0; invalid → None."""
    if value is None:
        return Decimal("0")
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return Decimal("0")
        amount = Decimal(text)
        if amount < 0:
            return None
        return amount.quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return None


def format_inr(amount, symbol: str = "₹") -> str:
    """Format a number in Indian grouping style: ₹1,23,456.78"""
    try:
        value = Decimal(str(amount or 0))
    except (InvalidOperation, ValueError):
        value = Decimal("0")

    sign = "-" if value < 0 else ""
    value = abs(value)
    whole, _, fraction = f"{value:.2f}".partition(".")

    if len(whole) <= 3:
        grouped = whole
    else:
        last3 = whole[-3:]
        rest = whole[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        grouped = ",".join(parts + [last3])

    return f"{sign}{symbol}{grouped}.{fraction}"
