"""Category model — hierarchical categories with optional parent."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, index=True)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=True, index=True
    )
    # expense | income | investment | transfer | refund
    category_type = db.Column(db.String(30), nullable=False, default="expense")
    icon = db.Column(db.String(50), default="bi-tag")
    color = db.Column(db.String(20), default="#6366f1")
    is_system = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    # Optional default virtual envelope for auto-spend mapping
    envelope_id = db.Column(
        db.Integer, db.ForeignKey("envelopes.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    parent = db.relationship(
        "Category",
        remote_side=[id],
        backref=db.backref("subcategories", lazy="dynamic"),
    )
    envelope = db.relationship("Envelope", back_populates="categories")
    transactions = db.relationship(
        "Transaction",
        back_populates="category",
        lazy="dynamic",
        foreign_keys="Transaction.category_id",
    )

    __table_args__ = (
        db.UniqueConstraint("name", "parent_id", name="uq_category_name_parent"),
    )

    def __repr__(self) -> str:
        return f"<Category {self.name}>"

    @property
    def is_subcategory(self) -> bool:
        return self.parent_id is not None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "parent_id": self.parent_id,
            "category_type": self.category_type,
            "icon": self.icon,
            "color": self.color,
            "is_system": self.is_system,
        }
