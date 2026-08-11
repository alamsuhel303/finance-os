"""Month checklist — income, Joint funding, SIPs, EPF, net-worth snapshot."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from flask import Blueprint, current_app, render_template

from models import NetWorthSnapshot
from services import (
    investment_service,
    joint_funding_service,
    reminder_service,
    recurring_income_service,
)

checklist_bp = Blueprint("checklist", __name__, url_prefix="/month")


def _fmt(amount: Decimal | float | int | None) -> str:
    if amount is None:
        return "0"
    return f"{float(amount):,.0f}"


@checklist_bp.route("/")
def index():
    today = date.today()
    year, month = today.year, today.month
    this_month = today.replace(day=1)

    income = recurring_income_service.get_month_status(year, month)
    joint = joint_funding_service.get_month_status(year, month)
    sip = investment_service.get_sip_month_status(year, month)
    epf = investment_service.get_epf_month_status(year, month)
    snap = NetWorthSnapshot.query.filter_by(snapshot_date=this_month).first()
    funding_ui = joint_funding_service.funding_ui()

    steps = [
        {
            "key": "income",
            "title": "Post income",
            "blurb": "Credit salary templates",
            "detail": _income_detail(income),
            "amount": _income_amount(income),
            "status": _income_status(income),
            "actions": _income_actions(income),
        },
        {
            "key": "joint",
            "title": funding_ui["title"],
            "blurb": funding_ui["blurb"],
            "detail": _joint_detail(joint),
            "amount": _joint_amount(joint),
            "status": _joint_status(joint),
            "actions": _joint_actions(joint),
        },
        {
            "key": "sips",
            "title": "Post SIPs",
            "blurb": "Cash SIP / fund contributions from bank",
            "detail": _sip_detail(sip),
            "amount": _sip_amount(sip),
            "status": _sip_status(sip),
            "actions": _sip_actions(sip),
        },
        {
            "key": "epf",
            "title": "Post EPF",
            "blurb": "Salary deduction — no bank debit",
            "detail": _epf_detail(epf),
            "amount": _epf_amount(epf),
            "status": _epf_status(epf),
            "actions": _epf_actions(epf),
        },
        {
            "key": "snapshot",
            "title": "Net worth snapshot",
            "blurb": "Lock the month for growth charts",
            "detail": _snapshot_detail(snap, this_month),
            "amount": _snapshot_amount(snap),
            "status": "done" if snap else "ready",
            "actions": (
                [{"label": "Net Worth", "url": "networth.index", "method": "GET"}]
                if snap
                else [
                    {
                        "label": "Record snapshot",
                        "url": "networth.snapshot",
                        "method": "POST",
                    },
                    {"label": "Net Worth", "url": "networth.index", "method": "GET"},
                ]
            ),
        },
    ]
    for i, step in enumerate(steps, start=1):
        step["n"] = i

    done = sum(1 for s in steps if s["status"] == "done")
    ready = sum(1 for s in steps if s["status"] == "ready")

    return render_template(
        "checklist/index.html",
        steps=steps,
        month_label=this_month.strftime("%B %Y"),
        step_total=len(steps),
        done_count=done,
        ready_count=ready,
        reminders=reminder_service.get_month_reminders(today),
        currency=current_app.config["CURRENCY_SYMBOL"],
        page_title="Month checklist",
        active_nav="checklist",
    )


def _income_status(income: dict) -> str:
    if income["plan_count"] == 0:
        return "setup"
    if income["ready_count"] > 0:
        return "ready"
    if income["posted_count"] > 0:
        return "done"
    return "idle"


def _income_amount(income: dict) -> str | None:
    if income.get("posted_count", 0) > 0 and income.get("ready_count", 0) == 0:
        return _fmt(income.get("posted_total"))
    if income.get("ready_count", 0) > 0:
        return _fmt(income.get("ready_total"))
    return None


def _income_detail(income: dict) -> str:
    if income["plan_count"] == 0:
        return "Add salary templates in Settings first."
    if income["ready_count"] > 0:
        parts = [
            f"{row['template'].name} {_fmt(row['amount'])}"
            for row in income["rows"]
            if row["status"] == "ready"
        ]
        return "Ready: " + (" · ".join(parts) if parts else f"{_fmt(income['ready_total'])}")
    if income["posted_count"] > 0:
        parts = [
            f"{row['template'].name} {_fmt(row['amount'])}"
            for row in income["rows"]
            if row["status"] == "posted"
        ]
        return (
            f"Posted {_fmt(income.get('posted_total'))} for {income['label']}"
            + (f" — {', '.join(parts)}" if parts else "")
        )
    return "No active amounts to post."


def _income_actions(income: dict) -> list[dict]:
    if income["plan_count"] == 0:
        return [
            {
                "label": "Add salary template",
                "url": "settings.recurring_income_create",
                "method": "GET",
            }
        ]
    actions = []
    if income["ready_count"] > 0:
        actions.append(
            {
                "label": "Post income",
                "url": "settings.post_recurring_income",
                "method": "POST",
            }
        )
    actions.append({"label": "Settings", "url": "settings.index", "method": "GET"})
    return actions


def _joint_status(joint: dict) -> str:
    if not joint["plan_configured"]:
        return "setup"
    if joint["ready_count"] > 0:
        return "ready"
    if joint["posted_count"] > 0:
        return "done"
    return "idle"


def _joint_amount(joint: dict) -> str | None:
    if joint.get("posted_count", 0) > 0 and joint.get("ready_count", 0) == 0:
        return _fmt(joint.get("posted_total"))
    if joint.get("ready_count", 0) > 0:
        return _fmt(joint.get("ready_total"))
    return None


def _joint_detail(joint: dict) -> str:
    ui = joint.get("ui") or joint_funding_service.funding_ui()
    if not joint["plan_configured"]:
        return ui["setup_detail"]
    if joint["ready_count"] > 0:
        parts = [
            f"{row['label']} {_fmt(row['amount'])}"
            for row in joint["rows"]
            if row["status"] == "ready"
        ]
        return "Ready: " + (" · ".join(parts) if parts else f"{_fmt(joint['ready_total'])}")
    if joint["posted_count"] > 0:
        parts = [
            f"{row['label']} {_fmt(row['amount'])}"
            for row in joint["rows"]
            if row["status"] == "posted"
        ]
        return (
            f"Funded {_fmt(joint.get('posted_total'))} for {joint['label']}"
            + (f" — {', '.join(parts)}" if parts else "")
        )
    return "Plan active but amounts are zero."


def _joint_actions(joint: dict) -> list[dict]:
    ui = joint.get("ui") or joint_funding_service.funding_ui()
    if not joint["plan_configured"]:
        return [
            {
                "label": "Set up plan",
                "url": "settings.joint_funding_edit",
                "method": "GET",
            }
        ]
    actions = []
    if joint["ready_count"] > 0:
        actions.append(
            {
                "label": f"Post {ui['title']}",
                "url": "settings.post_joint_funding",
                "method": "POST",
            }
        )
    actions.append(
        {"label": "Edit plan", "url": "settings.joint_funding_edit", "method": "GET"}
    )
    return actions


def _sip_status(sip: dict) -> str:
    if sip.get("ready_count", 0) > 0:
        return "ready"
    if sip.get("plan_count", 0) > 0 and sip.get("ready_count", 0) == 0:
        return "done"
    if sip.get("plan_count", 0) == 0:
        return "idle"
    return "idle"


def _sip_amount(sip: dict) -> str | None:
    if sip.get("ready_count", 0) > 0:
        return _fmt(sip.get("ready_total"))
    if sip.get("posted_count", 0) > 0 and sip.get("ready_count", 0) == 0:
        return _fmt(sip.get("posted_total"))
    return None


def _sip_detail(sip: dict) -> str:
    if sip.get("ready_count", 0) > 0:
        parts = [
            f"{row['investment'].name} {_fmt(row['amount'])}"
            for row in sip["rows"]
            if row["status"] == "ready"
        ]
        return (
            f"{sip['ready_count']} SIP{'s' if sip['ready_count'] != 1 else ''} ready · "
            f"{_fmt(sip['ready_total'])}"
            + (f" — {', '.join(parts[:3])}" if parts else "")
        )
    if sip.get("plan_count", 0) == 0:
        return "No active SIPs configured — optional."
    if sip.get("posted_count", 0) > 0:
        return (
            f"Posted {_fmt(sip.get('posted_total'))} for {sip.get('label', 'this month')} "
            f"({sip['posted_count']} holding{'s' if sip['posted_count'] != 1 else ''})"
        )
    return f"SIPs not due or paused for {sip.get('label', 'this month')}."


def _sip_actions(sip: dict) -> list[dict]:
    actions = []
    if sip.get("ready_count", 0) > 0:
        actions.append(
            {"label": "Post SIPs", "url": "investments.post_sips", "method": "POST"}
        )
    actions.append({"label": "Investments", "url": "investments.index", "method": "GET"})
    return actions


def _epf_status(epf: dict) -> str:
    if epf.get("plan_count", 0) == 0:
        return "setup"
    if epf.get("ready_count", 0) > 0:
        return "ready"
    if epf.get("posted_count", 0) > 0 or epf.get("plan_count", 0) > 0:
        return "done"
    return "idle"


def _epf_amount(epf: dict) -> str | None:
    if epf.get("ready_count", 0) > 0:
        return _fmt(epf.get("ready_total"))
    if epf.get("posted_count", 0) > 0 and epf.get("ready_count", 0) == 0:
        return _fmt(epf.get("posted_total"))
    return None


def _epf_detail(epf: dict) -> str:
    if epf.get("plan_count", 0) == 0:
        return "Add an EPF holding with monthly amount + credit day on Investments."
    if epf.get("ready_count", 0) > 0:
        names = [
            f"{row['investment'].name} {_fmt(row['amount'])}"
            for row in epf["rows"]
            if row["status"] == "ready"
        ]
        return (
            f"Ready {_fmt(epf['ready_total'])} (salary deduction, no bank debit)"
            + (f" — {', '.join(names)}" if names else "")
        )
    if epf.get("posted_count", 0) > 0:
        return (
            f"Posted {_fmt(epf.get('posted_total'))} for {epf.get('label', 'this month')} "
            "(salary deduction, no bank debit)"
        )
    return f"EPF not due for {epf.get('label', 'this month')}."


def _epf_actions(epf: dict) -> list[dict]:
    if epf.get("plan_count", 0) == 0:
        return [
            {"label": "Add EPF holding", "url": "investments.create", "method": "GET"}
        ]
    actions = []
    if epf.get("ready_count", 0) > 0:
        actions.append(
            {"label": "Post EPF", "url": "investments.post_epf", "method": "POST"}
        )
    actions.append({"label": "Investments", "url": "investments.index", "method": "GET"})
    return actions


def _snapshot_detail(snap: NetWorthSnapshot | None, this_month: date) -> str:
    label = this_month.strftime("%B %Y")
    if snap:
        return f"Snapshot recorded for {label}."
    return f"Lock {label} for growth charts."


def _snapshot_amount(snap: NetWorthSnapshot | None) -> str | None:
    if not snap:
        return None
    return _fmt(snap.net_worth)
