"""Net worth service — live aggregation + monthly snapshots."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from extensions import db
from models import Account, Investment, Liability, NetWorthSnapshot
from utils.helpers import parse_nonneg_amount


class NetWorthValidationError(ValueError):
    pass


ASSET_TYPE_COLORS = {
    "cash": "#38bdf8",
    "salary": "#38bdf8",
    "joint": "#38bdf8",
    "emergency": "#2dd4bf",
    "goal": "#a78bfa",
    "investment": "#34d399",
    "mutual_fund": "#34d399",
    "sip": "#4ade80",
    "stock": "#60a5fa",
    "rsu": "#818cf8",
    "epf": "#fbbf24",
    "fd": "#fb923c",
    "nps": "#f472b6",
    "gold": "#eab308",
    "other": "#94a3b8",
    "liability": "#fb7185",
}


# Hidden from net-worth breakdown (system / non-cash plumbing)
_HIDDEN_ACCOUNT_NAMES = {"Salary deduction (non-cash)"}


def compute_live_net_worth() -> dict[str, Any]:
    accounts = (
        Account.query.filter(Account.is_active.is_(True))
        .order_by(Account.sort_order, Account.name)
        .all()
    )
    accounts = [a for a in accounts if a.name not in _HIDDEN_ACCOUNT_NAMES]
    investments = (
        Investment.query.filter(Investment.is_active.is_(True))
        .order_by(Investment.sort_order, Investment.name)
        .all()
    )
    liabilities = (
        Liability.query.filter(Liability.is_active.is_(True))
        .order_by(Liability.name)
        .all()
    )

    cash_accounts = [a for a in accounts if a.account_type != "investment"]
    investment_accounts = [a for a in accounts if a.account_type == "investment"]

    cash_savings = sum(
        (Decimal(a.current_balance or 0) for a in cash_accounts),
        Decimal("0"),
    )
    # Investment-type accounts (e.g. Investment Account) — avoid double counting
    # if we also track holdings. Count account balance only when no investments exist,
    # otherwise prefer holdings for the investment bucket.
    investment_account_cash = sum(
        (Decimal(a.current_balance or 0) for a in investment_accounts),
        Decimal("0"),
    )
    holdings_value = sum(
        (Decimal(i.current_value or 0) for i in investments), Decimal("0")
    )
    # If holdings exist, use holdings as investments; still include investment
    # account cash only when it isn't already represented (cash waiting to invest).
    if holdings_value == 0:
        investments_total = investment_account_cash
    else:
        # Include idle cash sitting in investment account on top of holdings
        investments_total = holdings_value + investment_account_cash

    liabilities_total = sum(
        (Decimal(l.outstanding_amount or 0) for l in liabilities), Decimal("0")
    )

    total_assets = cash_savings + investments_total
    net_worth = total_assets - liabilities_total

    allocation = _build_allocation(accounts, investments, liabilities_total)

    # Breakdown UI: active + non-zero only (hides empty Emergency/Home/Travel funds)
    display_accounts = [
        a for a in cash_accounts if Decimal(a.current_balance or 0) != 0
    ]

    return {
        "cash_savings": cash_savings,
        "investments": investments_total,
        "holdings_value": holdings_value,
        "investment_account_cash": investment_account_cash,
        "liabilities": liabilities_total,
        "total_assets": total_assets,
        "net_worth": net_worth,
        "accounts": display_accounts,
        "investment_rows": investments,
        "liability_rows": liabilities,
        "allocation": allocation,
    }


def _build_allocation(
    accounts: list[Account],
    investments: list[Investment],
    liabilities_total: Decimal,
) -> list[dict[str, Any]]:
    buckets: dict[str, float] = {}

    for acc in accounts:
        if acc.account_type == "investment":
            key = "Investment Cash"
        elif acc.account_type in ("emergency", "goal"):
            key = acc.name
        else:
            key = "Cash & Bank"
        buckets[key] = buckets.get(key, 0.0) + float(acc.current_balance or 0)

    for inv in investments:
        label = {
            "mutual_fund": "Mutual Funds",
            "sip": "SIPs",
            "stock": "Stocks",
            "rsu": "RSUs",
            "epf": "EPF",
            "fd": "Fixed Deposits",
            "nps": "NPS",
            "gold": "Gold",
        }.get(inv.asset_type, "Other Investments")
        buckets[label] = buckets.get(label, 0.0) + float(inv.current_value or 0)

    if liabilities_total > 0:
        buckets["Loans (liability)"] = -float(liabilities_total)

    total_abs = sum(abs(v) for v in buckets.values()) or 1.0
    return [
        {
            "label": label,
            "value": value,
            "pct": round(abs(value) / total_abs * 100, 1),
            "color": ASSET_TYPE_COLORS.get(
                label.lower().split()[0].lower(), "#64748b"
            ),
        }
        for label, value in sorted(buckets.items(), key=lambda x: abs(x[1]), reverse=True)
        if abs(value) > 0.009
    ]


def record_snapshot(snapshot_date: date | None = None, notes: str | None = None) -> NetWorthSnapshot:
    """Upsert a snapshot for the given date (defaults to first of current month)."""
    today = date.today()
    snapshot_date = snapshot_date or today.replace(day=1)
    live = compute_live_net_worth()

    existing = NetWorthSnapshot.query.filter_by(snapshot_date=snapshot_date).first()
    if existing:
        snap = existing
    else:
        snap = NetWorthSnapshot(snapshot_date=snapshot_date)
        db.session.add(snap)

    snap.cash_savings = live["cash_savings"]
    snap.investments = live["investments"]
    snap.other_assets = Decimal("0")
    snap.liabilities = live["liabilities"]
    snap.net_worth = live["net_worth"]
    if notes:
        snap.notes = notes
    db.session.commit()
    return snap


def list_snapshots(limit: int = 24) -> list[NetWorthSnapshot]:
    return (
        NetWorthSnapshot.query.order_by(NetWorthSnapshot.snapshot_date.desc())
        .limit(limit)
        .all()
    )


def get_growth_stats(live_net_worth: Decimal) -> dict[str, Any]:
    snapshots = list_snapshots(24)
    ordered = list(reversed(snapshots))  # oldest → newest

    monthly_growth = None
    yearly_growth = None
    monthly_pct = None
    yearly_pct = None

    if len(ordered) >= 2:
        prev = Decimal(ordered[-2].net_worth or 0)
        monthly_growth = live_net_worth - prev
        monthly_pct = float((monthly_growth / prev) * 100) if prev != 0 else 0.0

    year_ago = None
    cutoff = date.today().replace(day=1)
    # Find snapshot ~12 months back
    target_month = cutoff.month - 11
    target_year = cutoff.year
    while target_month <= 0:
        target_month += 12
        target_year -= 1
    target = date(target_year, target_month, 1)
    for snap in ordered:
        if snap.snapshot_date <= target:
            year_ago = Decimal(snap.net_worth or 0)

    if year_ago is not None:
        yearly_growth = live_net_worth - year_ago
        yearly_pct = float((yearly_growth / year_ago) * 100) if year_ago != 0 else 0.0
    elif ordered:
        first = Decimal(ordered[0].net_worth or 0)
        yearly_growth = live_net_worth - first
        yearly_pct = float((yearly_growth / first) * 100) if first != 0 else 0.0

    chart = [
        {
            "label": s.snapshot_date.strftime("%b %Y"),
            "net_worth": float(s.net_worth or 0),
            "assets": float(
                Decimal(s.cash_savings or 0)
                + Decimal(s.investments or 0)
                + Decimal(s.other_assets or 0)
            ),
            "liabilities": float(s.liabilities or 0),
        }
        for s in ordered
    ]
    # Append live point if newest snapshot isn't today/this month
    this_month = date.today().replace(day=1)
    if not ordered or ordered[-1].snapshot_date < this_month:
        chart.append(
            {
                "label": this_month.strftime("%b %Y") + " (live)",
                "net_worth": float(live_net_worth),
                "assets": 0,
                "liabilities": 0,
            }
        )

    return {
        "monthly_growth": monthly_growth,
        "monthly_pct": round(monthly_pct, 1) if monthly_pct is not None else None,
        "yearly_growth": yearly_growth,
        "yearly_pct": round(yearly_pct, 1) if yearly_pct is not None else None,
        "snapshots": snapshots,
        "chart": chart,
    }


# ——— Liabilities CRUD ———


def list_liabilities(*, active_only: bool = True) -> list[Liability]:
    query = Liability.query
    if active_only:
        query = query.filter_by(is_active=True)
    return query.order_by(Liability.name).all()


def get_liability(lid: int) -> Optional[Liability]:
    return db.session.get(Liability, lid)


def create_liability(data: dict[str, Any]) -> Liability:
    item = Liability()
    _populate_liability(item, data)
    db.session.add(item)
    db.session.commit()
    return item


def update_liability(item: Liability, data: dict[str, Any]) -> Liability:
    _populate_liability(item, data)
    db.session.commit()
    return item


def delete_liability(item: Liability) -> None:
    db.session.delete(item)
    db.session.commit()


def _populate_liability(item: Liability, data: dict[str, Any]) -> None:
    name = (data.get("name") or "").strip()
    if not name:
        raise NetWorthValidationError("Liability name is required.")

    liability_type = (data.get("liability_type") or "other").strip().lower()
    if liability_type not in Liability.LIABILITY_TYPES:
        raise NetWorthValidationError("Invalid liability type.")

    amount = parse_nonneg_amount(data.get("outstanding_amount"))
    if amount is None:
        raise NetWorthValidationError("Enter a valid outstanding amount.")

    owner = (data.get("owner") or "joint").lower()
    if owner not in Liability.OWNERS:
        owner = "joint"

    rate = None
    rate_text = str(data.get("interest_rate") or "").strip()
    if rate_text:
        try:
            rate = Decimal(rate_text.replace(",", "")).quantize(Decimal("0.01"))
        except Exception as exc:
            raise NetWorthValidationError("Invalid interest rate.") from exc

    item.name = name
    item.liability_type = liability_type
    item.outstanding_amount = amount
    item.interest_rate = rate
    item.owner = owner
    item.notes = (data.get("notes") or "").strip() or None
    item.is_active = str(data.get("is_active", "1")).lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
