"""Foreign stock valuation — shares × live price × FX → INR."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import requests

from extensions import db
from models import Investment

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
FOREIGN_STOCK_TYPE = "foreign_stock"
TICKER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,19}$")

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FinanceOS/1.0; +local)",
    "Accept": "application/json",
}


class ForeignStockServiceError(ValueError):
    pass


def normalize_ticker(value: str | None) -> str:
    ticker = (value or "").strip().upper()
    if not ticker:
        raise ForeignStockServiceError("Ticker symbol is required (e.g. AMZN, DELL).")
    if not TICKER_RE.match(ticker):
        raise ForeignStockServiceError(
            f"“{ticker}” is not a valid ticker. Use letters like AMZN or BRK-B."
        )
    return ticker


def _get_json(url: str, *, params: dict | None = None) -> Any:
    try:
        resp = requests.get(
            url, params=params, headers=_HTTP_HEADERS, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except requests.Timeout as exc:
        raise ForeignStockServiceError("Stock price API timed out. Try again.") from exc
    except requests.ConnectionError as exc:
        raise ForeignStockServiceError(
            "Cannot reach stock price API. Check internet / try again."
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Stock price request failed: %s %s", url, exc)
        raise ForeignStockServiceError("Could not fetch stock price. Try again.") from exc


def fetch_usd_inr() -> Decimal:
    fx = _get_json(
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json"
    )
    try:
        rate = Decimal(str(fx["usd"]["inr"]))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise ForeignStockServiceError("Unexpected FX response.") from exc
    if rate <= 0:
        raise ForeignStockServiceError("Invalid USD/INR rate.")
    return rate


def fetch_fx_to_inr(currency: str) -> Decimal:
    """Return units of INR per 1 unit of `currency`."""
    code = (currency or "USD").strip().lower()
    if code == "inr":
        return Decimal("1")
    if code == "usd":
        return fetch_usd_inr()

    fx = _get_json(
        f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{code}.min.json"
    )
    try:
        rate = Decimal(str(fx[code]["inr"]))
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise ForeignStockServiceError(
            f"Could not convert {currency.upper()} to INR."
        ) from exc
    if rate <= 0:
        raise ForeignStockServiceError(f"Invalid {currency.upper()}/INR rate.")
    return rate


def fetch_quote(ticker: str) -> dict[str, Any]:
    """
    Live quote via Yahoo Finance chart meta.
    Returns {ticker, price, currency, as_of, company_name}.
    """
    symbol = normalize_ticker(ticker)
    payload = _get_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": "1d", "interval": "1d"},
    )
    chart = (payload or {}).get("chart") or {}
    if chart.get("error"):
        err = chart["error"]
        desc = err.get("description") or err.get("code") or "unknown error"
        raise ForeignStockServiceError(f"“{symbol}”: {desc}")

    results = chart.get("result") or []
    if not results:
        raise ForeignStockServiceError(f"No quote found for ticker “{symbol}”.")

    meta = results[0].get("meta") or {}
    price_raw = meta.get("regularMarketPrice")
    if price_raw is None:
        # Fallback to last close
        try:
            closes = (results[0].get("indicators") or {}).get("quote") or [{}]
            close_list = closes[0].get("close") or []
            price_raw = next((c for c in reversed(close_list) if c is not None), None)
        except (TypeError, IndexError, KeyError):
            price_raw = None
    try:
        price = Decimal(str(price_raw))
    except (InvalidOperation, TypeError) as exc:
        raise ForeignStockServiceError(f"“{symbol}” returned no usable price.") from exc
    if price <= 0:
        raise ForeignStockServiceError(f"“{symbol}” returned an invalid price.")

    currency = (meta.get("currency") or "USD").upper()
    as_of = date.today()
    ts = meta.get("regularMarketTime")
    if ts:
        try:
            as_of = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            pass

    return {
        "ticker": symbol,
        "price": price,
        "currency": currency,
        "as_of": as_of,
        "company_name": meta.get("longName") or meta.get("shortName") or "",
        "exchange": meta.get("exchangeName") or "",
    }


def value_shares(
    shares: Decimal,
    *,
    ticker: str | None = None,
    quote: dict[str, Any] | None = None,
    fx_to_inr: Decimal | None = None,
) -> dict[str, Any]:
    """Compute INR market value for shares of a foreign ticker."""
    shares = Decimal(shares or 0)
    if shares < 0:
        raise ForeignStockServiceError("Shares cannot be negative.")
    if shares <= 0:
        raise ForeignStockServiceError("Enter number of shares (> 0).")

    quote = quote or fetch_quote(ticker or "")
    currency = quote["currency"]
    fx = fx_to_inr if fx_to_inr is not None else fetch_fx_to_inr(currency)
    price = Decimal(quote["price"])
    value_local = shares * price
    value_inr = (value_local * fx).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    price_inr = (price * fx).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "shares": shares,
        "ticker": quote["ticker"],
        "price": price,
        "price_inr": price_inr,
        "currency": currency,
        "fx_to_inr": fx,
        "current_value": value_inr,
        "as_of": quote["as_of"],
        "quote": quote,
    }


def refresh_foreign_stock_holding(
    inv: Investment, *, quote: dict[str, Any] | None = None, fx_cache: dict[str, Decimal] | None = None
) -> dict[str, Any]:
    if inv.asset_type != FOREIGN_STOCK_TYPE:
        raise ForeignStockServiceError(f"“{inv.name}” is not a foreign stock holding.")
    shares = Decimal(inv.units or 0)
    ticker = normalize_ticker(inv.scheme_code)
    quote = quote or fetch_quote(ticker)
    currency = quote["currency"]
    if fx_cache is not None and currency in fx_cache:
        fx = fx_cache[currency]
    else:
        fx = fetch_fx_to_inr(currency)
        if fx_cache is not None:
            fx_cache[currency] = fx

    priced = value_shares(shares, quote=quote, fx_to_inr=fx)
    old_current = Decimal(inv.current_value or 0)
    inv.current_value = priced["current_value"]
    # Store local (usually USD) price in last_nav for display
    inv.last_nav = priced["price"]
    inv.last_nav_date = priced["as_of"]
    inv.scheme_code = priced["ticker"]
    db.session.commit()

    return {
        "id": inv.id,
        "name": inv.name,
        "ticker": priced["ticker"],
        "shares": shares,
        "price": priced["price"],
        "currency": priced["currency"],
        "fx_to_inr": priced["fx_to_inr"],
        "as_of": priced["as_of"],
        "old_current": old_current,
        "new_current": priced["current_value"],
    }


def refresh_all_foreign_stock_holdings() -> dict[str, Any]:
    holdings = (
        Investment.query.filter(
            Investment.is_active.is_(True),
            Investment.asset_type == FOREIGN_STOCK_TYPE,
        )
        .order_by(Investment.name)
        .all()
    )
    updated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    fx_cache: dict[str, Decimal] = {}
    quote_cache: dict[str, dict[str, Any]] = {}

    for inv in holdings:
        shares = Decimal(inv.units or 0)
        if shares <= 0:
            skipped.append(f"{inv.name}: no shares set")
            continue
        try:
            ticker = normalize_ticker(inv.scheme_code)
            if ticker not in quote_cache:
                quote_cache[ticker] = fetch_quote(ticker)
            refresh_foreign_stock_holding(
                inv, quote=quote_cache[ticker], fx_cache=fx_cache
            )
            updated.append(inv.name)
        except ForeignStockServiceError as exc:
            errors.append(str(exc) if inv.name in str(exc) else f"{inv.name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Foreign stock refresh failed for %s", inv.id)
            errors.append(f"{inv.name}: {exc}")

    return {
        "eligible_count": len(holdings),
        "updated_count": len(updated),
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "usd_inr": fx_cache.get("USD"),
    }
