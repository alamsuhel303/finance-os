"""Month checklist reminders — income, SIPs, net-worth snapshot."""

from __future__ import annotations

from datetime import date
from typing import Any

from models import NetWorthSnapshot
from services import (
    backup_service,
    insurance_service,
    investment_service,
    joint_funding_service,
    recurring_income_service,
)
from services.backup_service import BackupError


def get_month_reminders(ref: date | None = None) -> list[dict[str, Any]]:
    """
    Actionable prompts for the current month.

    Each reminder: key, severity (info|warning), title, detail, actions[{label,url,method}]
    """
    ref = ref or date.today()
    year, month = ref.year, ref.month
    this_month = ref.replace(day=1)
    reminders: list[dict[str, Any]] = []

    try:
        health = backup_service.get_backup_health()
        if health["stale"]:
            if health["has_backup"] and health["age_days"] is not None:
                detail = (
                    f"Newest backup is {health['age_days']:.0f} day(s) old "
                    f"(limit {health['max_age_days']}). Also copy "
                    f"{health['backup_dir']} off this Mac."
                )
            else:
                detail = (
                    f"No local backup yet. Create one in Settings, then copy "
                    f"{health['backup_dir']} (or {health['db_path']}) off this Mac."
                )
            reminders.append(
                {
                    "key": "backup_stale",
                    "severity": "warning",
                    "title": "Backup your data",
                    "detail": detail,
                    "actions": [
                        {
                            "label": "Create backup",
                            "url": "settings.backup",
                            "method": "POST",
                        },
                        {
                            "label": "Settings",
                            "url": "settings.index",
                            "method": "GET",
                        },
                    ],
                }
            )
    except BackupError:
        pass

    income = recurring_income_service.get_month_status(year, month)
    if income["plan_count"] == 0:
        reminders.append(
            {
                "key": "income_setup",
                "severity": "info",
                "title": "Set up recurring income",
                "detail": "Add salary templates so Monthly Income can be posted each month.",
                "actions": [
                    {
                        "label": "Add salary template",
                        "url": "settings.recurring_income_create",
                        "method": "GET",
                    }
                ],
            }
        )
    elif income["ready_count"] > 0:
        reminders.append(
            {
                "key": "income_post",
                "severity": "warning",
                "title": f"Post income for {income['label']}",
                "detail": (
                    f"{income['ready_count']} salary/credit"
                    f"{'s' if income['ready_count'] != 1 else ''} ready"
                    f" · {float(income['ready_total']):,.0f}"
                ),
                "actions": [
                    {
                        "label": "Post income",
                        "url": "settings.post_recurring_income",
                        "method": "POST",
                    }
                ],
            }
        )

    joint = joint_funding_service.get_month_status(year, month)
    if joint["plan_configured"] and joint["ready_count"] > 0:
        parts = [
            f"{row['label']} {float(row['amount']):,.0f}"
            for row in joint["rows"]
            if row["status"] == "ready"
        ]
        reminders.append(
            {
                "key": "joint_fund",
                "severity": "warning",
                "title": f"Fund Joint for {joint['label']}",
                "detail": " · ".join(parts) if parts else f"{float(joint['ready_total']):,.0f} ready",
                "actions": [
                    {
                        "label": "Post Fund Joint",
                        "url": "settings.post_joint_funding",
                        "method": "POST",
                    },
                    {
                        "label": "Edit plan",
                        "url": "settings.joint_funding_edit",
                        "method": "GET",
                    },
                ],
            }
        )

    sip = investment_service.get_sip_month_status(year, month)
    if sip["ready_count"] > 0:
        reminders.append(
            {
                "key": "sip_post",
                "severity": "warning",
                "title": f"Post SIPs for {sip['label']}",
                "detail": (
                    f"{sip['ready_count']} SIP"
                    f"{'s' if sip['ready_count'] != 1 else ''} ready"
                    f" · {float(sip['ready_total']):,.0f}"
                ),
                "actions": [
                    {
                        "label": "Post SIPs",
                        "url": "investments.post_sips",
                        "method": "POST",
                    },
                    {
                        "label": "Review",
                        "url": "investments.index",
                        "method": "GET",
                    },
                ],
            }
        )

    epf = investment_service.get_epf_month_status(year, month)
    if epf["ready_count"] > 0:
        reminders.append(
            {
                "key": "epf_post",
                "severity": "warning",
                "title": f"Post EPF for {epf['label']}",
                "detail": (
                    f"{epf['ready_count']} EPF ready · {float(epf['ready_total']):,.0f}"
                    " · salary deduction (no bank debit)"
                ),
                "actions": [
                    {
                        "label": "Post EPF",
                        "url": "investments.post_epf",
                        "method": "POST",
                    },
                    {
                        "label": "Review",
                        "url": "investments.index",
                        "method": "GET",
                    },
                ],
            }
        )

    snap = NetWorthSnapshot.query.filter_by(snapshot_date=this_month).first()
    if not snap:
        reminders.append(
            {
                "key": "nw_snapshot",
                "severity": "info",
                "title": "Record net worth snapshot",
                "detail": f"Lock {this_month.strftime('%B %Y')} so growth charts stay accurate.",
                "actions": [
                    {
                        "label": "Record snapshot",
                        "url": "networth.snapshot",
                        "method": "POST",
                    },
                    {
                        "label": "Net Worth",
                        "url": "networth.index",
                        "method": "GET",
                    },
                ],
            }
        )

    overview = insurance_service.get_overview()
    if overview["due_soon_count"]:
        names = ", ".join(p.name for p in overview["due_soon"][:3])
        reminders.append(
            {
                "key": "insurance_renewal",
                "severity": "warning",
                "title": "Insurance renewals due",
                "detail": f"{overview['due_soon_count']} polic{'ies' if overview['due_soon_count'] != 1 else 'y'}: {names}",
                "actions": [
                    {
                        "label": "Open Insurance",
                        "url": "insurance.index",
                        "method": "GET",
                    }
                ],
            }
        )

    return reminders
