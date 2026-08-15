"""Flask app context helpers for the Telegram worker."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from flask import Flask


def create_worker_app() -> Flask:
    from app import create_app

    return create_app()


@contextmanager
def app_context(app: Flask | None = None) -> Iterator[Flask]:
    flask_app = app or create_worker_app()
    with flask_app.app_context():
        yield flask_app
