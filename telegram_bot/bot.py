"""Long-polling Telegram bot entrypoint."""

from __future__ import annotations

import logging
import sys

from telegram import BotCommand, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram_bot import handlers
from telegram_bot.app_context import create_worker_app

logger = logging.getLogger(__name__)


def _with_app(flask_app, coro_handler):
    async def wrapped(update: Update, context):
        with flask_app.app_context():
            return await coro_handler(update, context)

    return wrapped


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Keep polling noise down — network blips are expected on long-poll."""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning("Telegram network issue (will retry): %s", err)
        return
    logger.exception("Telegram handler error: %s", err)


def build_application(flask_app) -> Application:
    token = flask_app.config.get("TELEGRAM_BOT_TOKEN") or ""
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is empty. Set it in .env before starting the bot."
        )

    timeout = int(flask_app.config.get("TELEGRAM_POLL_TIMEOUT", 30))
    app = (
        Application.builder()
        .token(token)
        .get_updates_read_timeout(timeout + 5)
        .build()
    )
    app.bot_data["flask_app"] = flask_app

    wrap = lambda h: _with_app(flask_app, h)

    app.add_handler(CommandHandler("start", wrap(handlers.cmd_start)))
    app.add_handler(CommandHandler("help", wrap(handlers.cmd_help)))
    app.add_handler(CommandHandler("add", wrap(handlers.cmd_add)))
    app.add_handler(CommandHandler("today", wrap(handlers.cmd_today)))
    app.add_handler(CommandHandler("recent", wrap(handlers.cmd_recent)))
    app.add_handler(CommandHandler("month", wrap(handlers.cmd_month)))
    app.add_handler(CommandHandler("budget", wrap(handlers.cmd_budget)))
    app.add_handler(CommandHandler("envelopes", wrap(handlers.cmd_envelopes)))
    app.add_handler(CommandHandler("undo", wrap(handlers.cmd_undo)))
    app.add_handler(CommandHandler("status", wrap(handlers.cmd_status)))
    app.add_handler(CommandHandler("link", wrap(handlers.cmd_link)))
    app.add_handler(CommandHandler("unlink", wrap(handlers.cmd_unlink)))
    app.add_handler(CallbackQueryHandler(wrap(handlers.on_callback)))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, wrap(handlers.on_text))
    )
    app.add_error_handler(_on_error)
    return app


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Welcome"),
            BotCommand("add", "Add expense"),
            BotCommand("today", "Today's spending"),
            BotCommand("month", "Monthly summary"),
            BotCommand("budget", "Budget status"),
            BotCommand("envelopes", "Envelope balances"),
            BotCommand("recent", "Recent expenses"),
            BotCommand("undo", "Undo last Telegram expense"),
            BotCommand("help", "Help"),
            BotCommand("status", "Bot status"),
            BotCommand("link", "Link account with code"),
            BotCommand("unlink", "Unlink this account"),
        ]
    )
    flask_app = application.bot_data.get("flask_app")
    if flask_app and flask_app.config.get("TELEGRAM_NOTIFY_STARTUP"):
        logger.info("TELEGRAM_NOTIFY_STARTUP is on — not broadcasting to all chats by default")
    logger.info("Telegram bot commands registered; polling starting")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # Avoid leaking secrets / noisy transport stacks
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    flask_app = create_worker_app()
    if not flask_app.config.get("TELEGRAM_ENABLED", False):
        logger.warning(
            "TELEGRAM_ENABLED is false — starting anyway if token is set "
            "(set TELEGRAM_ENABLED=true in .env to silence this)."
        )

    application = build_application(flask_app)
    application.post_init = _post_init

    logger.info("Bot started — long polling")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
