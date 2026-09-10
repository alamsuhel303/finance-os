"""Mutual fund NAV helpers via free mfapi.in (AMFI data)."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import requests

from extensions import db
from models import Investment

logger = logging.getLogger(__name__)

MFAPI_BASE = "https://api.mfapi.in"
AMFI_NAVALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
NAV_ASSET_TYPES = frozenset({"mutual_fund", "sip"})
# (connect, read) seconds — mfapi often hangs; don't block the UI for 20s per fund
MFAPI_TIMEOUT = (4, 10)
MFAPI_BATCH_TIMEOUT = (3, 6)
MFAPI_BATCH_RETRIES = 1
REQUEST_PAUSE_SEC = 0.15
_HTTP_HEADERS = {
    "User-Agent": "FinanceOS/1.0 (+local; mfapi client)",
    "Accept": "application/json",
}

# Reuse TCP connections across holdings in one refresh batch
_mfapi_session: requests.Session | None = None


class NavServiceError(ValueError):
    pass


def _reset_mfapi_session() -> None:
    global _mfapi_session
    if _mfapi_session is not None:
        try:
            _mfapi_session.close()
        except Exception:
            pass
    _mfapi_session = None


def _mfapi_session_get() -> requests.Session:
    global _mfapi_session
    if _mfapi_session is None:
        _mfapi_session = requests.Session()
        _mfapi_session.headers.update(_HTTP_HEADERS)
    return _mfapi_session


def _mfapi_get(
    path: str,
    *,
    params: dict | None = None,
    timeout: float | tuple[float, float] = MFAPI_TIMEOUT,
    retries: int = 2,
) -> Any:
    """GET JSON from mfapi.in with retries and clearer network errors."""
    url = f"{MFAPI_BASE}{path}"
    session = _mfapi_session_get()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code in (502, 503, 504) and attempt < retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise NavServiceError(
                "Fund API timed out. Try again in a moment."
            ) from exc
        except requests.ConnectionError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
                continue
            raise NavServiceError(
                "Cannot reach fund API (api.mfapi.in). Check internet / restart the app."
            ) from exc
        except requests.HTTPError as exc:
            last_exc = exc
            if (
                exc.response is not None
                and exc.response.status_code in (502, 503, 504)
                and attempt < retries
            ):
                time.sleep(0.25 * (attempt + 1))
                continue
            logger.warning("mfapi request failed: %s %s", url, exc)
            raise NavServiceError("Could not reach fund API. Try again.") from exc
        except ValueError as exc:
            logger.warning("mfapi bad JSON: %s %s", url, exc)
            raise NavServiceError("Could not reach fund API. Try again.") from exc
    if last_exc:
        raise NavServiceError("Could not reach fund API. Try again.") from last_exc
    raise NavServiceError("Could not reach fund API. Try again.")


def _parse_nav_payload(payload: dict[str, Any], code: str) -> dict[str, Any]:
    data = payload.get("data") or []
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


def fetch_latest_nav(scheme_code: str, *, batch: bool = False) -> dict[str, Any]:
    """
    Fetch latest NAV for an AMFI scheme code.
    Returns {scheme_code, scheme_name, nav, nav_date}.

    batch=True: one fast call to /mf/{code} (used by Refresh values — avoids
    slow /latest timeouts when mfapi is flaky).
    """
    code = (scheme_code or "").strip()
    if not code.isdigit():
        raise NavServiceError("Scheme code must be a numeric AMFI code.")

    if batch:
        payload = _mfapi_get(
            f"/mf/{code}",
            timeout=MFAPI_BATCH_TIMEOUT,
            retries=MFAPI_BATCH_RETRIES,
        )
        return _parse_nav_payload(payload, code)

    payload: dict[str, Any] | None = None
    try:
        payload = _mfapi_get(f"/mf/{code}/latest")
        data = payload.get("data") or []
        if data:
            return _parse_nav_payload(payload, code)
    except NavServiceError:
        logger.debug("mfapi /latest failed for %s — trying full history", code)

    payload = _mfapi_get(f"/mf/{code}")
    return _parse_nav_payload(payload, code)


def refresh_investment(
    inv: Investment, *, quote: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Update one holding from latest NAV. Returns result meta."""
    if not inv.scheme_code:
        raise NavServiceError(f"“{inv.name}” has no scheme code.")
    code = (inv.scheme_code or "").strip()
    if not code.isdigit():
        raise NavServiceError(
            f"“{inv.name}” has scheme code “{code}” — must be a numeric AMFI code."
        )

    if quote is None:
        quote = fetch_latest_nav(code)
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
    """Refresh every active holding that has a numeric AMFI scheme_code."""
    _reset_mfapi_session()
    started = time.monotonic()
    holdings = (
        Investment.query.filter(
            Investment.is_active.is_(True),
            Investment.scheme_code.isnot(None),
            Investment.scheme_code != "",
            Investment.asset_type != "gold",
        )
        .order_by(Investment.name)
        .all()
    )
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    nav_cache: dict[str, dict[str, Any] | None] = {}
    nav_errors: dict[str, str] = {}
    fetch_count = 0
    amfi_bulk: dict[str, dict[str, Any]] = {}
    try:
        amfi_bulk = fetch_amfi_nav_bulk()
    except NavServiceError as exc:
        logger.warning("AMFI bulk NAV unavailable, falling back to mfapi: %s", exc)

    needed_codes = {
        (inv.scheme_code or "").strip()
        for inv in holdings
        if (inv.scheme_code or "").strip().isdigit()
    }
    if amfi_bulk:
        for code in needed_codes:
            if code in amfi_bulk:
                nav_cache[code] = amfi_bulk[code]

    def _quote_for(code: str) -> dict[str, Any]:
        nonlocal fetch_count
        if code in nav_errors:
            raise NavServiceError(nav_errors[code])
        if code in nav_cache and nav_cache[code] is not None:
            return nav_cache[code]
        if fetch_count > 0 and REQUEST_PAUSE_SEC > 0:
            time.sleep(REQUEST_PAUSE_SEC)
        fetch_count += 1
        try:
            quote = fetch_latest_nav(code, batch=True)
            nav_cache[code] = quote
            return quote
        except NavServiceError as exc:
            nav_errors[code] = str(exc)
            raise

    for inv in holdings:
        code = (inv.scheme_code or "").strip()
        if not code.isdigit():
            continue
        if inv.asset_type not in NAV_ASSET_TYPES and inv.asset_type != "other":
            pass
        try:
            result = refresh_investment(inv, quote=_quote_for(code))
            if result["method"] == "nav_only":
                skipped.append(
                    f"{inv.name}: saved NAV {result['nav']} but no units "
                    "(set units for exact value, or refresh again later to ratio-update)"
                )
            else:
                updated.append(inv.name)
        except NavServiceError as exc:
            msg = str(exc)
            if inv.name and inv.name not in msg:
                msg = f"{inv.name}: {msg}"
            if msg not in errors:
                errors.append(msg)
        except Exception as exc:  # noqa: BLE001 — isolate per-holding failures
            logger.exception("NAV refresh failed for %s", inv.id)
            errors.append(f"{inv.name}: {exc}")

    elapsed = round(time.monotonic() - started, 1)
    amfi_hits = len([c for c in needed_codes if c in amfi_bulk])
    logger.info(
        "NAV batch refresh: %s schemes (%s from AMFI bulk, %s mfapi), "
        "%s updated, %s errors, %.1fs",
        len(needed_codes),
        amfi_hits,
        fetch_count,
        len(updated),
        len(errors),
        elapsed,
    )

    return {
        "updated": updated,
        "updated_count": len(updated),
        "skipped": skipped,
        "errors": errors,
        "eligible_count": sum(
            1 for inv in holdings if (inv.scheme_code or "").strip().isdigit()
        ),
        "scheme_count": len(needed_codes),
        "amfi_bulk_count": amfi_hits,
        "mfapi_fetch_count": fetch_count,
        "elapsed_sec": elapsed,
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
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def fetch_amfi_nav_bulk() -> dict[str, dict[str, Any]]:
    """
    Download AMFI's daily NAVAll.txt once and index by scheme code.
    Much more reliable than per-scheme mfapi calls when refreshing many funds.
    """
    session = _mfapi_session_get()
    try:
        resp = session.get(
            AMFI_NAVALL_URL,
            timeout=(4, 30),
            headers={
                "User-Agent": _HTTP_HEADERS["User-Agent"],
                "Accept": "text/plain,*/*",
            },
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("AMFI NAVAll download failed: %s", exc)
        raise NavServiceError("Could not download AMFI NAV file.") from exc

    quotes: dict[str, dict[str, Any]] = {}
    for raw_line in resp.text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Open Ended") or ";" not in line:
            continue
        parts = line.split(";")
        if len(parts) < 8:
            continue
        code = parts[0].strip()
        if not code.isdigit():
            continue
        try:
            nav = _parse_nav(parts[6].strip())
        except NavServiceError:
            continue
        nav_date = _parse_nav_date(parts[7].strip())
        quotes[code] = {
            "scheme_code": code,
            "scheme_name": parts[3].strip(),
            "nav": nav,
            "nav_date": nav_date,
        }
    if not quotes:
        raise NavServiceError("AMFI NAV file was empty or unreadable.")
    logger.info("AMFI NAVAll loaded %s scheme codes", len(quotes))
    return quotes
