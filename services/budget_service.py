"""Budget service — monthly category envelopes vs actual spend."""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func

from extensions import db
from models import Budget, Category, Transaction
from utils.helpers import parse_amount


class BudgetValidationError(ValueError):
    pass


STATUS_OK = "ok"  # < 75%
STATUS_WATCH = "watch"  # 75–99%
STATUS_OVER = "over"  # >= 100%
STATUS_NONE = "none"  # no budget set

# Signed contribution to budget "spent": expenses +, refunds −
_BUDGET_SIGNED_AMOUNT = case(
    (Transaction.transaction_type == "refund", -Transaction.amount),
    else_=Transaction.amount,
)

# Examples that help pick a monthly household limit (match by slug or name key).
CATEGORY_BUDGET_HINTS: dict[str, str] = {
    "rent": "House rent / EMI + society — your largest fixed household bill",
    "housing": "House rent / EMI + society — your largest fixed household bill",
    "utilities": "Electricity, water, gas, Wi‑Fi, mobile, OTT for the month",
    "groceries": "Staples, kirana, BigBasket / Zepto — weekly shop × 4–5",
    "fruits-vegetables": "Fresh produce — weekly market / app order × 4–5",
    "protein-supplements": "Protein, vitamins, pharmacy nutrition — monthly pack",
    "supplements": "Protein, vitamins, pharmacy nutrition — monthly pack",
    "personal-care-household": "Haircut, salon, toiletries, detergents, cleaning — weekly care × 4",
    "personal": "Haircut, salon, toiletries, detergents, cleaning — weekly care × 4",
    "cook": "Cook salary / kitchen help — fixed monthly amount",
    "fuel-bike": "Petrol / CNG + small bike upkeep — trips × typical fill-up",
    "fuel": "Petrol / CNG + small bike upkeep — trips × typical fill-up",
    "auto-cab": "Uber, Ola, auto-rickshaw, local cabs — weekly rides × 4",
    "car": "Optional: car service, parking, FASTag (not fuel)",
    "insurance": "Health / term / vehicle — premiums due or monthly average",
    "medical": "Doctor, medicines, labs, dental — keep a buffer even if healthy",
    "gym": "Gym / yoga / sports membership for the month",
    "misc-home-buffer": "Odds & ends + cushion so the household total still holds",
    "miscellaneous": "Odds & ends + cushion so the household total still holds",
}


def budget_hint_for(category: Category | None) -> str:
    if not category:
        return ""
    slug = (getattr(category, "slug", None) or "").strip().lower()
    if slug in CATEGORY_BUDGET_HINTS:
        return CATEGORY_BUDGET_HINTS[slug]
    key = re.sub(r"[^a-z0-9]+", "-", (category.name or "").lower()).strip("-")
    return CATEGORY_BUDGET_HINTS.get(key, "")


def is_household_budget_category(category: Category | None) -> bool:
    """Shopping / Travel pots and family-support cats stay off the ~₹1.5L plan."""
    if not category:
        return False
    from utils.seed import (
        BUDGET_EXCLUDED_CATEGORY_SLUGS,
        ENVELOPE_PURPOSE_CATEGORY_SLUGS,
    )

    slug = (getattr(category, "slug", None) or "").strip().lower()
    return (
        slug not in ENVELOPE_PURPOSE_CATEGORY_SLUGS
        and slug not in BUDGET_EXCLUDED_CATEGORY_SLUGS
    )

def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return start, end


def _status(budgeted: Decimal, actual: Decimal) -> str:
    if budgeted <= 0:
        return STATUS_NONE if actual <= 0 else STATUS_OVER
    pct = float((actual / budgeted) * 100)
    if pct >= 100:
        return STATUS_OVER
    if pct >= 75:
        return STATUS_WATCH
    return STATUS_OK


def _progress_pct(budgeted: Decimal, actual: Decimal) -> float:
    if budgeted <= 0:
        return 100.0 if actual > 0 else 0.0
    return min(100.0, float((actual / budgeted) * 100))


def _prev_ym(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _month_has_budgets(year: int, month: int) -> bool:
    return (
        Budget.query.filter_by(year=year, month=month)
        .filter(Budget.amount > 0)
        .count()
        > 0
    )


def find_latest_source_budgets(
    year: int, month: int, *, lookback: int = 24
) -> tuple[list[Budget], int, int] | None:
    """Walk prior months and return the most recent month that has budgets set."""
    y, m = year, month
    for _ in range(lookback):
        y, m = _prev_ym(y, m)
        rows = (
            Budget.query.filter_by(year=y, month=m)
            .filter(Budget.amount > 0)
            .all()
        )
        if rows:
            return rows, y, m
    return None


def ensure_month_budgets(year: int, month: int) -> dict[str, Any]:
    """
    If this month has no category budgets, copy from the latest prior month.
    Returns meta for the UI (auto-copied / needs first-time setup).
    """
    if _month_has_budgets(year, month):
        return {
            "auto_copied": False,
            "needs_setup": False,
            "source_label": None,
            "copied_count": 0,
        }

    found = find_latest_source_budgets(year, month)
    if not found:
        return {
            "auto_copied": False,
            "needs_setup": True,
            "source_label": None,
            "copied_count": 0,
        }

    sources, src_year, src_month = found
    count = 0
    for src in sources:
        existing = Budget.query.filter_by(
            year=year, month=month, category_id=src.category_id
        ).first()
        if existing:
            if Decimal(existing.amount or 0) != Decimal(src.amount or 0):
                existing.amount = src.amount
                count += 1
        else:
            db.session.add(
                Budget(
                    year=year,
                    month=month,
                    category_id=src.category_id,
                    amount=src.amount,
                    notes=src.notes,
                )
            )
            count += 1
    db.session.commit()
    return {
        "auto_copied": True,
        "needs_setup": False,
        "source_label": date(src_year, src_month, 1).strftime("%B %Y"),
        "copied_count": count,
    }


def get_budget_overview(year: int | None = None, month: int | None = None) -> dict[str, Any]:
    today = date.today()
    year = year or today.year
    month = month or today.month
    start, end = _month_bounds(year, month)

    carry = ensure_month_budgets(year, month)

    categories = [
        c
        for c in Category.query.filter_by(
            is_active=True, parent_id=None, category_type="expense"
        )
        .order_by(Category.sort_order, Category.name)
        .all()
        if is_household_budget_category(c)
    ]

    budgets = {
        b.category_id: b
        for b in Budget.query.filter_by(year=year, month=month).all()
    }

    actual_rows = (
        db.session.query(
            Transaction.category_id,
            func.coalesce(func.sum(_BUDGET_SIGNED_AMOUNT), 0).label("total"),
        )
        .filter(
            Transaction.transaction_type.in_(("expense", "refund")),
            Transaction.is_excluded_from_budget.is_(False),
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.category_id.isnot(None),
        )
        .group_by(Transaction.category_id)
        .all()
    )
    actual_map = {cid: Decimal(total or 0) for cid, total in actual_rows}

    rows: list[dict[str, Any]] = []
    total_budgeted = Decimal("0")
    total_actual = Decimal("0")
    over_count = 0

    for cat in categories:
        budgeted = Decimal(budgets[cat.id].amount) if cat.id in budgets else Decimal("0")
        actual = actual_map.get(cat.id, Decimal("0"))
        remaining = budgeted - actual
        status = _status(budgeted, actual)
        if status == STATUS_OVER and budgeted > 0:
            over_count += 1

        total_budgeted += budgeted
        total_actual += actual

        rows.append(
            {
                "category": cat,
                "budget_id": budgets[cat.id].id if cat.id in budgets else None,
                "budgeted": budgeted,
                "actual": actual,
                "remaining": remaining,
                "progress": round(_progress_pct(budgeted, actual), 1),
                "status": status,
                "hint": budget_hint_for(cat),
            }
        )

    # Uncategorized expenses (net of refunds)
    uncategorized = (
        db.session.query(func.coalesce(func.sum(_BUDGET_SIGNED_AMOUNT), 0))
        .filter(
            Transaction.transaction_type.in_(("expense", "refund")),
            Transaction.is_excluded_from_budget.is_(False),
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.category_id.is_(None),
        )
        .scalar()
    )
    uncategorized = Decimal(uncategorized or 0)
    total_actual += uncategorized

    remaining_total = total_budgeted - total_actual
    overall_pct = _progress_pct(total_budgeted, total_actual)

    # Editable list: show budgeted / spent rows; rest available to add
    if carry["needs_setup"]:
        display_rows = rows
        addable_categories = []
    else:
        display_rows = [r for r in rows if r["budgeted"] > 0 or r["actual"] > 0]
        shown_ids = {r["category"].id for r in display_rows}
        addable_categories = [c for c in categories if c.id not in shown_ids]

    category_hints = {c.id: budget_hint_for(c) for c in categories}

    return {
        "year": year,
        "month": month,
        "month_label": date(year, month, 1).strftime("%B %Y"),
        "rows": rows,
        "display_rows": display_rows,
        "addable_categories": addable_categories,
        "all_expense_categories": categories,
        "category_hints": category_hints,
        "uncategorized": uncategorized,
        "total_budgeted": total_budgeted,
        "total_actual": total_actual,
        "total_remaining": remaining_total,
        "overall_progress": round(overall_pct, 1),
        "overall_status": _status(total_budgeted, total_actual),
        "over_count": over_count,
        "categories_with_budget": sum(1 for r in rows if r["budgeted"] > 0),
        "auto_copied": carry["auto_copied"],
        "needs_setup": carry["needs_setup"],
        "source_label": carry["source_label"],
        "copied_count": carry["copied_count"],
    }


def envelope_funding_gaps(
    budget_overview: dict[str, Any], envelopes_overview: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Budget limits do not fund envelopes. Flag pots where category budgets
    exist but little/no Joint cash was allocated (common: Dining → Lifestyle).
    """
    env_rows = {
        r["envelope"].id: r for r in (envelopes_overview.get("rows") or [])
    }
    by_env: dict[int, dict[str, Any]] = {}
    for row in budget_overview.get("rows") or []:
        cat = row["category"]
        env_id = getattr(cat, "envelope_id", None)
        if not env_id or Decimal(row["budgeted"] or 0) <= 0:
            continue
        env_row = env_rows.get(env_id)
        if not env_row:
            continue
        env = env_row["envelope"]
        if getattr(env, "slug", None) == "unallocated":
            continue
        bucket = by_env.setdefault(
            env_id,
            {
                "envelope": env,
                "budgeted": Decimal("0"),
                "categories": [],
                "available": Decimal(env_row.get("available") or 0),
                "balance": Decimal(env.current_balance or 0),
            },
        )
        bucket["budgeted"] += Decimal(row["budgeted"] or 0)
        bucket["categories"].append(cat.name)

    gaps: list[dict[str, Any]] = []
    for bucket in by_env.values():
        # Gap when budgeted spend plan exceeds funded pot (or pot already negative)
        shortfall = bucket["budgeted"] - bucket["available"]
        if bucket["balance"] < 0 or shortfall > 0:
            gaps.append(
                {
                    **bucket,
                    "shortfall": max(shortfall, Decimal("0") - bucket["balance"])
                    if bucket["balance"] < 0
                    else shortfall,
                }
            )
    gaps.sort(key=lambda g: g["shortfall"], reverse=True)
    return gaps


def add_budget_row(
    year: int,
    month: int,
    *,
    category_id: int | None = None,
    name: str | None = None,
    amount: Any = None,
) -> str:
    """Add (or update) a budget line by category id or typed name."""
    if month < 1 or month > 12:
        raise BudgetValidationError("Month must be between 1 and 12.")

    parsed = parse_amount(amount)
    if parsed is None or parsed <= 0:
        raise BudgetValidationError("Enter a budget amount greater than zero.")

    from utils.seed import slugify

    category = None
    typed = (name or "").strip()

    if category_id:
        category = db.session.get(Category, category_id)
        if not category or category.category_type != "expense" or not category.is_active:
            raise BudgetValidationError("Select a valid expense category.")
        if not is_household_budget_category(category):
            raise BudgetValidationError(
                f"“{category.name}” belongs on Envelopes (Shopping / Travel / Lifestyle), not household Budget."
            )
        # Optional rename when adding with a different typed name
        if typed and typed.lower() != (category.name or "").lower():
            _rename_category(category, typed)
    elif typed:
        # Match existing expense category by name (case-insensitive), else create
        category = (
            Category.query.filter(
                Category.category_type == "expense",
                Category.is_active.is_(True),
                func.lower(Category.name) == typed.lower(),
            )
            .first()
        )
        if category and not is_household_budget_category(category):
            raise BudgetValidationError(
                f"“{category.name}” belongs on Envelopes (Shopping / Travel / Lifestyle), not household Budget."
            )
        if not category:
            base_slug = slugify(typed)
            slug = base_slug
            n = 2
            while Category.query.filter_by(slug=slug).first():
                slug = f"{base_slug}-{n}"
                n += 1
            max_order = (
                db.session.query(func.coalesce(func.max(Category.sort_order), 0)).scalar()
                or 0
            )
            category = Category(
                name=typed,
                slug=slug,
                category_type="expense",
                icon="bi-tag",
                color="#94a3b8",
                is_system=False,
                sort_order=int(max_order) + 10,
            )
            db.session.add(category)
            db.session.flush()
    else:
        raise BudgetValidationError("Enter a category name (or pick one from the list).")

    budget = Budget.query.filter_by(
        year=year, month=month, category_id=category.id
    ).first()
    if budget:
        budget.amount = parsed
        action = f"Updated “{category.name}” to ₹{parsed:,.2f}."
    else:
        db.session.add(
            Budget(year=year, month=month, category_id=category.id, amount=parsed)
        )
        action = f"Added “{category.name}” with ₹{parsed:,.2f}."
    db.session.commit()
    return action


def _rename_category(category: Category, new_name: str) -> None:
    from utils.seed import slugify

    new_name = new_name.strip()
    if not new_name:
        raise BudgetValidationError("Category name cannot be empty.")
    clash = (
        Category.query.filter(
            func.lower(Category.name) == new_name.lower(),
            Category.id != category.id,
            Category.parent_id.is_(None),
        )
        .first()
    )
    if clash:
        raise BudgetValidationError(f"Another category already uses “{new_name}”.")
    category.name = new_name
    base_slug = slugify(new_name)
    slug = base_slug
    n = 2
    while True:
        other = Category.query.filter_by(slug=slug).first()
        if not other or other.id == category.id:
            break
        slug = f"{base_slug}-{n}"
        n += 1
    category.slug = slug


def rename_budget_categories(names: dict[int, Any]) -> int:
    """Rename categories from budget row name inputs. Returns count renamed."""
    touched = 0
    for category_id, raw_name in names.items():
        category = db.session.get(Category, int(category_id))
        if not category or category.category_type != "expense":
            continue
        text = str(raw_name or "").strip()
        if not text or text == category.name:
            continue
        _rename_category(category, text)
        touched += 1
    if touched:
        db.session.commit()
    return touched


def remove_budget_row(year: int, month: int, category_id: int) -> str:
    """Remove a category budget line for the month."""
    budget = Budget.query.filter_by(
        year=year, month=month, category_id=category_id
    ).first()
    category = db.session.get(Category, category_id)
    label = category.name if category else "Category"
    if not budget:
        raise BudgetValidationError(f"No budget row for {label} this month.")
    db.session.delete(budget)
    db.session.commit()
    return f"Removed “{label}” from this month’s budget."


def upsert_budgets(year: int, month: int, amounts: dict[int, Any]) -> int:
    """Create or update budgets for the given month. Returns count of rows touched."""
    if month < 1 or month > 12:
        raise BudgetValidationError("Month must be between 1 and 12.")
    if year < 2000 or year > 2100:
        raise BudgetValidationError("Year looks invalid.")

    touched = 0
    for category_id, raw_amount in amounts.items():
        category = db.session.get(Category, category_id)
        if not category or category.category_type != "expense":
            continue

        text = str(raw_amount).strip() if raw_amount is not None else ""
        if text in ("",):
            # Empty means leave unchanged / skip create
            continue

        # Allow zero to clear budget
        if text in ("0", "0.0", "0.00"):
            amount = Decimal("0")
        else:
            amount = parse_amount(text)
            if amount is None:
                raise BudgetValidationError(
                    f"Invalid amount for {category.name}."
                )

        budget = Budget.query.filter_by(
            year=year, month=month, category_id=category_id
        ).first()

        if budget:
            if Decimal(budget.amount or 0) != amount:
                budget.amount = amount
                touched += 1
        else:
            if amount == 0:
                continue
            db.session.add(
                Budget(year=year, month=month, category_id=category_id, amount=amount)
            )
            touched += 1

    db.session.commit()
    return touched


def get_category_month_activity(
    category_id: int,
    *,
    year: int | None = None,
    month: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Expense + refund transactions for one household budget category in a month."""
    category = db.session.get(Category, category_id)
    if not category or category.category_type != "expense":
        return {"category": None, "rows": [], "totals": {}}

    today = date.today()
    year = year or today.year
    month = month or today.month
    start, end = _month_bounds(year, month)
    overview = get_budget_overview(year, month)
    row = next(
        (r for r in overview["rows"] if r["category"].id == category_id), None
    )

    txns = (
        Transaction.query.filter(
            Transaction.category_id == category_id,
            Transaction.transaction_type.in_(("expense", "refund")),
            Transaction.is_excluded_from_budget.is_(False),
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .limit(limit + 1)
        .all()
    )
    truncated = len(txns) > limit
    txns = txns[:limit]
    total = sum(
        (
            -Decimal(t.amount or 0)
            if t.transaction_type == "refund"
            else Decimal(t.amount or 0)
            for t in txns
        ),
        Decimal("0"),
    )
    if truncated:
        # Prefer overview actual (full month) for the headline number
        total = Decimal(row["actual"]) if row else total

    return {
        "category": category,
        "year": year,
        "month": month,
        "month_label": start.strftime("%B %Y"),
        "rows": txns,
        "truncated": truncated,
        "budget_row": row,
        "totals": {
            "spent": Decimal(row["actual"]) if row else total,
            "budgeted": Decimal(row["budgeted"]) if row else Decimal("0"),
            "remaining": Decimal(row["remaining"]) if row else Decimal("0"),
        },
    }


def get_month_spent_activity(
    *,
    year: int | None = None,
    month: int | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    """All expenses and refunds that make up Budget Spent for the month."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    start, end = _month_bounds(year, month)
    overview = get_budget_overview(year, month)

    household_ids = {
        r["category"].id
        for r in overview["rows"]
        if r.get("category") is not None
    }

    txns = (
        Transaction.query.filter(
            Transaction.transaction_type.in_(("expense", "refund")),
            Transaction.is_excluded_from_budget.is_(False),
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    # Match overview total_actual: household budget cats + uncategorized
    rows = [
        t
        for t in txns
        if t.category_id is None or t.category_id in household_ids
    ]
    truncated = len(rows) > limit
    rows = rows[:limit]
    listed = sum(
        (
            -Decimal(t.amount or 0)
            if t.transaction_type == "refund"
            else Decimal(t.amount or 0)
            for t in rows
        ),
        Decimal("0"),
    )

    return {
        "year": year,
        "month": month,
        "month_label": start.strftime("%B %Y"),
        "rows": rows,
        "truncated": truncated,
        "listed_total": listed,
        "totals": {
            "spent": Decimal(overview["total_actual"]),
            "budgeted": Decimal(overview["total_budgeted"]),
            "remaining": Decimal(overview["total_remaining"]),
            "progress": overview["overall_progress"],
            "status": overview["overall_status"],
        },
    }


def copy_budgets_from_previous(year: int, month: int) -> int:
    """Force-copy from the latest prior month that has budgets (manual refresh)."""
    # Clear "has budgets" check by always copying from latest source
    found = find_latest_source_budgets(year, month)
    if not found:
        return 0

    sources, _, _ = found
    touched = 0
    for src in sources:
        existing = Budget.query.filter_by(
            year=year, month=month, category_id=src.category_id
        ).first()
        if existing:
            if Decimal(existing.amount or 0) != Decimal(src.amount or 0):
                existing.amount = src.amount
                touched += 1
        else:
            db.session.add(
                Budget(
                    year=year,
                    month=month,
                    category_id=src.category_id,
                    amount=src.amount,
                    notes=src.notes,
                )
            )
            touched += 1
    db.session.commit()
    return touched or len(sources)
