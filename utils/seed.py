"""Database seeding — default accounts and categories for first launch."""

from __future__ import annotations

import logging
import re
from datetime import date
from decimal import Decimal

from extensions import db
from models import Account, Budget, Category, Envelope, Goal, Transaction

logger = logging.getLogger(__name__)

# Household monthly budget only (INR). Shopping / Travel / Lifestyle stay on Envelopes.
DEFAULT_BUDGET_AMOUNTS = {
    "Rent": 43000,
    "Utilities": 5000,
    "Groceries": 18000,
    "Fruits & Vegetables": 8000,
    "Protein & Supplements": 8000,
    "Personal Care & Household": 6000,
    "Cook": 4000,
    "Fuel & Bike": 5000,
    "Auto / Cab": 3000,
    "Gym": 3000,
    "Insurance": 11000,
    "Medical": 10000,
    "Misc / Home Buffer": 11000,
}

# Spend labels funded via purpose envelopes — not part of the household budget plan.
ENVELOPE_PURPOSE_CATEGORY_SLUGS = frozenset(
    {
        "shopping",
        "furniture",
        "electronics",
        "travel",
        "flights",
        "hotels",
        "travel-cab-transport",
        "bike",  # merged into Fuel & Bike
    }
)

# Outside the ~₹1.5L Essentials household Budget.
# Still bookable; tracked via envelopes / personal accounts instead.
# Dining/movies → fund Lifestyle pot; Parents → usually personal accounts.
BUDGET_EXCLUDED_CATEGORY_SLUGS = frozenset(
    {
        "parents",
        "dining-out",
        "dining",
        "movies-entertainment",
        "lifestyle",
    }
)

# One-time renames for clearer household naming on existing DBs
CATEGORY_RENAMES = {
    "Housing": "Rent",
    "Supplements": "Protein & Supplements",
    "Dining": "Dining Out",
    "Fuel": "Fuel & Bike",
    "Personal": "Personal Care & Household",
    "Miscellaneous": "Misc / Home Buffer",
    # Common typos / near-duplicates → canonical household name
    "Fruits and Vegitables": "Fruits & Vegetables",
    "Fruits and Vegetables": "Fruits & Vegetables",
    # Lifestyle catch-all → clearer movies / outings label (envelope stays “Lifestyle”)
    "Lifestyle": "Movies & Entertainment",
}

# Shared fund accounts (seeded regardless of mode)
_SHARED_ACCOUNTS = [
    # Emergency is virtual tags on accounts/investments — no separate cash account
    {"name": "Home Fund", "account_type": "goal", "owner": "joint", "sort_order": 5},
    {"name": "Travel Fund", "account_type": "goal", "owner": "joint", "sort_order": 6},
    {
        "name": "Lifestyle Fund",
        "account_type": "goal",
        "owner": "joint",
        "sort_order": 7,
    },
    {
        "name": "Investment Account",
        "account_type": "investment",
        "owner": "joint",
        "sort_order": 8,
    },
    {"name": "Cash", "account_type": "cash", "owner": "joint", "sort_order": 9},
]


def _get_core_accounts() -> list[dict]:
    """Personal/joint core accounts from AppProfile. Empty until setup completes."""
    from services import profile_service

    profile = profile_service.get_profile()
    if not profile or not profile.is_setup_complete:
        return []

    p1 = (profile.person1_name or "Person 1").strip() or "Person 1"
    if profile.mode == "single":
        return [
            {
                "name": f"{p1} Salary",
                "account_type": "bank",
                "owner": "self",
                "sort_order": 1,
                "role": "salary",
            },
            {
                "name": f"{p1} Expenses",
                "account_type": "cash",
                "owner": "self",
                "sort_order": 2,
                "role": "expenses",
            },
        ]

    p2 = (profile.person2_name or "Partner").strip() or "Partner"
    return [
        {
            "name": f"{p1} Salary",
            "account_type": "bank",
            "owner": "self",
            "sort_order": 1,
            "role": "salary",
        },
        {
            "name": f"{p2} Salary",
            "account_type": "bank",
            "owner": "wife",
            "sort_order": 2,
            "role": "salary",
        },
        {
            "name": "Joint Account",
            "account_type": "joint",
            "owner": "joint",
            "sort_order": 3,
            "role": "joint",
        },
    ]


def _get_default_accounts() -> list[dict]:
    """Full account list: profile core (if set up) + shared funds."""
    return _get_core_accounts() + _SHARED_ACCOUNTS


# Legacy renames only (pre-profile era). Profile sync handles Suhel/My Account → {Name} Salary.
ACCOUNT_RENAMES = {
    "My Salary": "My Account",
    "Wife Salary": "Wife Account",
}

# (name, type, icon, color) — household first, then optional / envelope-purpose labels
DEFAULT_CATEGORIES = [
    ("Rent", "expense", "bi-house", "#38bdf8"),
    ("Utilities", "expense", "bi-lightning-charge", "#fbbf24"),
    ("Groceries", "expense", "bi-cart3", "#34d399"),
    ("Fruits & Vegetables", "expense", "bi-basket", "#86efac"),
    ("Protein & Supplements", "expense", "bi-capsule", "#a78bfa"),
    ("Personal Care & Household", "expense", "bi-person", "#a3e635"),
    ("Cook", "expense", "bi-egg-fried", "#fb923c"),
    ("Dining Out", "expense", "bi-cup-hot", "#f472b6"),
    ("Movies & Entertainment", "expense", "bi-film", "#e879f9"),
    ("Fuel & Bike", "expense", "bi-fuel-pump", "#94a3b8"),
    ("Auto / Cab", "expense", "bi-taxi-front", "#fcd34d"),
    ("Gym", "expense", "bi-activity", "#4ade80"),
    ("Insurance", "expense", "bi-shield-check", "#2dd4bf"),
    ("Medical", "expense", "bi-heart-pulse", "#f87171"),
    ("Misc / Home Buffer", "expense", "bi-three-dots", "#64748b"),
    # Optional household (no default budget amount)
    ("Car", "expense", "bi-car-front", "#818cf8"),
    ("Parents", "expense", "bi-people", "#f59e0b"),
    # Envelope-purpose labels (Shopping / Travel pots — not household budget)
    ("Shopping", "expense", "bi-bag", "#fb7185"),
    ("Furniture", "expense", "bi-lamp", "#c084fc"),
    ("Electronics", "expense", "bi-phone", "#22d3ee"),
    ("Travel", "expense", "bi-airplane", "#60a5fa"),
    ("Flights", "expense", "bi-airplane-engines", "#38bdf8"),
    ("Hotels", "expense", "bi-building", "#818cf8"),
    ("Travel Cab / Transport", "expense", "bi-taxi-front", "#67e8f9"),
    ("Investment", "investment", "bi-graph-up-arrow", "#10b981"),
    ("Salary", "income", "bi-wallet2", "#22c55e"),
]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "category"


def seed_accounts() -> int:
    """Create/sync accounts from profile + shared funds.

    Core personal accounts are only created after setup. Existing owner-matched
    accounts are renamed to the profile names (no duplicate empty copies).
    """
    touched = sync_core_accounts_from_profile()
    for item in _SHARED_ACCOUNTS:
        if _shared_account_present(item):
            continue
        db.session.add(
            Account(
                name=item["name"],
                account_type=item["account_type"],
                owner=item["owner"],
                opening_balance=Decimal("0"),
                current_balance=Decimal("0"),
                sort_order=item["sort_order"],
                is_active=True,
            )
        )
        touched += 1
    return touched


def _shared_account_present(item: dict) -> bool:
    if Account.query.filter_by(name=item["name"]).first():
        return True
    account_type = item["account_type"]
    # One fund pot per type is enough (user may have renamed)
    if account_type in ("emergency", "investment", "goal"):
        return (
            Account.query.filter_by(account_type=account_type, name=item["name"]).first()
            is not None
            or Account.query.filter_by(name=item["name"]).first() is not None
        )
    if account_type == "cash" and item["name"] == "Cash":
        return Account.query.filter_by(name="Cash").first() is not None
    return False


def _account_is_empty(account: Account) -> bool:
    bal = Decimal(account.current_balance or 0)
    if bal != 0:
        return False
    txns = Transaction.query.filter(
        (Transaction.account_id == account.id)
        | (Transaction.to_account_id == account.id)
    ).count()
    return txns == 0


def _account_rank(account: Account) -> tuple:
    """Higher rank = prefer keeping this account (has money/history)."""
    bal = abs(Decimal(account.current_balance or 0))
    txns = Transaction.query.filter(
        (Transaction.account_id == account.id)
        | (Transaction.to_account_id == account.id)
    ).count()
    return (txns > 0, bal, -account.id)


def _find_core_match(item: dict) -> Account | None:
    """Find the best existing account for a profile core slot (by role/owner).

    Prefers accounts with balance/transactions over empty name-matched shells.
    """
    role = item.get("role")
    owner = item["owner"]

    if role == "joint" or item["account_type"] == "joint":
        candidates = Account.query.filter(
            (Account.account_type == "joint") | (Account.name == "Joint Account")
        ).all()
        return max(candidates, key=_account_rank) if candidates else None

    if role == "expenses":
        candidates = (
            Account.query.filter_by(owner="self", is_active=True)
            .order_by(Account.sort_order, Account.id)
            .all()
        )
        named = [a for a in candidates if "expense" in (a.name or "").lower()]
        if named:
            return max(named, key=_account_rank)
        soft = [
            a
            for a in candidates
            if a.account_type == "cash" and (a.name or "") != "Cash"
        ]
        return max(soft, key=_account_rank) if soft else None

    # salary / personal bank — pick richest self/wife bank, not an empty rename shell
    candidates = (
        Account.query.filter(
            Account.owner == owner,
            Account.account_type.in_(("bank", "salary")),
            Account.is_active.is_(True),
        )
        .all()
    )
    return max(candidates, key=_account_rank) if candidates else None


def sync_core_accounts_from_profile() -> int:
    """Rename/create core accounts to match AppProfile. Safe to run every startup."""
    from services import profile_service

    if not profile_service.is_setup_complete():
        return 0

    touched = 0
    keep_ids: set[int] = set()

    for item in _get_core_accounts():
        match = _find_core_match(item)
        if match:
            keep_ids.add(match.id)
            if match.name != item["name"]:
                conflict = Account.query.filter_by(name=item["name"]).first()
                if conflict and conflict.id != match.id:
                    if _account_is_empty(conflict):
                        db.session.delete(conflict)
                        db.session.flush()
                    else:
                        logger.warning(
                            "Skip rename %r → %r; target name already in use",
                            match.name,
                            item["name"],
                        )
                        continue
                match.name = item["name"]
                touched += 1
            if item["account_type"] == "bank" and match.account_type == "salary":
                match.account_type = "bank"
                touched += 1
            if match.account_type != item["account_type"] and item.get("role") == "expenses":
                match.account_type = item["account_type"]
                touched += 1
            if match.sort_order != item["sort_order"]:
                match.sort_order = item["sort_order"]
            if match.owner != item["owner"]:
                match.owner = item["owner"]
                touched += 1
        else:
            acc = Account(
                name=item["name"],
                account_type=item["account_type"],
                owner=item["owner"],
                opening_balance=Decimal("0"),
                current_balance=Decimal("0"),
                sort_order=item["sort_order"],
                is_active=True,
            )
            db.session.add(acc)
            db.session.flush()
            keep_ids.add(acc.id)
            touched += 1

    # Drop empty duplicate personal bank accounts left over from earlier seeds
    for owner in ("self", "wife"):
        banks = (
            Account.query.filter(
                Account.owner == owner,
                Account.account_type.in_(("bank", "salary")),
                Account.is_active.is_(True),
            )
            .order_by(Account.sort_order, Account.id)
            .all()
        )
        for acc in banks:
            if acc.id in keep_ids:
                continue
            if _account_is_empty(acc):
                db.session.delete(acc)
                touched += 1

    return touched


def migrate_account_names() -> int:
    """Rename legacy account labels (e.g. My Salary → My Account).

    Handles the case where a fresh empty 'My Account' was seeded while
    'My Salary' still held the real balance/transactions.
    """
    renamed = 0
    for old_name, new_name in ACCOUNT_RENAMES.items():
        old = Account.query.filter_by(name=old_name).first()
        if not old:
            # Also normalize type on already-renamed rows
            existing_new = Account.query.filter_by(name=new_name).first()
            if existing_new and existing_new.account_type == "salary":
                existing_new.account_type = "bank"
                renamed += 1
            continue

        new = Account.query.filter_by(name=new_name).first()
        if new and new.id != old.id:
            # Prefer keeping the account that has history/balance
            old_txns = Transaction.query.filter(
                (Transaction.account_id == old.id)
                | (Transaction.to_account_id == old.id)
            ).count()
            new_txns = Transaction.query.filter(
                (Transaction.account_id == new.id)
                | (Transaction.to_account_id == new.id)
            ).count()
            old_bal = Decimal(old.current_balance or 0)
            new_bal = Decimal(new.current_balance or 0)

            if new_txns == 0 and new_bal == 0:
                # Empty duplicate — drop it, rename the real account
                db.session.delete(new)
                db.session.flush()
                old.name = new_name
                if old.account_type == "salary":
                    old.account_type = "bank"
                renamed += 1
            elif old_txns == 0 and old_bal == 0:
                db.session.delete(old)
                if new.account_type == "salary":
                    new.account_type = "bank"
                renamed += 1
            else:
                # Both have data — leave names; user can merge manually
                logger.warning(
                    "Could not auto-rename %r → %r; both accounts have data",
                    old_name,
                    new_name,
                )
            continue

        old.name = new_name
        if old.account_type == "salary":
            old.account_type = "bank"
        renamed += 1
    return renamed


def _merge_category_into(source: Category, target: Category) -> None:
    """Move transactions/budgets from source → target, then deactivate source."""
    Transaction.query.filter_by(category_id=source.id).update(
        {Transaction.category_id: target.id}, synchronize_session=False
    )
    Transaction.query.filter_by(subcategory_id=source.id).update(
        {Transaction.subcategory_id: target.id}, synchronize_session=False
    )
    for budget in Budget.query.filter_by(category_id=source.id).all():
        existing = Budget.query.filter_by(
            year=budget.year, month=budget.month, category_id=target.id
        ).first()
        if existing:
            # Keep the larger planned amount when both lines exist
            if Decimal(budget.amount or 0) > Decimal(existing.amount or 0):
                existing.amount = budget.amount
            db.session.delete(budget)
        else:
            budget.category_id = target.id
    if not target.envelope_id and source.envelope_id:
        target.envelope_id = source.envelope_id
    source.is_active = False
    # Avoid unique name clashes if UI lists inactive cats
    if source.name == target.name:
        source.name = f"{source.name} (merged)"
        source.slug = f"{source.slug}-merged-{source.id}"


def migrate_category_names() -> int:
    """Rename or merge legacy household category labels; deactivate merged Bike."""
    changed = 0
    for old_name, new_name in CATEGORY_RENAMES.items():
        old = Category.query.filter_by(
            name=old_name, parent_id=None, is_active=True
        ).first()
        if not old:
            continue
        target = (
            Category.query.filter_by(name=new_name, parent_id=None, is_active=True)
            .first()
            or Category.query.filter_by(name=new_name, parent_id=None).first()
        )
        if target and target.id != old.id:
            _merge_category_into(old, target)
            changed += 1
            continue
        old.name = new_name
        base_slug = slugify(new_name)
        slug = base_slug
        n = 2
        while True:
            other = Category.query.filter_by(slug=slug).first()
            if not other or other.id == old.id:
                break
            slug = f"{base_slug}-{n}"
            n += 1
        old.slug = slug
        changed += 1

    bike = Category.query.filter_by(slug="bike", parent_id=None, is_active=True).first()
    if bike:
        fuel = Category.query.filter_by(slug="fuel-bike", parent_id=None).first()
        if fuel:
            _merge_category_into(bike, fuel)
        else:
            bike.is_active = False
        changed += 1
    return changed


def migrate_strip_envelope_purpose_budgets() -> int:
    """Remove Shopping/Travel/Lifestyle (etc.) lines from category budgets."""
    purpose_ids = [
        c.id
        for c in Category.query.filter(
            Category.slug.in_(tuple(ENVELOPE_PURPOSE_CATEGORY_SLUGS))
        ).all()
    ]
    if not purpose_ids:
        return 0
    deleted = Budget.query.filter(Budget.category_id.in_(purpose_ids)).delete(
        synchronize_session=False
    )
    return int(deleted or 0)


def seed_categories() -> int:
    touched = 0
    for index, (name, category_type, icon, color) in enumerate(DEFAULT_CATEGORIES):
        slug = slugify(name)
        existing = Category.query.filter_by(slug=slug).first()
        if existing:
            # Keep sort order aligned with household-first list
            if existing.sort_order != index + 1:
                existing.sort_order = index + 1
                touched += 1
            continue
        category = Category(
            name=name,
            slug=slug,
            category_type=category_type,
            icon=icon,
            color=color,
            is_system=True,
            is_active=True,
            sort_order=index + 1,
        )
        db.session.add(category)
        touched += 1
    return touched


def seed_budgets_for_month(year: int | None = None, month: int | None = None) -> int:
    """Seed default envelopes for a month if none exist yet."""
    today = date.today()
    year = year or today.year
    month = month or today.month

    existing = Budget.query.filter_by(year=year, month=month).count()
    if existing:
        return 0

    created = 0
    for name, amount in DEFAULT_BUDGET_AMOUNTS.items():
        category = Category.query.filter_by(name=name, parent_id=None).first()
        if not category:
            continue
        db.session.add(
            Budget(
                year=year,
                month=month,
                category_id=category.id,
                amount=Decimal(str(amount)),
            )
        )
        created += 1
    return created


def seed_goals() -> int:
    """Seed default life goal shells — amounts start at 0; user fills targets."""
    defaults = [
        {
            "name": "Emergency Fund",
            "slug": "emergency-fund",
            "goal_type": "emergency",
            "target_amount": Decimal("0"),
            "monthly_contribution": Decimal("0"),
            "account_name": None,  # virtual tags — see emergency_service
            "icon": "bi-shield-check",
            "color": "#2dd4bf",
            "sort_order": 1,
        },
        {
            "name": "Home Fund",
            "slug": "home-fund",
            "goal_type": "home",
            "target_amount": Decimal("0"),
            "monthly_contribution": Decimal("0"),
            "account_name": "Home Fund",
            "icon": "bi-house-heart",
            "color": "#60a5fa",
            "sort_order": 2,
        },
        {
            "name": "Travel Fund",
            "slug": "travel-fund",
            "goal_type": "travel",
            "target_amount": Decimal("0"),
            "monthly_contribution": Decimal("0"),
            "account_name": "Travel Fund",
            "icon": "bi-airplane",
            "color": "#a78bfa",
            "sort_order": 3,
        },
        {
            "name": "Car Fund",
            "slug": "car-fund",
            "goal_type": "car",
            "target_amount": Decimal("0"),
            "monthly_contribution": Decimal("0"),
            "account_name": None,
            "icon": "bi-car-front",
            "color": "#fb923c",
            "sort_order": 4,
        },
        {
            "name": "Retirement",
            "slug": "retirement",
            "goal_type": "retirement",
            "target_amount": Decimal("0"),
            "monthly_contribution": Decimal("0"),
            "account_name": "Investment Account",
            "icon": "bi-sunrise",
            "color": "#fbbf24",
            "sort_order": 5,
        },
    ]

    created = 0
    for item in defaults:
        if Goal.query.filter_by(slug=item["slug"]).first():
            continue
        linked_id = None
        if item["account_name"]:
            account = Account.query.filter_by(name=item["account_name"]).first()
            if account:
                linked_id = account.id
        db.session.add(
            Goal(
                name=item["name"],
                slug=item["slug"],
                goal_type=item["goal_type"],
                target_amount=item["target_amount"],
                current_amount=Decimal("0"),
                monthly_contribution=item["monthly_contribution"],
                linked_account_id=linked_id,
                owner="joint",
                icon=item["icon"],
                color=item["color"],
                sort_order=item["sort_order"],
                is_active=True,
            )
        )
        created += 1
    return created


DEFAULT_ENVELOPES = [
    {
        "name": "Essentials",
        "slug": "essentials",
        "icon": "bi-house-heart",
        "color": "#2dd4bf",
        "sort_order": 1,
        "categories": [
            "Rent",
            "Utilities",
            "Groceries",
            "Fruits & Vegetables",
            "Protein & Supplements",
            "Personal Care & Household",
            "Cook",
            "Fuel & Bike",
            "Auto / Cab",
            "Car",
            "Insurance",
            "Medical",
            "Gym",
            "Misc / Home Buffer",
            # Parents support is optional Joint labelling; usually paid from personal accounts
            "Parents",
        ],
    },
    {
        "name": "Shopping",
        "slug": "shopping",
        "icon": "bi-bag",
        "color": "#fb7185",
        "sort_order": 2,
        "categories": ["Shopping", "Furniture", "Electronics"],
    },
    {
        "name": "Travel",
        "slug": "travel",
        "icon": "bi-airplane",
        "color": "#60a5fa",
        "sort_order": 3,
        "categories": [
            "Travel",
            "Flights",
            "Hotels",
            "Travel Cab / Transport",
        ],
    },
    {
        "name": "Lifestyle",
        "slug": "lifestyle",
        "icon": "bi-stars",
        "color": "#e879f9",
        "sort_order": 4,
        # Dining + movies — household Budget rows, cash pot = Lifestyle envelope
        "categories": ["Dining Out", "Movies & Entertainment"],
    },
    {
        "name": "Unallocated",
        "slug": "unallocated",
        "icon": "bi-inbox",
        "color": "#94a3b8",
        "sort_order": 99,
        "categories": [],
    },
]


def seed_envelopes() -> int:
    """Seed virtual envelopes and map expense categories to them.

    Returns count of new envelopes + newly mapped categories (for commit gating).
    """
    touched = 0
    for item in DEFAULT_ENVELOPES:
        env = Envelope.query.filter_by(slug=item["slug"]).first()
        if not env:
            env = Envelope(
                name=item["name"],
                slug=item["slug"],
                icon=item["icon"],
                color=item["color"],
                current_balance=Decimal("0"),
                is_system=True,
                is_active=True,
                sort_order=item["sort_order"],
            )
            db.session.add(env)
            db.session.flush()
            touched += 1

        for cat_name in item["categories"]:
            category = Category.query.filter_by(name=cat_name, parent_id=None).first()
            if category and category.envelope_id != env.id:
                category.envelope_id = env.id
                touched += 1
    return touched


def migrate_exclude_non_essentials_from_budget() -> int:
    """Parents / Dining / Movies sit outside the Essentials household Budget."""
    cats = Category.query.filter(
        Category.slug.in_(tuple(BUDGET_EXCLUDED_CATEGORY_SLUGS))
    ).all()
    if not cats:
        return 0
    ids = [c.id for c in cats]
    updated = (
        Transaction.query.filter(
            Transaction.category_id.in_(ids),
            Transaction.is_excluded_from_budget.is_(False),
        ).update({Transaction.is_excluded_from_budget: True}, synchronize_session=False)
    )
    # Remove budget lines for these cats (plan lives on Lifestyle / personal)
    Budget.query.filter(Budget.category_id.in_(ids)).delete(synchronize_session=False)
    return int(updated or 0)


def migrate_envelope_category_maps() -> int:
    """Keep category → envelope maps aligned with DEFAULT_ENVELOPES."""
    by_slug = {e.slug: e for e in Envelope.query.filter_by(is_active=True).all()}
    changed = 0
    for item in DEFAULT_ENVELOPES:
        env = by_slug.get(item["slug"])
        if not env:
            continue
        for cat_name in item["categories"]:
            cat = Category.query.filter_by(name=cat_name, parent_id=None).first()
            if cat and cat.envelope_id != env.id:
                cat.envelope_id = env.id
                changed += 1
    return changed


def seed_sample_investments() -> int:
    """No demo holdings — portfolio starts empty; user adds real investments."""
    return 0


# alias (lowercase) → category name as seeded in DEFAULT_CATEGORIES
DEFAULT_TELEGRAM_ALIASES: list[tuple[str, str]] = [
    ("dinner", "Dining Out"),
    ("lunch", "Dining Out"),
    ("breakfast", "Dining Out"),
    ("restaurant", "Dining Out"),
    ("zomato", "Dining Out"),
    ("swiggy", "Dining Out"),
    ("dining", "Dining Out"),
    ("cafe", "Dining Out"),
    ("groceries", "Groceries"),
    ("grocery", "Groceries"),
    ("vegetables", "Fruits & Vegetables"),
    ("fruits", "Fruits & Vegetables"),
    ("petrol", "Fuel & Bike"),
    ("fuel", "Fuel & Bike"),
    ("diesel", "Fuel & Bike"),
    ("bike", "Fuel & Bike"),
    ("rent", "Rent"),
    ("electricity", "Utilities"),
    ("utilities", "Utilities"),
    ("wifi", "Utilities"),
    ("internet", "Utilities"),
    ("movie", "Movies & Entertainment"),
    ("movies", "Movies & Entertainment"),
    ("entertainment", "Movies & Entertainment"),
    ("shopping", "Shopping"),
    ("amazon", "Shopping"),
    ("flipkart", "Shopping"),
    ("headphones", "Shopping"),
    ("electronics", "Shopping"),
    ("travel", "Travel"),
    ("flight", "Flights"),
    ("flights", "Flights"),
    ("hotel", "Hotels"),
    ("cab", "Travel Cab / Transport"),
    ("uber", "Travel Cab / Transport"),
    ("ola", "Travel Cab / Transport"),
    ("health", "Medical"),
    ("gym", "Gym"),
    ("medicine", "Medical"),
    ("protein", "Protein & Supplements"),
    ("misc", "Misc / Home Buffer"),
    ("buffer", "Misc / Home Buffer"),
]


def seed_telegram_aliases() -> int:
    """Seed category keyword aliases for the Telegram parser (idempotent)."""
    from models import Category, TelegramCategoryAlias

    created = 0
    for alias, cat_name in DEFAULT_TELEGRAM_ALIASES:
        key = alias.strip().lower()
        if TelegramCategoryAlias.query.filter_by(alias=key).first():
            continue
        cat = Category.query.filter_by(name=cat_name, parent_id=None).first()
        if not cat:
            continue
        db.session.add(TelegramCategoryAlias(alias=key, category_id=cat.id))
        created += 1
    return created


def migrate_emergency_to_virtual_tags() -> int:
    """
    One-time: Emergency Fund is tagged cash/investments, not a bank account.

    Unlink emergency goals from any linked account so progress uses virtual tags.
    """
    changed = 0
    goals = Goal.query.filter(
        Goal.goal_type == "emergency",
        Goal.linked_account_id.isnot(None),
    ).all()
    for goal in goals:
        goal.linked_account_id = None
        changed += 1
    return changed


def seed_database() -> None:
    """Idempotent seed — safe to run on every startup."""
    renamed = migrate_account_names()
    cats_renamed = migrate_category_names()
    emergency_migrated = migrate_emergency_to_virtual_tags()
    accounts_created = seed_accounts()
    categories_created = seed_categories()
    if (
        accounts_created
        or categories_created
        or renamed
        or cats_renamed
        or emergency_migrated
    ):
        db.session.flush()

    envelopes_created = seed_envelopes()
    envelope_remaps = migrate_envelope_category_maps()
    parents_excluded = migrate_exclude_non_essentials_from_budget()
    budgets_stripped = migrate_strip_envelope_purpose_budgets()
    budgets_created = seed_budgets_for_month()
    goals_created = seed_goals()
    if goals_created:
        db.session.flush()
    investments_created = seed_sample_investments()
    aliases_created = seed_telegram_aliases()

    if (
        renamed
        or cats_renamed
        or emergency_migrated
        or accounts_created
        or categories_created
        or envelopes_created
        or envelope_remaps
        or parents_excluded
        or budgets_stripped
        or budgets_created
        or goals_created
        or investments_created
        or aliases_created
    ):
        db.session.commit()
        logger.info(
            "Seeded renamed=%s cats_renamed=%s emergency_migrated=%s accounts=%s "
            "categories=%s envelopes=%s envelope_remaps=%s parents_excluded=%s "
            "budgets_stripped=%s budgets=%s goals=%s investments=%s aliases=%s",
            renamed,
            cats_renamed,
            emergency_migrated,
            accounts_created,
            categories_created,
            envelopes_created,
            envelope_remaps,
            parents_excluded,
            budgets_stripped,
            budgets_created,
            goals_created,
            investments_created,
            aliases_created,
        )
    else:
        logger.debug("Seed skipped — defaults already present")