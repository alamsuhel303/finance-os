# Finance OS — Usage Guide

Day-to-day how-to for the household money OS.  
App URL after `python app.py`: [http://127.0.0.1:5001](http://127.0.0.1:5001)

For a feature overview (what exists), see [README.md](README.md). This file is **how to use it**.

---

## Mental model

| Concept | Meaning |
|---------|---------|
| **Account** | Real cash (Suhel, Seema, Joint, Cash…) |
| **Envelope** | Purpose label on Joint money (Essentials, Shopping, Travel, Lifestyle…) |
| **Category** | What the spend/income was (Groceries, Salary…) |
| **Budget** | Essentials household plan (~₹1.5L: Rent, groceries…). Not Dining/Parents |
| **Goal** | Long-term target (Emergency, Home…) |

Money moves with **transactions**. Envelopes do not move bank cash by themselves.

---

## First-time setup

1. Start the app and open the Dashboard.
2. **Accounts → Statement wizard** (or edit each account) — set balances to match your bank as of today.
3. **Settings → Recurring Income** — add Suhel / Seema salary templates (amount, credit account, day).
4. **Settings → Joint Funding** — set monthly contributions + shared envelope plan (see below).
5. **Settings → Categories** — add any missing labels.
6. **Settings → Create Backup**, then copy `backups/` off this Mac.
7. Optional: **Insurance**, **Investments**, **Goals**, **Budget** limits for the current month.

---

## Month checklist

Nav **Month** (or Dashboard → **Month**) — one page for the monthly ritual:

| Step | What it does |
|------|----------------|
| 1. Post income | Creates salary/credit transactions from templates |
| 2. Fund Joint | Suhel + Seema → Joint with shared envelope plan |
| 3. Post SIPs | Due SIP contributions (if configured) |
| 4. Net worth snapshot | Locks the month for growth charts |

Statuses: **Ready** / **Done** / **Needs setup** / **Optional**.  
Post buttons return you to the checklist when you started from there.

Dashboard also shows the same items as reminders, plus backup and insurance when relevant.

---

## Accounts

- **Accounts** page lists spending accounts and fund accounts.
- Click an account name/balance to open **Transactions filtered to that account**. Filters stay after add/edit/delete.
- **Emergency tags** — tag how much of Suhel/Seema/Joint/FD cash counts as emergency (no transfer). Unlock with **Edit emergency tags**, save to lock.
- Investments can also count toward Emergency when their purpose/goal is Emergency Fund.

### Statement wizard

**Accounts → Statement wizard**

Use when your books don’t match the bank, without guessing “current balance” by hand.

1. Pick **Statement as of** date (e.g. today or statement date).
2. Enter the balance the bank shows for each account; leave blank to skip.
3. **Apply** — the app adjusts **opening** so that:
   - implied balance on that date = your statement figure
   - transactions **after** that date still change **current**

This does **not** invent transactions. Prefer this over editing current balance directly.

---

## Transactions

### Add one
**Transactions → Add**, or from a filtered account ledger (account is pre-selected).

| Type | Effect |
|------|--------|
| Expense | Decreases source account; can spend an envelope if from Joint |
| Income | Increases account |
| Transfer | Moves cash between accounts; Self/Wife → Joint can split into envelopes |
| Investment | Outflow from cash + updates linked holding when set |
| Refund | Increases account |

### Excel import (preview + duplicates)

1. **Transactions → Import Excel → Download template** (re-download after category changes).
2. Fill the **Transactions** sheet — green columns auto-fill Joint Account, expense, UPI; envelope follows category. You mainly need date, amount, description, category.
3. **Preview import** — review rows:
   - **Ready** — will be created on confirm
   - **Duplicate** — same date + amount + description as an existing txn (or another row in the file); skipped by default
   - **Error** — validation failed (bad account name, etc.)
4. **Confirm import** — only Ready rows are written. Nothing is saved until you confirm.
5. Re-download the template after you add accounts/categories so dropdowns stay current.

### Manual transfer split
On a transfer to Joint, use **Envelope split** rows, or leave empty to default **100% Essentials**.

---

## Joint funding plan

**Settings → Joint Funding** (or Month checklist / Dashboard **Fund Joint** when ready).

1. **Who contributes** — e.g. Suhel ₹30,000 + Seema ₹70,000 = Joint total ₹1,00,000.
2. **Shared envelope plan** — e.g. Essentials 1.35L, Shopping 45k, Travel 45k, Lifestyle 15k.  
   Leftover automatically goes to **Unallocated**.
3. You do **not** enter a separate envelope split per person.
4. **Post Fund Joint** creates both transfers and pro-rates the envelope plan across them (idempotent per person/month).
5. **Changing the plan after you already posted** does not rewrite this month’s pots — use **Move between pots** (below) or wait for next month’s post.

One-off transfers can still use the manual split on the transaction form.

---

## Envelopes & Budget

### Mental model

| | Budget | Envelopes |
|---|---|---|
| What | Monthly **limits** for the Essentials household plan | **Labels** on cash in Joint |
| Typical total | ~₹1.5L (Rent, groceries, utilities, cook…) | Funded when you Post Fund Joint |
| Dining / movies | Not on Budget | Fund **Lifestyle** pot (~₹15k) |
| Parents (Suhel/Seema) | Not on Budget | No envelope (personal account) |
| Shop / travel | Not on Budget | **Shopping** / **Travel** pots |

- Pay from **Joint** → expense can reduce that category’s envelope (e.g. Dining → Lifestyle).
- Pay from **Suhel / Seema** → counts in history / personal balance; **Budget** only if the category is on the Essentials plan; **no** envelope hit.

### Envelopes page

- See allocated / spent / available for each Joint pot.
- **Click an envelope** — month activity (Fund Joint allocations, Joint spends, moves). Prev/Next months; pencil opens the linked transaction when there is one.

### Move between pots

Use when Fund Joint already posted but the split was wrong (e.g. forgot Lifestyle).

1. Open **Envelopes**.
2. Scroll to **Move between pots**.
3. Choose **From** (e.g. Essentials), **To** (e.g. Lifestyle), **Amount**, optional note.
4. Click **Move** and confirm.

This only relabels Joint purpose pots — **bank balances do not change**. It is a one-off correction; it does not auto-run every month. Next month, include Lifestyle in the Joint funding plan and Post Fund Joint as usual.

### Budget page

- Essentials household category limits only.
- Click a category → that month’s transactions.
- Warning appears if category budgets exceed what you funded in a linked pot (usually after Dining was still on Budget; Dining is envelope-only now).

---

## Investments

- Add holdings (MF, SIP, stock, EPF, FD…).
- **Post this month** — posts due SIPs as investment transactions (also on Month checklist).
- **Refresh values** — updates NAVs/values where configured.
- Link a holding’s purpose/goal (e.g. Emergency, Home) so Goals and Emergency totals stay accurate.

---

## Goals & Net Worth

- **Goals** — set targets and monthly contribution; progress uses linked cash + tagged holdings.
- **Net Worth** — live total; hide noise from inactive/zero accounts; record a snapshot each month for history charts.
- **Liabilities** — loans/cards reduce net worth.

---

## Insurance

Track policies and renewal dates. Due-soon policies appear in Dashboard reminders and on the Month page under “Other reminders”.

---

## Settings cheat sheet

| Action | Where |
|--------|--------|
| Salary templates / post income | Settings → Recurring Income (also Month) |
| Joint funding plan / post | Settings → Joint Funding (also Month / Dashboard) |
| Add/edit categories | Settings → Manage Categories |
| Backup DB | Settings → Create Backup |
| Restore DB | Settings → Restore (type `RESTORE`) |
| Fix wrong balances | Settings → Recalculate balances |
| Align to bank statement | Accounts → Statement wizard |
| CSV downloads | Settings → Export CSV |
| Month ritual | Nav → Month |

---

## If balances look wrong

1. Prefer fixing via **transactions**, not by inventing a new current balance.
2. Or use **Accounts → Statement wizard** with today’s bank figures.
3. Confirm **Opening balance** is a sensible starting point.
4. **Settings → Recalculate balances** — sets  
   `current = opening + all ledger effects`  
   for every account.

Joint can show negative if expenses hit Joint before you post **Fund Joint** — that is expected until funding is posted.

---

## Backup habit

### Reminder
If the newest local backup is older than **`BACKUP_MAX_AGE_DAYS`** (default **7**, set in `.env`), the Dashboard shows a **Backup your data** nudge with Create Backup + Settings. The message includes the folder path to copy off-Mac.

### In the app
Before bulk imports or big edits: **Settings → Create Backup** (optional label).  
Files land in `backups/` as `finance_os_….db`. Success flash also reminds you to copy off this Mac.

**Restore:** Settings → Restore → type `RESTORE`. Restart the app afterward if balances look stale.

### Off the Mac (important)
App backups stay on this machine. Git does **not** track `database/*.db` or `backups/*.db`.

Copy these somewhere safe (external drive, iCloud, Google Drive, another laptop):

| Path | What it is |
|------|------------|
| `database/finance.db` | Live database (latest state) |
| `backups/*.db` | Point-in-time snapshots from Settings |

Suggested habit: after the Month checklist (income + Fund Joint), Create Backup, then copy the newest `backups/*.db` (or the whole `backups/` folder) off-device.

To restore from an offline copy: put the `.db` file into `backups/` and use Settings → Restore, or stop the app and replace `database/finance.db` with the copy (keep a safety rename of the old file first).

---

## Excel import — column cheat sheet

Download a fresh template from **Transactions → Import Excel** so dropdowns match your live accounts/categories.

| Column | Required | Notes |
|--------|----------|--------|
| `date` | Yes | `YYYY-MM-DD` or a normal Excel date cell |
| `amount` | Yes | Positive number |
| `description` | Yes | What the txn is (used for duplicate detection) |
| `type` | Yes | Dropdown: `expense` / `income` / `transfer` / `investment` / `refund` |
| `account` | Yes | Dropdown — source account (exact name) |
| `to_account` | Transfers | Dropdown — destination account |
| `category` | No | Dropdown — your categories |
| `paid_by` | No | `self` / `wife` / `joint` |
| `payment_mode` | No | e.g. `upi`, `card`, `netbanking`, `cash`, `auto_debit`, `cheque`, `other` |
| `need_want` | No | `need` / `want` / `n/a` |
| `envelope` | No | Dropdown — purpose pot (mainly Joint spend) |
| `notes` | No | Free text |

Sheets in the template: **Transactions** (fill here), **Instructions**, **Reference** (lists that power dropdowns).  
Only `.xlsx` is supported. Re-download the template after you add accounts or categories.

**Duplicate rule:** same `date` + `amount` + `description` (case/spacing-insensitive) as an existing transaction, or repeated in the file → marked Duplicate and skipped on confirm (when “Skip duplicates” is on).

---

## FAQ

**Why is Lifestyle negative but Budget looks fine?**  
Dining is off Budget; it draws the **Lifestyle** envelope when paid from Joint. Fund Lifestyle in the Joint plan (e.g. ₹15k). If you already posted Fund Joint this month without Lifestyle, use **Envelopes → Move between pots** (Essentials → Lifestyle).

**Why is Joint negative?**  
Expenses hit Joint before you posted **Fund Joint**. Post funding (or a manual transfer in) — the balance catches up. Recalculate is not required for this case.

**I opened Suhel’s ledger, then after saving a txn I see everyone’s transactions.**  
Account (and other) filters are kept after add/edit/delete. If you still see everything, check the account filter dropdown or use **Clear filter**.

**When should I Recalculate balances?**  
When an account’s shown balance doesn’t match opening + its transactions. Prefer **Statement wizard** if you have bank figures; use Recalculate for drift cleanup.

**Statement wizard vs editing Opening on the account form?**  
Wizard is for “bank says X on date D” across several accounts. Single-account Opening edit is fine for a one-off start balance.

**Should I edit Opening balance or Current balance?**  
Edit **Opening** (or use the wizard). Don’t invent Current by hand.

**Excel preview showed Ready but Confirm created nothing?**  
Preview session may have expired — upload and preview again, then confirm soon after.

**Import skipped a row as duplicate but I want it anyway?**  
Uncheck **Skip duplicates** on upload/preview only if you truly need a second identical txn (rare).

**Fund Joint / Post income did nothing.**  
Already posted for this month (idempotent), or amounts are zero / plan inactive. Check Month checklist or Settings status (Ready vs Posted).

**Backup reminder won’t go away.**  
Create a backup in Settings. Reminder returns after `BACKUP_MAX_AGE_DAYS` without a newer file. Still copy `backups/` off-Mac — the reminder is local-only.

**Where is my data if I reinstall the Mac?**  
Only where you copied it. Keep offline copies of `finance.db` / `backups/`.

---

## Tips

- Use **Month** at the start of each month, then spend through Dashboard / Transactions.
- Use **account-filtered** ledgers when reconciling a bank statement.
- Keep category names stable — Excel import and budgets match by name.
- After adding categories/accounts, re-download the Excel import template.
- Dashboard privacy toggle hides sensitive amounts on screen when needed.
