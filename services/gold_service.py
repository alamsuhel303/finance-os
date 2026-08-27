"""India 22K gold valuation — grams × live spot-derived rate."""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import requests

from extensions import db
from models import Investment

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
TROY_OUNCE_GRAMS = Decimal("31.1034768")
# 22 carat = 916 hallmark ≈ 91.6% of pure (24K) gold
PURITY_22K = Decimal("0.916")
GOLD_ASSET_TYPE = "gold"

_HTTP_HEADERS = {
    "User-Agent": "FinanceOS/1.0 (+local; gold price client)",
    "Accept": "application/json",
}


class GoldServiceError(ValueError):
    pass


def _get_json(url: str) -> Any:
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout as exc:
        raise GoldServiceError("Gold price API timed out. Try again.") from exc
    except requests.ConnectionError as exc:
        raise GoldServiceError(
            "Cannot reach gold price API. Check internet / try again."
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Gold price request failed: %s %s", url, exc)
        raise GoldServiceError("Could not fetch gold price. Try again.") from exc


def fetch_22k_price_per_gram() -> dict[str, Any]:
    """
    Live ₹/gram for 22K gold (metal value).

    Uses free XAU/USD spot + USD/INR FX, then applies 22K purity (0.916).
    Excludes making charges and GST — suitable for jewellery metal valuation.
    """
    spot = _get_json("https://api.gold-api.com/price/XAU")
    try:
        ounce_usd = Decimal(str(spot["price"]))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise GoldServiceError("Unexpected gold spot response.") from exc
    if ounce_usd <= 0:
        raise GoldServiceError("Invalid gold spot price.")

    fx = _get_json(
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json"
    )
    try:
        usd_inr = Decimal(str(fx["usd"]["inr"]))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise GoldServiceError("Unexpected FX response.") from exc
    if usd_inr <= 0:
        raise GoldServiceError("Invalid USD/INR rate.")

    per_gram_24k = (ounce_usd * usd_inr) / TROY_OUNCE_GRAMS
    per_gram_22k = (per_gram_24k * PURITY_22K).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    as_of = date.today()
    updated = spot.get("updatedAt") or ""
    if updated:
        try:
            as_of = datetime.fromisoformat(updated.replace("Z", "+00:00")).date()
        except ValueError:
            pass

    return {
        "price_per_gram_22k": per_gram_22k,
        "price_per_gram_24k": per_gram_24k.quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ),
        "ounce_usd": ounce_usd,
        "usd_inr": usd_inr,
        "as_of": as_of,
        "purity": "22K",
        "note": "Metal value only (spot × 0.916). Excludes making charges & GST.",
    }


def value_grams_22k(grams: Decimal, price_per_gram: Decimal | None = None) -> dict[str, Any]:
    """Compute ₹ value for grams of 22K gold."""
    grams = Decimal(grams or 0)
    if grams < 0:
        raise GoldServiceError("Grams cannot be negative.")
    quote = None
    if price_per_gram is None:
        quote = fetch_22k_price_per_gram()
        price_per_gram = quote["price_per_gram_22k"]
    else:
        price_per_gram = Decimal(price_per_gram)

    value = (grams * price_per_gram).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "grams": grams,
        "price_per_gram_22k": price_per_gram,
        "current_value": value,
        "as_of": quote["as_of"] if quote else date.today(),
        "quote": quote,
    }


def refresh_gold_holding(inv: Investment) -> dict[str, Any]:
    """Update one gold holding: current_value = grams × 22K ₹/g."""
    if inv.asset_type != GOLD_ASSET_TYPE:
        raise GoldServiceError(f"“{inv.name}” is not a gold holding.")
    grams = Decimal(inv.units or 0)
    if grams <= 0:
        raise GoldServiceError(
            f"“{inv.name}” has no grams set. Edit the holding and enter weight in grams."
        )

    priced = value_grams_22k(grams)
    old_current = Decimal(inv.current_value or 0)
    inv.current_value = priced["current_value"]
    inv.last_nav = priced["price_per_gram_22k"]
    inv.last_nav_date = priced["as_of"]
    inv.scheme_code = "22K"
    db.session.commit()

    return {
        "id": inv.id,
        "name": inv.name,
        "grams": grams,
        "price_per_gram_22k": priced["price_per_gram_22k"],
        "as_of": priced["as_of"],
        "old_current": old_current,
        "new_current": priced["current_value"],
    }


def refresh_all_gold_holdings() -> dict[str, Any]:
    """Refresh every active gold holding that has grams."""
    holdings = (
        Investment.query.filter(
            Investment.is_active.is_(True),
            Investment.asset_type == GOLD_ASSET_TYPE,
        )
        .order_by(Investment.name)
        .all()
    )
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if not holdings:
        return {
            "eligible_count": 0,
            "updated_count": 0,
            "updated": [],
            "skipped": [],
            "errors": [],
        }

    try:
        quote = fetch_22k_price_per_gram()
    except GoldServiceError as exc:
        return {
            "eligible_count": len(holdings),
            "updated_count": 0,
            "updated": [],
            "skipped": [],
            "errors": [str(exc)],
        }

    price = quote["price_per_gram_22k"]
    for inv in holdings:
        grams = Decimal(inv.units or 0)
        if grams <= 0:
            skipped.append(f"{inv.name}: no grams set")
            continue
        try:
            old = Decimal(inv.current_value or 0)
            new_val = (grams * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            inv.current_value = new_val
            inv.last_nav = price
            inv.last_nav_date = quote["as_of"]
            inv.scheme_code = "22K"
            updated.append(inv.name)
            logger.info(
                "Gold refresh %s: %sg × ₹%s = ₹%s (was ₹%s)",
                inv.name,
                grams,
                price,
                new_val,
                old,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gold refresh failed for %s", inv.id)
            errors.append(f"{inv.name}: {exc}")

    if updated:
        db.session.commit()

    return {
        "eligible_count": len(holdings),
        "updated_count": len(updated),
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "price_per_gram_22k": price,
        "as_of": quote["as_of"],
    }
