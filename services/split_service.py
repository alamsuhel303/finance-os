"""Friends CRUD and expense-split / settlement ledger helpers."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from extensions import db
from models import (
    Account,
    Category,
    ExpenseSplit,
    ExpenseSplitSettlement,
    ExpenseSplitShare,
    Friend,
    Transaction,
)
from utils.helpers import parse_amount, parse_nonneg_amount


class SplitValidationError(ValueError):
    pass


# —— Friends ——


def list_friends(*, active_only: bool = True) -> list[Friend]:
    q = Friend.query
    if active_only:
        q = q.filter_by(is_active=True)
    return q.order_by(Friend.name).all()


def get_friend(friend_id: int) -> Friend | None:
    return db.session.get(Friend, friend_id)


def create_friend(data: dict[str, Any]) -> Friend:
    name = (data.get("name") or "").strip()
    if not name:
        raise SplitValidationError("Friend name is required.")
    friend = Friend(
        name=name[:120],
        notes=(data.get("notes") or "").strip() or None,
        is_active=True,
    )
    db.session.add(friend)
    db.session.commit()
    return friend


def update_friend(friend: Friend, data: dict[str, Any]) -> Friend:
    name = (data.get("name") or "").strip()
    if not name:
        raise SplitValidationError("Friend name is required.")
    friend.name = name[:120]
    friend.notes = (data.get("notes") or "").strip() or None
    if "is_active" in data:
        friend.is_active = str(data.get("is_active")).lower() in (
            "1",
            "true",
            "on",
            "yes",
        )
    db.session.commit()
    return friend


def deactivate_friend(friend: Friend) -> None:
    outstanding = friend_outstanding(friend.id)
    if outstanding > 0:
        raise SplitValidationError(
            f"{friend.name} still owes {outstanding}. Settle first, or keep them active."
        )
    friend.is_active = False
    db.session.commit()


def friend_outstanding(friend_id: int) -> Decimal:
    total = Decimal("0")
    shares = ExpenseSplitShare.query.filter_by(
        friend_id=friend_id, is_household=False
    ).all()
    for share in shares:
        total += share.outstanding
    return total


def friends_balances() -> list[dict[str, Any]]:
    """Per-friend open receivable totals (active friends + anyone with balance)."""
    rows: list[dict[str, Any]] = []
    friends = Friend.query.order_by(Friend.name).all()
    for friend in friends:
        owed = friend_outstanding(friend.id)
        if not friend.is_active and owed <= 0:
            continue
        rows.append(
            {
                "friend": friend,
                "outstanding": owed,
            }
        )
    rows.sort(key=lambda r: (-r["outstanding"], r["friend"].name.lower()))
    return rows


def total_friends_receivable() -> Decimal:
    shares = ExpenseSplitShare.query.filter_by(is_household=False).all()
    return sum((s.outstanding for s in shares), Decimal("0"))


# —— Parse split from form ——


def parse_split_payload(data: Any) -> dict[str, Any] | None:
    """
    Return None if split is not enabled.
    Else {household_share, friends: [{friend_id, amount}], notes}.
    """
    enabled = str(data.get("split_enabled") or "").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    if not enabled:
        return None

    household = parse_nonneg_amount(data.get("split_household_share"))
    if household is None:
        raise SplitValidationError("Enter your (household) share of the bill.")

    if hasattr(data, "getlist"):
        friend_ids = data.getlist("split_friend_id")
        friend_amts = data.getlist("split_friend_amount")
    else:
        friend_ids = data.get("split_friend_id") or []
        friend_amts = data.get("split_friend_amount") or []
        if not isinstance(friend_ids, list):
            friend_ids = [friend_ids] if friend_ids else []
        if not isinstance(friend_amts, list):
            friend_amts = [friend_amts] if friend_amts else []

    friends: list[dict[str, Any]] = []
    for raw_id, raw_amt in zip(friend_ids, friend_amts):
        try:
            fid = int(raw_id)
        except (TypeError, ValueError):
            continue
        amt = parse_amount(raw_amt)
        if amt is None:
            raise SplitValidationError("Enter a valid amount for each friend.")
        if amt <= 0:
            continue
        friend = get_friend(fid)
        if not friend or not friend.is_active:
            raise SplitValidationError("One of the selected friends is invalid.")
        friends.append({"friend_id": fid, "amount": amt})

    if not friends:
        raise SplitValidationError("Add at least one friend share for a split bill.")

    notes = (data.get("split_notes") or "").strip() or None
    return {
        "household_share": household,
        "friends": friends,
        "notes": notes,
    }


def validate_split_against_total(payload: dict[str, Any], total: Decimal) -> None:
    household = Decimal(payload["household_share"])
    friend_sum = sum((Decimal(f["amount"]) for f in payload["friends"]), Decimal("0"))
    combined = household + friend_sum
    if combined != total:
        raise SplitValidationError(
            f"Split parts ({combined}) must equal the bill total ({total}). "
            "Adjust household or friend amounts."
        )
    if household <= 0 and friend_sum <= 0:
        raise SplitValidationError("Split amounts must be greater than zero.")


def equal_split_amounts(
    total: Decimal, *, friend_count: int, include_household: bool = True
) -> tuple[Decimal, list[Decimal]]:
    """Return (household_share, [friend amounts…]) summing to total."""
    if friend_count < 1:
        raise SplitValidationError("Need at least one friend for equal split.")
    parts = friend_count + (1 if include_household else 0)
    if total <= 0:
        raise SplitValidationError("Bill total must be greater than zero.")
    weights = [Decimal("1")] * parts
    amounts = proportion_split_amounts(total, weights)
    if include_household:
        return amounts[0], amounts[1:]
    return Decimal("0"), amounts


def proportion_split_amounts(total: Decimal, weights: list[Decimal | int | str]) -> list[Decimal]:
    """
    Divide total by positive weights (e.g. 2,1,1 → 50%, 25%, 25%).
    Residue from rounding goes to the last part so amounts sum exactly to total.
    """
    if total <= 0:
        raise SplitValidationError("Bill total must be greater than zero.")
    if not weights:
        raise SplitValidationError("Enter at least one share part.")
    parsed: list[Decimal] = []
    for w in weights:
        try:
            d = Decimal(str(w).strip())
        except Exception as exc:
            raise SplitValidationError("Share parts must be numbers.") from exc
        if d <= 0:
            raise SplitValidationError("Share parts must be greater than zero.")
        parsed.append(d)
    weight_sum = sum(parsed)
    amounts: list[Decimal] = []
    for w in parsed[:-1]:
        amt = (total * w / weight_sum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        amounts.append(amt)
    residue = (total - sum(amounts)).quantize(Decimal("0.01"))
    amounts.append(residue)
    if any(a < 0 for a in amounts):
        raise SplitValidationError("Could not divide the bill by those parts.")
    return amounts


def find_friend_by_name(name: str) -> Friend | None:
    """Case-insensitive exact match on active friends."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for friend in list_friends(active_only=True):
        if friend.name.strip().lower() == needle:
            return friend
    return None


def match_friend_prefix(tokens: list[str], start: int) -> tuple[Friend | None, int]:
    """
    Greedy match of friend name starting at tokens[start].
    Returns (friend, tokens_consumed) or (None, 0).
    """
    if start >= len(tokens):
        return None, 0
    friends = sorted(
        list_friends(active_only=True),
        key=lambda f: len(f.name.split()),
        reverse=True,
    )
    for friend in friends:
        parts = friend.name.strip().split()
        n = len(parts)
        if n == 0 or start + n > len(tokens):
            continue
        candidate = " ".join(tokens[start : start + n])
        if candidate.lower() == friend.name.strip().lower():
            return friend, n
    return None, 0


HOUSEHOLD_ALIASES = frozenset(
    {"me", "us", "self", "household", "home", "ours", "my"}
)


def build_split_payload_from_shares(
    *,
    household_share: Decimal,
    friends: list[dict[str, Any]],
    notes: str | None = None,
) -> dict[str, Any]:
    if not friends:
        raise SplitValidationError("Add at least one friend share for a split bill.")
    return {
        "household_share": household_share,
        "friends": friends,
        "notes": notes,
    }


def payload_to_form_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Fields accepted by parse_split_payload / create_transaction."""
    return {
        "split_enabled": "1",
        "split_household_share": str(payload["household_share"]),
        "split_friend_id": [f["friend_id"] for f in payload["friends"]],
        "split_friend_amount": [str(f["amount"]) for f in payload["friends"]],
        "split_notes": payload.get("notes") or "",
    }


def _parse_number_token(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "").strip())
    except Exception:
        return None


def _is_household_token(token: str) -> bool:
    return token.strip().lower().rstrip(":") in HOUSEHOLD_ALIASES


def parse_telegram_split_clause(total: Decimal, clause: str) -> dict[str, Any]:
    """
    Parse text after the word 'split'.

    Examples:
      equal Rahul Priya
      Rahul 500 Priya 500
      me 500 Rahul 750 Priya 750
      parts me 2 Rahul 1 Priya 1
      me:2 Rahul:1 Priya:1
      2:1:1 me Rahul Priya
    """
    raw = (clause or "").strip()
    if not raw:
        raise SplitValidationError(
            "After 'split', add friends — e.g. split equal Rahul Priya "
            "or split Rahul 500 Priya 500 or split parts me 2 Rahul 1 Priya 1"
        )
    if total is None or total <= 0:
        raise SplitValidationError("Bill total must be greater than zero.")

    tokens = [t for t in re.split(r"\s+", raw) if t]
    lower0 = tokens[0].lower()

    # equal Rahul Priya
    if lower0 in ("equal", "evenly", "even"):
        return _telegram_equal_split(total, tokens[1:])

    # 2:1:1 me Rahul Priya
    if re.fullmatch(r"\d+(?:\.\d+)?(?::\d+(?:\.\d+)?)+", tokens[0]):
        weights = [Decimal(p) for p in tokens[0].split(":")]
        return _telegram_weighted_named(total, weights, tokens[1:])

    # parts me 2 Rahul 1 …
    if lower0 in ("parts", "part", "ratio", "ratios", "share", "shares"):
        return _telegram_parts_pairs(total, tokens[1:])

    # me:2 Rahul:1 style (any token contains colon name:weight)
    if any(":" in t and not t.replace(".", "").replace(":", "").isdigit() for t in tokens):
        return _telegram_colon_pairs(total, tokens)

    # Amount pairs: Rahul 500 … or me 500 Rahul 750 …
    return _telegram_amount_pairs(total, tokens)


def _telegram_equal_split(total: Decimal, name_tokens: list[str]) -> dict[str, Any]:
    friends = _consume_friend_names(name_tokens)
    if not friends:
        raise SplitValidationError(
            "Equal split needs friend names — e.g. split equal Rahul Priya"
        )
    hh, friend_amts = equal_split_amounts(total, friend_count=len(friends))
    return build_split_payload_from_shares(
        household_share=hh,
        friends=[
            {"friend_id": f.id, "amount": amt}
            for f, amt in zip(friends, friend_amts)
        ],
        notes="telegram equal split",
    )


def _telegram_weighted_named(
    total: Decimal, weights: list[Decimal], name_tokens: list[str]
) -> dict[str, Any]:
    if len(weights) < 2:
        raise SplitValidationError("Ratio needs at least two parts (you + a friend).")
    names = _consume_person_slots(name_tokens, expected=len(weights))
    amounts = proportion_split_amounts(total, weights)
    return _payload_from_named_amounts(names, amounts)


def _telegram_parts_pairs(total: Decimal, tokens: list[str]) -> dict[str, Any]:
    slots, weights = _parse_name_number_pairs(tokens, allow_decimal_weight=True)
    if len(slots) < 2:
        raise SplitValidationError(
            "Parts need you + friends — e.g. split parts me 2 Rahul 1 Priya 1"
        )
    if not any(s is None for s in slots):
        raise SplitValidationError("Include 'me' (your share) in the parts list.")
    amounts = proportion_split_amounts(total, weights)
    return _payload_from_named_amounts(slots, amounts)


def _telegram_colon_pairs(total: Decimal, tokens: list[str]) -> dict[str, Any]:
    slots: list[Friend | None] = []
    weights: list[Decimal] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if ":" in tok:
            name_part, _, num_part = tok.partition(":")
            w = _parse_number_token(num_part)
            if w is None or w <= 0:
                raise SplitValidationError(f"Bad share part in '{tok}'.")
            if _is_household_token(name_part):
                slots.append(None)
            else:
                friend = find_friend_by_name(name_part)
                if not friend:
                    raise SplitValidationError(
                        f"Unknown friend '{name_part}'. Add them under Splits → Friends."
                    )
                slots.append(friend)
            weights.append(w)
            i += 1
            continue
        friend, n = match_friend_prefix(tokens, i)
        if friend and i + n < len(tokens) and ":" not in tokens[i + n]:
            # "Rahul 1" style leftover — treat as name + next number
            w = _parse_number_token(tokens[i + n]) if i + n < len(tokens) else None
            if w is not None and w > 0:
                slots.append(friend)
                weights.append(w)
                i += n + 1
                continue
        if _is_household_token(tok) and i + 1 < len(tokens):
            w = _parse_number_token(tokens[i + 1])
            if w is not None and w > 0:
                slots.append(None)
                weights.append(w)
                i += 2
                continue
        raise SplitValidationError(
            f"Couldn't parse '{tok}'. Try: split me:2 Rahul:1 Priya:1"
        )
    if len(slots) < 2 or not any(s is None for s in slots):
        raise SplitValidationError("Include me and at least one friend with parts.")
    amounts = proportion_split_amounts(total, weights)
    return _payload_from_named_amounts(slots, amounts)


def _telegram_amount_pairs(total: Decimal, tokens: list[str]) -> dict[str, Any]:
    slots, amounts = _parse_name_number_pairs(tokens, allow_decimal_weight=True)
    if not slots:
        # Maybe only friend names → equal split
        friends = _consume_friend_names(tokens)
        if friends:
            return _telegram_equal_split(total, tokens)
        raise SplitValidationError(
            "Couldn't parse split. Try: split Rahul 500 Priya 500 "
            "or split equal Rahul Priya"
        )

    has_household = any(s is None for s in slots)
    friend_lines = [
        {"friend_id": s.id, "amount": amt}
        for s, amt in zip(slots, amounts)
        if s is not None
    ]
    if not friend_lines:
        raise SplitValidationError("Add at least one friend amount.")

    if has_household:
        hh = next(amt for s, amt in zip(slots, amounts) if s is None)
    else:
        friend_sum = sum((Decimal(f["amount"]) for f in friend_lines), Decimal("0"))
        hh = (total - friend_sum).quantize(Decimal("0.01"))
        if hh < 0:
            raise SplitValidationError(
                f"Friend shares ({friend_sum}) exceed the bill ({total})."
            )

    payload = build_split_payload_from_shares(
        household_share=hh,
        friends=friend_lines,
        notes="telegram split",
    )
    validate_split_against_total(payload, total)
    return payload


def _parse_name_number_pairs(
    tokens: list[str], *, allow_decimal_weight: bool
) -> tuple[list[Friend | None], list[Decimal]]:
    slots: list[Friend | None] = []
    numbers: list[Decimal] = []
    i = 0
    while i < len(tokens):
        if _is_household_token(tokens[i]):
            if i + 1 >= len(tokens):
                raise SplitValidationError("Expected a number after 'me'.")
            num = _parse_number_token(tokens[i + 1])
            if num is None or num <= 0:
                raise SplitValidationError(f"Invalid number after 'me': {tokens[i + 1]}")
            slots.append(None)
            numbers.append(num)
            i += 2
            continue
        friend, n = match_friend_prefix(tokens, i)
        if not friend:
            # stop if we can't parse further — caller may retry as names-only
            if not slots:
                return [], []
            raise SplitValidationError(
                f"Unknown friend '{tokens[i]}'. Add them under Splits → Friends first."
            )
        if i + n >= len(tokens):
            raise SplitValidationError(f"Expected an amount after {friend.name}.")
        num = _parse_number_token(tokens[i + n])
        if num is None or num <= 0:
            raise SplitValidationError(
                f"Invalid amount after {friend.name}: {tokens[i + n]}"
            )
        slots.append(friend)
        numbers.append(num)
        i += n + 1
    return slots, numbers


def _consume_friend_names(tokens: list[str]) -> list[Friend]:
    friends: list[Friend] = []
    i = 0
    while i < len(tokens):
        if _is_household_token(tokens[i]):
            i += 1
            continue
        friend, n = match_friend_prefix(tokens, i)
        if not friend:
            raise SplitValidationError(
                f"Unknown friend '{tokens[i]}'. Add them under Splits → Friends first."
            )
        friends.append(friend)
        i += n
    return friends


def _consume_person_slots(
    tokens: list[str], *, expected: int
) -> list[Friend | None]:
    slots: list[Friend | None] = []
    i = 0
    while i < len(tokens) and len(slots) < expected:
        if _is_household_token(tokens[i]):
            slots.append(None)
            i += 1
            continue
        friend, n = match_friend_prefix(tokens, i)
        if not friend:
            raise SplitValidationError(
                f"Unknown friend '{tokens[i]}'. Add them under Splits → Friends first."
            )
        slots.append(friend)
        i += n
    if len(slots) != expected:
        raise SplitValidationError(
            f"Ratio has {expected} parts but {len(slots)} names "
            f"(include me + friends in the same order)."
        )
    if not any(s is None for s in slots):
        raise SplitValidationError("Include 'me' in the name list for your share.")
    if all(s is None for s in slots):
        raise SplitValidationError("Add at least one friend name.")
    if i < len(tokens):
        raise SplitValidationError(f"Unexpected extra text: {' '.join(tokens[i:])}")
    return slots


def _payload_from_named_amounts(
    slots: list[Friend | None], amounts: list[Decimal]
) -> dict[str, Any]:
    if len(slots) != len(amounts):
        raise SplitValidationError("Internal split mismatch.")
    hh = Decimal("0")
    friends: list[dict[str, Any]] = []
    for slot, amt in zip(slots, amounts):
        if slot is None:
            hh += amt
        else:
            friends.append({"friend_id": slot.id, "amount": amt})
    return build_split_payload_from_shares(
        household_share=hh,
        friends=friends,
        notes="telegram split",
    )


# —— Attach / replace split on a transaction ——


def sync_split_for_transaction(
    txn: Transaction, payload: dict[str, Any] | None
) -> ExpenseSplit | None:
    """Create/update/remove split rows for an expense. Caller commits."""
    existing = ExpenseSplit.query.filter_by(transaction_id=txn.id).first()

    if payload is None:
        if existing:
            if existing.settlements:
                raise SplitValidationError(
                    "This split has settlements — remove them before clearing the split."
                )
            db.session.delete(existing)
            txn.household_share_amount = None
        else:
            txn.household_share_amount = None
        return None

    if txn.transaction_type != "expense":
        raise SplitValidationError("Only expenses can be split with friends.")

    total = Decimal(txn.amount or 0)
    validate_split_against_total(payload, total)
    household = Decimal(payload["household_share"])

    if existing and existing.settlements:
        # Allow editing only if friend amounts still cover settled amounts
        for friend_line in payload["friends"]:
            fid = friend_line["friend_id"]
            new_amt = Decimal(friend_line["amount"])
            share = next(
                (
                    s
                    for s in existing.shares
                    if not s.is_household and s.friend_id == fid
                ),
                None,
            )
            if share and Decimal(share.settled_amount or 0) > new_amt:
                raise SplitValidationError(
                    "Cannot reduce a friend's share below what they've already settled."
                )

    if existing:
        # Drop old shares without settlements attached directly; rebuild carefully
        settled_by_friend = {
            s.friend_id: Decimal(s.settled_amount or 0)
            for s in existing.shares
            if not s.is_household and s.friend_id
        }
        for share in list(existing.shares):
            db.session.delete(share)
        db.session.flush()
        split = existing
        split.total_amount = total
        split.household_share = household
        split.notes = payload.get("notes")
    else:
        settled_by_friend = {}
        split = ExpenseSplit(
            transaction_id=txn.id,
            total_amount=total,
            household_share=household,
            notes=payload.get("notes"),
            status="open",
        )
        db.session.add(split)
        db.session.flush()

    hh_share_row = None
    if household > 0:
        hh_share_row = ExpenseSplitShare(
            split_id=split.id,
            friend_id=None,
            is_household=True,
            amount=household,
            settled_amount=household,
            status="settled",
        )
        db.session.add(hh_share_row)

    for friend_line in payload["friends"]:
        fid = friend_line["friend_id"]
        amt = Decimal(friend_line["amount"])
        prev_settled = settled_by_friend.get(fid, Decimal("0"))
        share = ExpenseSplitShare(
            split_id=split.id,
            friend_id=fid,
            is_household=False,
            amount=amt,
            settled_amount=min(prev_settled, amt),
            status="open",
        )
        share.refresh_status()
        db.session.add(share)

    txn.household_share_amount = household
    db.session.flush()
    refresh_split_status(split)
    return split


def refresh_split_status(split: ExpenseSplit) -> None:
    friend_shares = [s for s in split.shares if not s.is_household]
    if not friend_shares:
        split.status = "settled"
        return
    for s in friend_shares:
        s.refresh_status()
    if all(s.status == "settled" for s in friend_shares):
        split.status = "settled"
    elif any(Decimal(s.settled_amount or 0) > 0 for s in friend_shares):
        split.status = "partial"
    else:
        split.status = "open"


def get_split(split_id: int) -> ExpenseSplit | None:
    return db.session.get(ExpenseSplit, split_id)


def get_split_for_transaction(txn_id: int) -> ExpenseSplit | None:
    return ExpenseSplit.query.filter_by(transaction_id=txn_id).first()


def list_open_splits() -> list[ExpenseSplit]:
    return (
        ExpenseSplit.query.filter(ExpenseSplit.status.in_(("open", "partial")))
        .order_by(ExpenseSplit.id.desc())
        .all()
    )


def list_all_splits(*, limit: int = 50) -> list[ExpenseSplit]:
    return ExpenseSplit.query.order_by(ExpenseSplit.id.desc()).limit(limit).all()


def assert_can_delete_transaction(txn: Transaction) -> None:
    """Legacy guard — delete_transaction now auto-undoes settlements first."""
    return


def _encode_share_allocations(allocations: list[dict[str, Any]]) -> str:
    return json.dumps(
        [
            {"share_id": int(a["share_id"]), "amount": str(a["amount"])}
            for a in allocations
        ]
    )


def _decode_share_allocations(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or "share_id" not in item:
            continue
        try:
            out.append(
                {
                    "share_id": int(item["share_id"]),
                    "amount": Decimal(str(item["amount"])),
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def _infer_legacy_settlement_allocations(
    settlement: ExpenseSplitSettlement,
) -> list[dict[str, Any]]:
    """
    Best-effort allocations for settlements recorded before share_allocations existed.
    Matches FIFO apply: same-split shares first, then other shares for this friend.
    """
    amount = Decimal(settlement.amount or 0)
    if amount <= 0:
        return []

    remaining = amount
    allocations: list[dict[str, Any]] = []

    if settlement.share_id:
        share = db.session.get(ExpenseSplitShare, settlement.share_id)
        if share and not share.is_household:
            settled = Decimal(share.settled_amount or 0)
            take = min(settled, amount)
            if take > 0:
                allocations.append({"share_id": share.id, "amount": take})
                remaining -= take

    same_split = (
        ExpenseSplitShare.query.filter_by(
            split_id=settlement.split_id,
            friend_id=settlement.friend_id,
            is_household=False,
        )
        .order_by(ExpenseSplitShare.id.asc())
        .all()
    )
    for share in same_split:
        if remaining <= 0:
            break
        settled = Decimal(share.settled_amount or 0)
        if settled <= 0:
            continue
        take = min(settled, remaining)
        allocations.append({"share_id": share.id, "amount": take})
        remaining -= take

    if remaining > 0:
        other_shares = (
            ExpenseSplitShare.query.filter(
                ExpenseSplitShare.friend_id == settlement.friend_id,
                ExpenseSplitShare.is_household.is_(False),
                ExpenseSplitShare.split_id != settlement.split_id,
            )
            .order_by(ExpenseSplitShare.split_id.asc(), ExpenseSplitShare.id.asc())
            .all()
        )
        for share in other_shares:
            if remaining <= 0:
                break
            settled = Decimal(share.settled_amount or 0)
            if settled <= 0:
                continue
            take = min(settled, remaining)
            allocations.append({"share_id": share.id, "amount": take})
            remaining -= take

    return allocations


def _reverse_share_allocations(
    allocations: list[dict[str, Any]],
    *,
    total: Decimal,
    allow_partial: bool = False,
) -> Decimal:
    """
    Undo settled_amount on shares (reverse order of apply).
    Returns amount still not reversed on shares.
    """
    remaining = total
    splits_to_refresh: set[int] = set()
    for alloc in reversed(allocations):
        share = db.session.get(ExpenseSplitShare, int(alloc["share_id"]))
        if not share:
            continue
        take = Decimal(alloc["amount"])
        settled = Decimal(share.settled_amount or 0)
        if take > settled + Decimal("0.005") and not allow_partial:
            raise SplitValidationError(
                "Could not reverse settlement — share amounts no longer match."
            )
        actual = min(take, settled)
        if actual <= 0:
            continue
        share.settled_amount = settled - actual
        share.refresh_status()
        splits_to_refresh.add(share.split_id)
        remaining -= actual

    if remaining > Decimal("0.005") and not allow_partial:
        raise SplitValidationError(
            "Could not reverse settlement — share amounts no longer match."
        )

    for split_id in splits_to_refresh:
        split = db.session.get(ExpenseSplit, split_id)
        if split:
            refresh_split_status(split)
    return max(remaining, Decimal("0"))


def _greedy_reverse_on_friend_shares(
    *,
    friend_id: int,
    amount: Decimal,
    prefer_split_id: int | None = None,
    skip_split_id: int | None = None,
) -> Decimal:
    """Peel settled amounts from a friend's shares until amount exhausted."""
    remaining = amount
    if remaining <= 0:
        return Decimal("0")

    def _iter_shares():
        if prefer_split_id is not None:
            preferred = (
                ExpenseSplitShare.query.filter_by(
                    split_id=prefer_split_id,
                    friend_id=friend_id,
                    is_household=False,
                )
                .order_by(ExpenseSplitShare.id.desc())
                .all()
            )
            for share in preferred:
                yield share
        others = ExpenseSplitShare.query.filter(
            ExpenseSplitShare.friend_id == friend_id,
            ExpenseSplitShare.is_household.is_(False),
        )
        if prefer_split_id is not None:
            others = others.filter(ExpenseSplitShare.split_id != prefer_split_id)
        if skip_split_id is not None:
            others = others.filter(ExpenseSplitShare.split_id != skip_split_id)
        for share in others.order_by(
            ExpenseSplitShare.split_id.desc(), ExpenseSplitShare.id.desc()
        ):
            yield share

    splits_to_refresh: set[int] = set()
    seen: set[int] = set()
    for share in _iter_shares():
        if share.id in seen:
            continue
        seen.add(share.id)
        if remaining <= 0:
            break
        settled = Decimal(share.settled_amount or 0)
        if settled <= 0:
            continue
        take = min(settled, remaining)
        share.settled_amount = settled - take
        share.refresh_status()
        splits_to_refresh.add(share.split_id)
        remaining -= take

    for split_id in splits_to_refresh:
        split = db.session.get(ExpenseSplit, split_id)
        if split:
            refresh_split_status(split)
    return remaining


def _remove_settlement_record(settlement: ExpenseSplitSettlement) -> None:
    settle_txn_id = settlement.transaction_id
    db.session.delete(settlement)
    db.session.flush()
    if settle_txn_id:
        from services import transaction_service

        settle_txn = db.session.get(Transaction, settle_txn_id)
        if settle_txn:
            transaction_service._delete_transaction_core(settle_txn)


def undo_settlement(
    settlement: ExpenseSplitSettlement | int, *, commit: bool = True
) -> None:
    """Reverse a friend repayment: restore receivable and remove the cash credit txn."""
    if isinstance(settlement, int):
        settlement = db.session.get(ExpenseSplitSettlement, settlement)
    if not settlement:
        raise SplitValidationError("Settlement not found.")

    amount = Decimal(settlement.amount or 0)
    if amount <= 0:
        raise SplitValidationError("Invalid settlement amount.")

    allocations = _decode_share_allocations(settlement.share_allocations)
    if not allocations:
        allocations = _infer_legacy_settlement_allocations(settlement)

    remaining = amount
    if allocations:
        remaining = _reverse_share_allocations(
            allocations, total=amount, allow_partial=True
        )
    if remaining > Decimal("0.005"):
        remaining = _greedy_reverse_on_friend_shares(
            friend_id=settlement.friend_id,
            amount=remaining,
            prefer_split_id=settlement.split_id,
        )
    if remaining > Decimal("0.005"):
        raise SplitValidationError(
            "Could not reverse settlement — share amounts no longer match."
        )

    _remove_settlement_record(settlement)

    if commit:
        db.session.commit()


def _cleanup_settlement_for_split_delete(
    settlement: ExpenseSplitSettlement, *, deleting_split_id: int
) -> None:
    """
    Drop settlement when its parent expense split is being deleted.
    Only reverse balances on *other* splits — this split's rows are removed.
    """
    amount = Decimal(settlement.amount or 0)
    if amount > 0:
        _greedy_reverse_on_friend_shares(
            friend_id=settlement.friend_id,
            amount=amount,
            skip_split_id=deleting_split_id,
        )
    _remove_settlement_record(settlement)


def undo_settlements_for_transaction(txn: Transaction) -> int:
    """Undo all settlements linked to this expense's split. Returns count undone."""
    split = get_split_for_transaction(txn.id)
    if not split:
        return 0
    settlements = (
        ExpenseSplitSettlement.query.filter_by(split_id=split.id)
        .order_by(ExpenseSplitSettlement.id.desc())
        .all()
    )
    if not settlements:
        return 0
    count = 0
    for settlement in settlements:
        _cleanup_settlement_for_split_delete(
            settlement, deleting_split_id=split.id
        )
        count += 1
    db.session.expire(split)
    return count


def delete_split_for_transaction(txn: Transaction) -> None:
    """Remove split rows when deleting the expense."""
    split = get_split_for_transaction(txn.id)
    if not split:
        return
    if ExpenseSplitSettlement.query.filter_by(split_id=split.id).count():
        raise SplitValidationError(
            "This expense still has friend settlements. Undo them first."
        )
    db.session.delete(split)


# —— Settlements ——


def settle_friend_share(
    *,
    friend_id: int,
    amount: Decimal,
    account_id: int,
    settle_date: date | None = None,
    notes: str | None = None,
    split_id: int | None = None,
) -> ExpenseSplitSettlement:
    """
    Apply repayment from a friend: credit account cash, reduce outstanding shares.
    Creates an income transaction (excluded from budget) tagged as settlement.
    """
    friend = get_friend(friend_id)
    if not friend:
        raise SplitValidationError("Friend not found.")

    amount = Decimal(amount or 0)
    if amount <= 0:
        raise SplitValidationError("Settlement amount must be greater than zero.")

    account = db.session.get(Account, account_id)
    if not account or not account.is_active:
        raise SplitValidationError("Select a valid account to receive the repayment.")

    owed = friend_outstanding(friend_id)
    if amount > owed:
        raise SplitValidationError(
            f"{friend.name} only owes {owed}. Cannot settle {amount}."
        )

    # Shares oldest-first (by split id)
    q = ExpenseSplitShare.query.filter_by(
        friend_id=friend_id, is_household=False
    ).order_by(ExpenseSplitShare.split_id, ExpenseSplitShare.id)
    if split_id:
        q = q.filter_by(split_id=split_id)
    shares = [s for s in q.all() if s.outstanding > 0]
    if not shares:
        raise SplitValidationError("Nothing left to settle for this friend.")

    remaining = amount
    # Create cash credit transaction (income, excluded from budget)
    settle_date = settle_date or date.today()
    income_cat = (
        Category.query.filter_by(
            is_active=True, category_type="income", slug="other-income"
        ).first()
        or Category.query.filter_by(is_active=True, category_type="income").first()
    )
    from services import transaction_service

    txn, _ = transaction_service.create_transaction(
        {
            "amount": str(amount),
            "description": f"Settlement from {friend.name}",
            "transaction_type": "income",
            "account_id": account_id,
            "category_id": income_cat.id if income_cat else None,
            "date": settle_date.isoformat(),
            "notes": notes or "Friend expense split settlement",
            "is_excluded_from_budget": "1",
            "source": "web",
        }
    )

    # Apply to shares FIFO; one settlement row per primary share (first),
    # but reduce multiple shares if needed — store settlement on first split.
    primary_share = shares[0]
    primary_split = primary_share.split
    allocations: list[dict[str, Any]] = []

    for share in shares:
        if remaining <= 0:
            break
        take = min(share.outstanding, remaining)
        share.settled_amount = Decimal(share.settled_amount or 0) + take
        share.refresh_status()
        refresh_split_status(share.split)
        allocations.append({"share_id": share.id, "amount": take})
        remaining -= take

    settlement = ExpenseSplitSettlement(
        split_id=primary_split.id,
        friend_id=friend_id,
        share_id=primary_share.id,
        amount=amount,
        account_id=account_id,
        date=settle_date,
        transaction_id=txn.id,
        notes=notes,
        share_allocations=_encode_share_allocations(allocations),
    )
    db.session.add(settlement)
    db.session.commit()
    return settlement


def settlement_overview() -> dict[str, Any]:
    balances = friends_balances()
    outstanding = sum((b["outstanding"] for b in balances), Decimal("0"))
    open_splits = list_open_splits()
    return {
        "balances": balances,
        "outstanding_total": outstanding,
        "open_splits": open_splits,
        "open_split_count": len(open_splits),
        "friends": list_friends(active_only=True),
    }
