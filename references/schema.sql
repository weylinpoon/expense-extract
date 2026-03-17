-- expense-extract schema
--
-- This file is the single source of truth for CREATE TABLE/INDEX statements.
-- Scripts should call scripts/db.py:ensure_schema() (which executes this file)
-- and then runs best-effort additive migrations for older DBs.

-- expenses table
CREATE TABLE IF NOT EXISTS expenses (
  expense_id TEXT PRIMARY KEY,
  source_type TEXT,
  source_ref TEXT,

  entity TEXT,
  entity_source TEXT,
  group_name TEXT,
  entity_confidence REAL,

  vendor TEXT,
  vendor_normalized TEXT,
  invoice_number TEXT,
  expense_date TEXT,              -- ISO-8601 YYYY-MM-DD when known

  subtotal TEXT,                  -- store as captured string (never invent/round)
  tax TEXT,
  total TEXT,

  currency TEXT,                  -- ISO 4217 when known
  currency_source TEXT,           -- explicit_document_currency|currency_symbol_and_context|receipt_address|unknown

  category TEXT,                  -- allowed set enforced by app logic
  needs_review INTEGER NOT NULL DEFAULT 0,
  review_reason TEXT,
  confidence REAL,

  -- linkage back to ingest sources (optional)
  source_id TEXT,
  email_message_id TEXT,
  extraction_source TEXT,

  raw_json TEXT NOT NULL,         -- JSON string blob of saved record
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Helpful index for duplicate detection (include date for stronger matching)
CREATE INDEX IF NOT EXISTS idx_expenses_dupe_check
ON expenses (invoice_number, vendor_normalized, total, expense_date);

-- Helpful index for entity queries
CREATE INDEX IF NOT EXISTS idx_expenses_entity
ON expenses (entity, group_name);


-- email_sources table (for inbox/message dedupe + provenance)
CREATE TABLE IF NOT EXISTS email_sources (
  source_id TEXT PRIMARY KEY,
  email_message_id TEXT,
  thread_id TEXT,
  email_from TEXT,
  email_to TEXT,
  email_cc TEXT,
  subject TEXT,
  received_at TEXT,
  has_attachments INTEGER NOT NULL DEFAULT 0,
  selected_attachment_name TEXT,
  triage_status TEXT,
  triage_reason TEXT,
  entity TEXT,
  entity_source TEXT,
  processing_status TEXT,
  processed_at TEXT,
  linked_expense_id TEXT,
  source_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_sources_message_id_unique
ON email_sources (email_message_id)
WHERE email_message_id IS NOT NULL AND email_message_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_email_sources_source_hash_unique
ON email_sources (source_hash);


-- Dynamic entity rules
-- One table supports both rule types: recipient_email and vendor_normalized.
CREATE TABLE IF NOT EXISTS entity_rules (
  rule_id TEXT PRIMARY KEY,
  rule_type TEXT NOT NULL,        -- recipient_email | vendor_normalized
  rule_key TEXT NOT NULL,         -- email address (lowercased) OR vendor_normalized
  entity TEXT NOT NULL,
  group_name TEXT,
  source TEXT NOT NULL,           -- seeded_yaml | learned_from_recipient | user_correction
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- Ensure a single active rule per (type,key)
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_rules_type_key
ON entity_rules (rule_type, rule_key);

-- Lookup accelerators
CREATE INDEX IF NOT EXISTS idx_entity_rules_vendor
ON entity_rules (rule_type, rule_key);
