"""Profile service — central access to app profile and owner labels."""

from __future__ import annotations

from models.app_profile import AppProfile


def get_profile() -> AppProfile | None:
    """Return the singleton AppProfile row, or None if setup hasn't run."""
    return AppProfile.query.first()


def is_setup_complete() -> bool:
    profile = get_profile()
    return profile is not None and profile.is_setup_complete


def is_couple_mode() -> bool:
    profile = get_profile()
    if not profile:
        return True  # default assumption until setup
    return profile.is_couple


def get_owner_labels() -> dict[str, str]:
    """Return {owner_key: display_name} from profile, with fallbacks."""
    profile = get_profile()
    if profile:
        return profile.owner_labels
    # Fallback before setup
    return {"self": "Person 1", "wife": "Person 2"}


def person1_name() -> str:
    profile = get_profile()
    return profile.person1_name if profile else "Person 1"


def person2_name() -> str | None:
    profile = get_profile()
    if profile and profile.is_couple:
        return profile.person2_name
    return None


def personal_label() -> str:
    """'Alex' or 'Alex / Sam' depending on mode."""
    profile = get_profile()
    if profile:
        return profile.personal_label
    return "Person 1 / Person 2"
