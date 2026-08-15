"""Authorization helpers for Telegram updates."""

from __future__ import annotations

from models import TelegramUser
from services import telegram_service

UNAUTHORIZED_MESSAGE = (
    "Your Telegram account is not authorized for Finance OS.\n"
    "Ask the Finance OS administrator to authorize this Telegram account "
    "(Settings → Telegram → Link code, then send /link CODE here)."
)


def require_user(telegram_user_id: int) -> TelegramUser | None:
    return telegram_service.get_user_by_telegram_id(telegram_user_id)
