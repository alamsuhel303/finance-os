"""First-time setup — choose single/couple mode and enter names."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from extensions import db
from models.app_profile import AppProfile
from services import profile_service

setup_bp = Blueprint("setup", __name__)


@setup_bp.route("/setup", methods=["GET"])
def index():
    """Show the onboarding page — skip if already set up."""
    if profile_service.is_setup_complete():
        return redirect(url_for("dashboard.index"))
    return render_template("setup.html")


@setup_bp.route("/setup", methods=["POST"])
def save():
    """Save profile and seed accounts, then redirect to dashboard."""
    if profile_service.is_setup_complete():
        return redirect(url_for("dashboard.index"))

    mode = request.form.get("mode", "couple").strip().lower()
    if mode not in ("single", "couple"):
        mode = "couple"

    person1 = (request.form.get("person1_name") or "").strip()
    person2 = (request.form.get("person2_name") or "").strip()

    if not person1:
        person1 = "Person 1"
    if mode == "couple" and not person2:
        person2 = "Person 2"
    if mode == "single":
        person2 = None

    profile = AppProfile(
        mode=mode,
        person1_name=person1,
        person2_name=person2,
        is_setup_complete=True,
    )
    db.session.add(profile)
    db.session.commit()

    # Now seed accounts based on the profile
    from utils.seed import seed_database

    seed_database()

    return redirect(url_for("dashboard.index"))
