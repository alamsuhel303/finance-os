"""Shared Flask extensions.

Keeping extensions in one module avoids circular imports between
the app factory, models, and blueprints.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
