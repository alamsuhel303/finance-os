"""App profile — singleton setup: single or couple mode + person names."""

from __future__ import annotations

from datetime import datetime, timezone

from extensions import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppProfile(db.Model):
    __tablename__ = "app_profile"

    id = db.Column(db.Integer, primary_key=True)
    # "single" or "couple"
    mode = db.Column(db.String(20), nullable=False, default="couple")
    person1_name = db.Column(db.String(100), nullable=False, default="Person 1")
    person2_name = db.Column(db.String(100), nullable=True)
    is_setup_complete = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<AppProfile mode={self.mode} p1={self.person1_name}>"

    @property
    def is_couple(self) -> bool:
        return self.mode == "couple"

    @property
    def owner_labels(self) -> dict[str, str]:
        """Map owner keys to display names."""
        labels = {"self": self.person1_name}
        if self.is_couple and self.person2_name:
            labels["wife"] = self.person2_name
        return labels

    @property
    def personal_label(self) -> str:
        """For templates: 'Alex' or 'Alex / Sam'."""
        if self.is_couple and self.person2_name:
            return f"{self.person1_name} / {self.person2_name}"
        return self.person1_name
