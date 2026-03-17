# expense-extract

Extract bookkeeping fields from receipts/invoices (images, PDFs, email attachments) and persist them in a local SQLite database.

This skill is designed for **deterministic ingestion** (no guessing when ambiguous) plus a clean path for **LLM enrichment**.

Important: the scripts in this repo **do not call an LLM**. Instead, you (or an agent) run an LLM separately and then write the decided fields back into SQLite using `update_expense.py`.

## What it does

- Saves expenses into SQLite (`expenses` table) and tracks source messages/files (`email_sources` table).
- Supports:
  - inbox/email processing (`process_inbox.py`)
  - insert/save (`save_expense.py`)
  - update fields (`update_expense.py`)
  - delete (also deletes associated `email_sources`) (`delete_expense.py`)
  - query/list (`query_expenses.py`)

## How the “LLM enrichment” flow works (scripts never call an LLM)

1) **Deterministic extract** (receipt/invoice/email → draft in SQLite)
- Extract what’s unambiguous.
- If key fields are missing/unclear, save a draft with `needs_review=1`.

2) **LLM (or human) decides missing fields**
- This happens *outside* these scripts (e.g., an OpenClaw agent run, manual review, etc.).

3) **Write the decisions back** using `update_expense.py`
- `update_expense.py` updates SQLite; it does not run an LLM.

4) **Query / export** using `query_expenses.py`

## Install

### Option A: Copy into OpenClaw skills directory (recommended)

```bash
# from your cloned Openclaw repo
cp -R skills/expense-extract ~/.openclaw/skills/

# sanity check
~/.openclaw/skills/expense-extract/scripts/doctor.py --print-config
~/.openclaw/skills/expense-extract/scripts/self_check.py

# optional: print merged config and see which override file was used
~/.openclaw/skills/expense-extract/scripts/print_config.py
```

### Option B: Use the installer script

```bash
cd skills/expense-extract
./scripts/install.py
./scripts/doctor.py
```

## Quickstart

> Assumes you are in this directory.

### 1) Pick a database path

Common choices:
- a dedicated DB for the agent: `./expenses.sqlite`
- or a shared DB under OpenClaw memory: `~/.openclaw/memory/warren.sqlite` (your setup may vary)

### 2) Query recent expenses

```bash
./scripts/query_expenses.py --db ./expenses.sqlite
```

Show review-needed:

```bash
./scripts/query_expenses.py --db ./expenses.sqlite --needing-review
```

Show one expense:

```bash
./scripts/query_expenses.py --db ./expenses.sqlite --expense-id <EXPENSE_ID>
```

### 3) Update/enrich an expense (LLM-assisted workflow)

`update_expense.py` is a **deterministic updater**: it does not invoke an LLM. It just applies fields you already decided (manually or via an LLM run elsewhere).

```bash
./scripts/update_expense.py \
  --db ./expenses.sqlite \
  --expense-id <EXPENSE_ID> \
  --set-json '{"vendor_normalized":"Google Cloud Canada","expense_date":"2025-06-30","invoice_number":"5306125943","total":"0.00","currency":"CAD","needs_review":false,"review_reason":null}'
```

Clear review flags only:

```bash
./scripts/update_expense.py --db ./expenses.sqlite --expense-id <EXPENSE_ID> --set-json '{}' --clear-review
```

### 4) Delete an expense (and its sources)

```bash
./scripts/delete_expense.py --db ./expenses.sqlite --expense-id <EXPENSE_ID>
```

## Configuration (recommended)

This skill supports **upgrade-safe local overrides**.

Config precedence (lowest → highest):
1. `defaults.json` (bundled with the skill)
2. `~/.openclaw/config/expense-extract.json` (recommended)
3. `$EXPENSE_EXTRACT_CONFIG` (path to a JSON file)


Example override file:

```json
{
  "allowed_categories": ["Software", "Utilities", "Travel"],
  "allowed_currencies": ["CAD", "USD"],
  "query_default_limit": 25
}
```

See `defaults.json` for the full key list.

Print the resolved config (including which override file was used):

```bash
./scripts/print_config.py
```

Run a lightweight integration self-check:

```bash
./scripts/self_check.py
```

## Schema

The SQLite schema lives in `references/schema.sql` (single source of truth). Scripts run a best-effort additive migration layer on top for older DBs.

## Notes / design constraints

- **Deterministic scripts should not guess** vendor/date/total/currency/category when ambiguous. Save drafts and mark `needs_review`.
- Use `update_expense.py` to apply decisions (from a human or an LLM run elsewhere) to canonical columns.
- Deleting an expense must also remove associated rows in `email_sources`.

## Repo layout

- `SKILL.md` — skill instructions + behavior
- `defaults.json` — default policy/config
- `scripts/` — deterministic CLI scripts used by the skill
