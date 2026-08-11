"""Mutual fund NAV helpers via free mfapi.in (AMFI data)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests

from extensions import db
from models import Investment

logger = logging.getLogger(__name__)

MFAPI_BASE = "https://api.mfapi.in"
NAV_ASSET_TYPES = frozenset({"mutual_fund", "sip"})
REQUEST_TIMEOUT = 15
_HTTP_HEADERS = {
    "User-Agent": "FinanceOS/1.0 (+local; mfapi client)",
    "Accept": "application/json",
}


class NavServiceError(ValueError):
    pass


def _mfapi_get(path: str, *, params: dict | None = None) -> Any:
    """GET JSON from mfapi.in with clearer network errors."""
    url = f"{MFAPI_BASE}{path}"
    try:
        resp = requests.get(
            url,
            params=params,
            headers=_HTTP_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout as exc:
        raise NavServiceError("Fund API timed out. Try again in a moment.") from exc
    except requests.ConnectionError as exc:
        raise NavServiceError(
            "Cannot reach fund API (api.mfapi.in). Check internet / restart the app."
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        logger.warning("mfapi request failed: %s %s", url, exc)
        raise NavServiceError("Could not reach fund API. Try again.") from exc


def search_schemes(query: str, *, limit: int = 12) -> list[dict[str, Any]]:
    """Search AMFI schemes by name. Returns [{scheme_code, scheme_name}, ...]."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    payload = _mfapi_get("/mf/search", params={"q": q})

    if not isinstance(payload, list):
        return []

    results = []
    for row in payload[:limit]:
        code = row.get("schemeCode") or row.get("scheme_code")
        name = row.get("schemeName") or row.get("scheme_name")
        if code and name:
            results.append({"scheme_code": str(code), "scheme_name": str(name)})
    return results


def fetch_latest_nav(scheme_code: str) -> dict[str, Any]:
    """
    Fetch latest NAV for an AMFI scheme code.
    Returns {scheme_code, scheme_name, nav, nav_date}.
    """
    code = (scheme_code or "").strip()
    if not code.isdigit():
        raise NavServiceError("Scheme code must be a numeric AMFI code.")

    payload = _mfapi_get(f"/mf/{code}/latest")
    data = payload.get("data") or []
    if not data:
        # Fallback: full history endpoint, first row is latest
        try:
            payload = _mfapi_get(f"/mf/{code}")
            data = payload.get("data") or []
        except NavServiceError as exc:
            raise NavServiceError(f"No NAV data for scheme {code}.") from exc

    if not data:
        raise NavServiceError(f"No NAV data for scheme {code}.")

    latest = data[0]
    nav = _parse_nav(latest.get("nav"))
    nav_date = _parse_nav_date(latest.get("date"))
    meta = payload.get("meta") or {}
    return {
        "scheme_code": code,
        "scheme_name": meta.get("scheme_name") or meta.get("schemeName") or "",
        "nav": nav,
        "nav_date": nav_date,
    }


def refresh_investment(inv: Investment) -> dict[str, Any]:
    """Update one holding from latest NAV. Returns result meta."""
    if not inv.scheme_code:
        raise NavServiceError(f"“{inv.name}” has no scheme code.")

    quote = fetch_latest_nav(inv.scheme_code)
    nav = quote["nav"]
    old_nav = Decimal(inv.last_nav) if inv.last_nav is not None else None
    units = Decimal(inv.units or 0)
    old_current = Decimal(inv.current_value or 0)
    method = "none"

    if units > 0:
        new_current = (units * nav).quantize(Decimal("0.01"))
        method = "units"
    elif old_nav and old_nav > 0 and old_current > 0:
        new_current = (old_current * (nav / old_nav)).quantize(Decimal("0.01"))
        method = "ratio"
    else:
        new_current = old_current
        method = "nav_only"

    inv.current_value = new_current
    inv.last_nav = nav
    inv.last_nav_date = quote["nav_date"]
    db.session.commit()

    return {
        "id": inv.id,
        "name": inv.name,
        "nav": nav,
        "nav_date": quote["nav_date"],
        "scheme_name": quote["scheme_name"],
        "old_current": old_current,
        "new_current": new_current,
        "method": method,
        "units": units,
    }


def refresh_all_nav_holdings() -> dict[str, Any]:
    """Refresh every active holding that has a scheme_code."""
    holdings = (
        Investment.query.filter(
            Investment.is_active.is_(True),
            Investment.scheme_code.isnot(None),
            Investment.scheme_code != "",
        )
        .order_by(Investment.name)
        .all()
    )
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for inv in holdings:
        if inv.asset_type not in NAV_ASSET_TYPES and inv.asset_type != "other":
            # Still allow refresh if they set a scheme code on MF-like holdings
            pass
        try:
            result = refresh_investment(inv)
            if result["method"] == "nav_only":
                skipped.append(
                    f"{inv.name}: saved NAV {result['nav']} but no units "
                    "(set units for exact value, or refresh again later to ratio-update)"
                )
            else:
                updated.append(inv.name)
        except NavServiceError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001 — isolate per-holding failures
            logger.exception("NAV refresh failed for %s", inv.id)
            errors.append(f"{inv.name}: {exc}")

    return {
        "updated": updated,
        "updated_count": len(updated),
        "skipped": skipped,
        "errors": errors,
        "eligible_count": len(holdings),
    }


def accrue_units_from_purchase(inv: Investment, amount: Decimal) -> Optional[Decimal]:
    """
    When a SIP/contribution is posted, add units = amount / NAV if scheme known.
    Returns units added, or None if not applicable.
    """
    if not inv.scheme_code or amount <= 0:
        return None
    if inv.asset_type not in NAV_ASSET_TYPES:
        return None
    try:
        quote = fetch_latest_nav(inv.scheme_code)
    except NavServiceError:
        logger.warning("Could not accrue units for %s — NAV fetch failed", inv.name)
        return None

    nav = quote["nav"]
    if nav <= 0:
        return None
    added = (amount / nav).quantize(Decimal("0.0001"))
    inv.units = Decimal(inv.units or 0) + added
    inv.last_nav = nav
    inv.last_nav_date = quote["nav_date"]
    # Align current to units × NAV after cash bump already added amount
    inv.current_value = (Decimal(inv.units or 0) * nav).quantize(Decimal("0.01"))
    return added


def _parse_nav(value) -> Decimal:
    try:
        nav = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise NavServiceError("Invalid NAV value from feed.") from exc
    if nav <= 0:
        raise NavServiceError("NAV from feed was zero or negative.")
    return nav.quantize(Decimal("0.0001"))


def _parse_nav_date(value) -> Optional[date]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
