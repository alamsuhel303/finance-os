# Finance OS

A personal finance operating system for long-term household money management.

**Stack:** Flask · SQLAlchemy · SQLite · Bootstrap 5 · Chart.js · openpyxl

This is **not** a simple expense tracker — it is designed as a durable finance OS for accounts, envelopes, budgets, investments, goals, and monthly rituals.

---

## Sprint Status

| Sprint | Scope | Status |
|--------|--------|--------|
| **1** | Project setup, SQLite, Dashboard, Transactions, Categories, Accounts | **Done** |
| **2** | Budget, Reports, Charts, Monthly Summary, Backup/Restore | **Done** |
| **3** | Investments, Net Worth, Goals | **Done** |
| **3.5** | Envelopes, SIP/EPF post, NAV refresh, Goal ledgers | **Done** |
| **4a** | Financial Health Score | **Done** |
| **4a+** | Recurring income, month reminders, Insurance, CSV exports | **Done** |
| **4a++** | Excel import, Categories CRUD, Joint funding, Emergency tags, balance rebuild | **Done** |
| **4a+++** | Backup reminder, import dry-run + duplicates, statement wizard, Month checklist | **Done** |
| 4b | Home Planner, Car Planner | Upcoming |
| 5 | FIRE, Buy vs Rent, Tax, AI Insights | Upcoming |

---

## Quick Start

```bash
cd finance-os

python3 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env   # optional — defaults work out of the box

python app.py
```

Open: [http://127.0.0.1:5001](http://127.0.0.1:5001)

> **Note:** Port `5000` is used by macOS AirPlay Receiver and often returns browser **403**. Finance OS defaults to **5001**.

The SQLite database is created automatically at `database/finance.db`.  
Defaults (accounts, categories, envelopes, budgets, goal shells) seed on first launch. Goal targets and investments start empty — fill them in the UI.

For day-to-day how-to, see **[USAGE.md](USAGE.md)**.  
Feature overview stays in this README — no separate features doc.

### Tests

```bash
source .venv/bin/activate
pytest -q
```

---

## Telegram bot

Enter expenses from phone/Mac Telegram using the **same** `transaction_service` as the web UI.

### 1. Create a bot

1. Open Telegram → talk to [@BotFather](https://t.me/BotFather)
2. `/newbot` → copy the token
3. Put it in `.env` (never commit the real token):

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC...your_token
TELEGRAM_TIMEZONE=Asia/Kolkata
```

### 2. Link household members

1. Start the web app: `python app.py`
2. **Settings → Telegram** → generate a link code for Person 1 / Person 2
3. In Telegram, message your bot: `/link ABC123`

Each Telegram account maps to `self` or `wife` (Person 1 / Person 2). The same Telegram login on phone + Mac is one user ID (multi-device works automatically).

### 3. Run the worker (second terminal)

```bash
source .venv/bin/activate
python -m telegram_bot
# or: python scripts/run_telegram_bot.py
```

Keep **both** processes running for live use:

| Terminal | Command |
|----------|---------|
| 1 | `python app.py` |
| 2 | `python -m telegram_bot` |

### Commands

| Command | Purpose |
|---------|---------|
| `/add 450 dinner` | Draft expense → Confirm |
| `/today` | Today's expenses |
| `/recent` | Latest expenses |
| `/month` | Monthly summary |
| `/budget` | Budget vs spent |
| `/envelopes` | Envelope balances |
| `/undo` | Undo last Telegram expense |
| `/status` | Bot + DB health |
| `/link CODE` | Authorize this Telegram account |
| `/unlink` | Remove authorization |
| `/help` | Help |

Plain text also works: `850 dinner`, `Spent 850 at Zomato for dinner`.

Every expense shows a confirmation card (**Confirm / Edit / Cancel**) before it is written to the ledger.

### Offline / restart

If the laptop is off, Telegram keeps undelivered bot updates for a **limited time** (typically up to ~24 hours). When the worker starts again it long-polls and processes them in order, with **idempotency** on `update_id` so duplicates are not double-posted.

SQLite (`telegram_messages`) is the audit trail; Telegram is only a temporary transport.

### Security

- Unknown Telegram users only get an “not authorized” message
- Link codes expire and are single-use
- Callback buttons verify the Telegram user owns the pending draft
- Bot token stays in `.env` / environment only

---

## Architecture

```
finance-os/
├── app.py                 # App factory + entry point
├── config.py              # Environment-aware configuration
├── extensions.py          # Shared extensions (db)
├── models/                # SQLAlchemy models
├── routes/                # Flask blueprints
├── services/              # Business logic (no HTTP concerns)
├── telegram_bot/          # Long-polling Telegram worker
├── tests/                 # pytest
├── utils/                 # Helpers, seed data, schema upgrades
├── templates/             # Jinja templates
├── static/                # CSS / JS
├── database/              # SQLite file (auto-created)
└── backups/               # SQLite snapshots from Settings
```

Web UI and Telegram both call `services/transaction_service.py` (and budget/report services). No duplicate ledger logic.
---

## What’s Built

### Core ledger
- **Accounts** — Personal / Joint (+ funds). Click an account to see its filtered ledger.
- **Statement wizard** — align opening/current to bank balances as of a date.
- **Transactions** — expense, income, transfer, investment, refund. Filters preserved after add/edit/delete.
- **Categories** — manage under Settings (create / rename / deactivate).
- **Excel import** — template with dropdowns; **preview** then confirm; **duplicate skip** (date + amount + description).

### Household cash model
- **Envelopes** — virtual purpose pots (Essentials, Shopping, Travel, Lifestyle, Unallocated) on Joint cash. **Move between pots** corrects labels after Fund Joint without moving bank money.
- **Joint funding plan** — Settings: Person 1 + Person 2 amounts + shared envelope plan (include Lifestyle for dining). Leftover → Unallocated. Dashboard / Month “Fund Joint” posts both transfers (plan edits after posting need Move between pots for the current month).
- **Emergency Fund** — virtual tags on spending accounts + investments marked for Emergency goal (no separate transfer account required).

### Planning & wealth
- **Budget** — Essentials household plan (~₹1.5L: Rent, groceries…). Dining/movies → Lifestyle envelope; Parents support stays off Budget.
- **Investments** — holdings, SIP post, NAV/value refresh, goal linkage.
- **Net Worth** — cash + investments − liabilities; monthly snapshots.
- **Goals** — Emergency, Home, Travel, Car, Retirement; progress includes linked cash + tagged holdings.
- **Insurance** — policies + renewal reminders.
- **Financial Health** — weighted 0–100 score on the Dashboard.
- **Month checklist** — nav **Month**: income → Fund Joint → SIPs → snapshot.

### Settings
- Recurring income templates + one-click monthly post
- Joint funding plan
- Categories
- Backup / restore SQLite + **stale-backup Dashboard reminder** (`BACKUP_MAX_AGE_DAYS`)
- CSV exports (transactions, accounts, net worth, investments)
- **Recalculate balances** — rebuilds `current_balance` from opening + ledger if drift occurs

---

## Design Notes

1. **Balances are ledger-driven** — prefer transactions over editing `current_balance`. If numbers look wrong, use **Settings → Recalculate balances**.
2. **Cash vs purpose** — real money lives in accounts; envelopes only label Joint purpose.
3. **Self/Wife → Joint** with no split defaults to 100% Essentials; Joint funding plan uses a shared envelope allocation (pro-rated across both transfers).
4. **Net worth** prefers investment holdings over the Investment Account balance when holdings exist.
5. **Goals** use effective current = linked cash + holdings tagged to that goal.
6. **Snapshots** are point-in-time — record monthly for Net Worth growth charts.

---

## Financial Health Score

Weighted 0–100 gauge on the Dashboard:

| Factor | Weight | Measures |
|--------|--------|----------|
| Emergency fund | 20% | Months of expenses covered (target 6) |
| Savings rate | 20% | (Income − expenses) ÷ income |
| Investment rate | 15% | Investment txns ÷ income |
| Debt load | 15% | Liabilities vs annual income |
| Goal progress | 15% | Average progress on targeted goals |
| Budget discipline | 15% | Spend vs category limits |

---

## Configuration

Optional `.env` (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `FLASK_ENV` | `development` | Config profile |
| `SECRET_KEY` | (dev default) | Flask sessions |
| `DATABASE_URL` | `sqlite:///database/finance.db` | DB path |
| `CURRENCY_SYMBOL` | `₹` | Display |
| `CURRENCY_CODE` | `INR` | Display |
| `TELEGRAM_ENABLED` | `false` | Prefer `true` when running the bot |
| `TELEGRAM_BOT_TOKEN` | _(empty)_ | From BotFather — secret |
| `TELEGRAM_POLL_TIMEOUT` | `30` | Long-poll seconds |
| `TELEGRAM_TIMEZONE` | `Asia/Kolkata` | Today / yesterday for expenses |
| `TELEGRAM_NOTIFY_STARTUP` | `false` | Do not spam chats on restart |
| `TELEGRAM_PENDING_TTL_MINUTES` | `60` | Confirm draft expiry |
| `TELEGRAM_LINK_CODE_TTL_MINUTES` | `30` | Settings link-code expiry |

---

## Next Up — Sprint 4b

Home Planner and Car Planner (purchase / EMI what-if tools).
