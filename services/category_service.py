"""Category service — create, rename, deactivate spending/income labels."""

from __future__ import annotations

import re
from typing import Any, Optional

from extensions import db
from models import Budget, Category, Envelope, Transaction


class CategoryValidationError(ValueError):
    pass


CATEGORY_TYPES = (
    "expense",
    "income",
    "investment",
    "transfer",
    "refund",
)

CATEGORY_TYPE_LABELS = {
    "expense": "Expense",
    "income": "Income",
    "investment": "Investment",
    "transfer": "Transfer",
    "refund": "Refund",
}

ICON_CHOICES = (
    "bi-tag",
    "bi-cart3",
    "bi-cup-hot",
    "bi-house",
    "bi-car-front",
    "bi-airplane",
    "bi-heart-pulse",
    "bi-bag",
    "bi-wallet2",
    "bi-graph-up-arrow",
    "bi-lightning-charge",
    "bi-people",
    "bi-person",
    "bi-stars",
    "bi-three-dots",
)

DEFAULT_COLORS = (
    "#34d399",
    "#38bdf8",
    "#fbbf24",
    "#f472b6",
    "#a78bfa",
    "#fb7185",
    "#2dd4bf",
    "#60a5fa",
    "#f87171",
    "#94a3b8",
)


def list_categories(*, active_only: bool = False) -> list[Category]:
    query = Category.query.filter_by(parent_id=None)
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Category.category_type, Category.sort_order, Category.name).all()


def get_category(category_id: int) -> Optional[Category]:
    return db.session.get(Category, category_id)


def create_category(data: dict[str, Any]) -> Category:
    category = Category()
    _populate(category, data, is_new=True)
    db.session.add(category)
    db.session.commit()
    return category


def update_category(category: Category, data: dict[str, Any]) -> Category:
    _populate(category, data, is_new=False)
    db.session.commit()
    return category


def delete_category(category: Category) -> None:
    """Hard-delete only when unused; otherwise require deactivate."""
    txn_count = Transaction.query.filter(
        (Transaction.category_id == category.id)
        | (Transaction.subcategory_id == category.id)
    ).count()
    if txn_count:
        raise CategoryValidationError(
            f"Cannot delete “{category.name}” — used on {txn_count} transaction(s). "
            "Deactivate it instead."
        )
    budget_count = Budget.query.filter_by(category_id=category.id).count()
    if budget_count:
        raise CategoryValidationError(
            f"Cannot delete “{category.name}” — it has {budget_count} budget row(s). "
            "Deactivate it instead."
        )
    child_count = Category.query.filter_by(parent_id=category.id).count()
    if child_count:
        raise CategoryValidationError(
            f"Cannot delete “{category.name}” — it has subcategories. "
            "Remove those first or deactivate."
        )
    db.session.delete(category)
    db.session.commit()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "category"


def _unique_slug(base: str, *, exclude_id: int | None = None) -> str:
    slug = _slugify(base)
    candidate = slug
    suffix = 2
    while True:
        existing = Category.query.filter_by(slug=candidate).first()
        if not existing or (exclude_id is not None and existing.id == exclude_id):
            return candidate
        candidate = f"{slug}-{suffix}"
        suffix += 1


def _populate(category: Category, data: dict[str, Any], *, is_new: bool) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise CategoryValidationError("Category name is required.")
    if len(name) > 100:
        raise CategoryValidationError("Category name is too long.")

    existing = Category.query.filter_by(name=name, parent_id=None).first()
    if existing and (is_new or existing.id != category.id):
        raise CategoryValidationError("A category with this name already exists.")

    category_type = (data.get("category_type") or "expense").strip().lower()
    if category_type not in CATEGORY_TYPES:
        raise CategoryValidationError("Invalid category type.")

    color = (data.get("color") or "#6366f1").strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise CategoryValidationError("Color must be a hex value like #34d399.")

    icon = (data.get("icon") or "bi-tag").strip()
    if icon not in ICON_CHOICES:
        icon = "bi-tag"

    sort_order = 0
    try:
        sort_order = int(data.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0

    envelope_raw = data.get("envelope_id")
    envelope_id = None
    if envelope_raw not in (None, "", "0"):
        try:
            envelope_id = int(envelope_raw)
        except (TypeError, ValueError) as exc:
            raise CategoryValidationError("Invalid envelope.") from exc
        if not Envelope.query.filter_by(id=envelope_id, is_active=True).first():
            raise CategoryValidationError("Envelope not found.")

    name_changed = is_new or category.name != name
    category.name = name
    if is_new or name_changed:
        category.slug = _unique_slug(name, exclude_id=None if is_new else category.id)
    category.category_type = category_type
    category.color = color
    category.icon = icon
    category.sort_order = sort_order
    category.envelope_id = envelope_id
    category.parent_id = None
    category.is_active = str(data.get("is_active") or "").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    if is_new:
        category.is_system = False
