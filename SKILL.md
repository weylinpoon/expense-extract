---
name: expense-extract
description: Extract structured bookkeeping fields from receipts, invoices, and expense documents (PDFs, images, scans, email attachments, screenshots). Use when asked to parse or summarize expenses for accounting/bookkeeping, including vendor, vendor_normalized, invoice number, expense date, subtotal, tax, total, currency + currency_source, description, category classification, entity assignment (entity, group_name, entity_source, entity_confidence) with dynamic entity rules (seed from entity_rules.yaml, persist/learn in SQLite entity_rules table), confidence scoring, reconciliation/needs_review flags, persisting each extracted record into a local SQLite database, and querying recent/specific/review-needed expenses.
---

# Expense Extract

Extract fields from the document, persist the extracted record locally (SQLite).

## Output schema (expense JSON)

For **extraction/saving**, return exactly one JSON object:

```json
{
  "expense_id": null,
  "source_type": null,
  "source_ref": null,

  "entity": null,
  "group_name": null,
  "entity_source": null,
  "entity_confidence": null,

  "vendor": null,
  "vendor_normalized": null,
  "invoice_number": null,
  "expense_date": null,
  "subtotal": null,
  "tax": null,
  "total": null,
  "currency": null,
  "currency_source": null,
  "description": null,
  "category": null,
  "confidence": null,
  "needs_review": false,
  "review_reason": null
}
```

## Enrichment completion confirmation

After running inbox processing + LLM enrichment, always send a confirmation summary (human-readable) with:
- emails ingested
- expenses created
- expenses enriched
- expenses still needing review (count + top reasons)

This confirmation is required by default (do not wait for the user to ask).

## Deleting expenses (important)

When deleting an expense, also delete the associated `email_sources` row(s) so the inbox processor can re-process that email if needed.

Association:
- Prefer matching by `expenses.source_id` → `email_sources.source_id`.
- Otherwise match by `expenses.email_message_id` → `email_sources.email_message_id`.

Implementation: use `scripts/delete_expense.py`.

## Query output formatting (default)

When **showing expenses from the database** (e.g. “show recent expenses”, “show expenses needing review”, “show expense <id>”), default to a **user-friendly formatted list** (1 line per record) to avoid dumping long JSON keys.

- If the user explicitly asks for “full details”, “raw JSON”, or “show the full record”, then return JSON.

Implementation: `query_expenses.py` defaults to formatted text; pass `--json` for machine-readable JSON.

## LLM category guessing during inbox processing

When processing the inbox, the agent should use an LLM to assign a category **only when** the extracted category is `Uncategorized`.

- Controlled via `defaults.json`:
  - `llm_category_guess_enabled` (bool)
  - `llm_category_guess_only_if_uncategorized` (bool; should remain true)
  - `llm_category_review_threshold` (float 0..1)

If the guessed category confidence is below the threshold, set `needs_review=true` and record a review reason.

## LLM enrichment during inbox processing (vendor + amounts + dates)

For inbox processing, the script (`process_inbox.py`) should focus on **deterministic ingestion** (download attachment, extract text, dedupe, store context) and avoid making semantic choices.

**Hard rule:** The script must NOT decide canonical bookkeeping fields when ambiguity is possible (vendor, invoice/date, totals, currency, category). It should store candidates in `raw_json` only.

The agent (LLM) should then enrich each saved draft expense by deciding:
- `vendor` / `vendor_normalized`
- `invoice_number`
- `expense_date` (choose the correct bookkeeping date)
- `total` (choose the correct total)
- `currency`
- `category` (only if Uncategorized, per settings)

### Total selection rule
When both `Total Paid` and `Amount Due` exist:
- If `Total Paid == Amount Due` (within a small tolerance), use **Total Paid**.
- Otherwise, use **Amount Due**.

This is configured in `defaults.json` as:
- `llm_total_choice_rule: "prefer_total_paid_if_equal_else_amount_due"`

### Controls (defaults.json)
- `llm_vendor_guess_enabled` (bool)
- `llm_vendor_guess_mode` ("always")
- `llm_vendor_review_threshold` (float 0..1)
- `llm_field_enrichment_enabled` (bool)
- `llm_field_review_threshold` (float 0..1)

### Implementation
- `process_inbox.py` stores context into `raw_json` (e.g. extracted text excerpt + email metadata)
- The agent applies enrichment after insertion using:
  - `update_expense.py`

If confidence is below the relevant threshold, set `needs_review=true` and record a review reason.

**Enforcement:** Treat any non-LLM heuristic overwrite of vendor/date/total/currency on inbox-created draft expenses as a bug. Use the LLM enrichment step + `update_expense.py` only.

## General extraction rules

- Use `null` when a value is missing, illegible, or not present.
- Never invent numbers or “fill in” missing amounts.
- Prefer values explicitly labeled on the document (e.g., “Invoice #”, “Date”, “Subtotal”, “Tax”, “Total”).
- Dates: return as ISO-8601 `YYYY-MM-DD` when the document makes the date unambiguous; otherwise `null`.
- Amounts (`subtotal`, `tax`, `total`): return as strings exactly as read (e.g., `"123.45"`). Do not round.
- `description`: short plain-language description derived from the document text (do not add facts not present).

## Entity assignment (dynamic rules)

Assign each expense to an `entity` (and optional `group_name`). Return `entity_source` (how the entity was determined) and `entity_confidence` (0..1).

### entity_source allowed values

Return `entity_source` as exactly one of:

- `user_override`
- `recipient_email_rule`
- `vendor_rule`
- `conversation_context`
- `unknown`

### Rule precedence (highest → lowest)

Determine `entity` using this precedence order:

1. **User override** (explicit user-provided entity)
2. **Recipient-email rule** (dynamic rule)
3. **Vendor rule** (dynamic rule)
4. **Conversation context**

If entity is still unclear:

- `entity = null`
- `entity_source = "unknown"`
- `entity_confidence = 0.0`
- `needs_review = true`
- add an entity-unclear note to `review_reason`

### Seed rules from entity_rules.yaml

Load initial recipient-email mappings from the YAML file bundled with this skill:

- `entity_rules.yaml`

Format:

```yaml
recipients:
  "ap@company.com":
    entity: "Company Inc"
    group_name: "Company Group"
```

Rules:

- Match recipient email addresses case-insensitively.
- Prefer recipient-email rules over vendor rules.

### Persist rules in SQLite (entity_rules table)

Store rules in a SQLite table called `entity_rules`.

Supported rule types:

- `recipient_email` (key = email address, lowercased)
- `vendor_normalized` (key = vendor_normalized)

### Learning rules (automatic)

- When an entity is determined from a **recipient-email rule**, also store (or upsert) a **vendor rule** mapping:
  - `vendor_normalized → entity, group_name`
  - Mark rule source as `learned_from_recipient`.

- When a user **corrects** an entity assignment, store (or upsert) a **vendor rule** mapping:
  - `vendor_normalized → corrected entity, group_name`
  - Mark rule source as `user_correction`.

Notes:

- Do not create vendor rules if `vendor_normalized` is null.
- Never create rules when `entity` is null.

### entity_confidence guidance

Set `entity_confidence` based on how deterministic the assignment was:

- `1.0` for user override.
- `0.9–1.0` for an exact recipient-email rule match.
- `0.8–0.9` for a vendor rule match.
- `0.5–0.7` for conversation-context inference.
- `0.0` when unknown.

If `entity_confidence < 0.8`, set `needs_review = true` and add `low_entity_confidence` to `review_reason`.

## Vendor normalization (vendor_normalized)

- `vendor`: vendor/merchant name as shown.
- `vendor_normalized`: cleaned canonical name for reporting.

## Currency detection (currency + currency_source)

Return `currency` as ISO 4217 when determinable, else `null`.

Return `currency_source` as exactly one of:

- `explicit_document_currency`
- `currency_symbol_and_context`
- `receipt_address`
- `unknown`

## Category classification

Allowed categories are **configurable**.

- Skill defaults live in: `defaults.json` (key: `allowed_categories`)
- User override file (recommended): `~/.openclaw/config/expense-extract.json`
  - Any keys here override `defaults.json`.
  - You can also point to a different file with the env var `EXPENSE_EXTRACT_CONFIG`.
  - Back-compat: we also read `~/.openclaw/config/bookkeeping-expense-extractor.json` and `BOOKKEEPING_EXPENSE_EXTRACTOR_CONFIG`.

If `allowed_categories` is set, scripts will flag `invalid_category` during save if a category is outside the list.

Validation is also configurable:
- `allowed_currencies` → flags `invalid_currency`
- `allowed_currency_sources` → flags `invalid_currency_source`
- `allowed_entity_sources` → flags `invalid_entity_source`

Draft ingestion is configurable:
- `draft_review_reason` (default: `needs_llm_enrichment`) — review_reason set on inbox-created draft expenses.

Query formatting is configurable:
- `query_default_limit` (default: 10) — default `--limit` when not provided
- `query_desc_max_len` (default: 80) — max description length in formatted list output

## Persistence (SQLite)

Persist expenses into the local SQLite database.

Also persist entity rules into `entity_rules`.

See `references/schema.sql` for table definitions.
