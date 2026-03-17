#!/usr/bin/env python3
"""db.py

Shared SQLite schema + best-effort migration helpers for expense-extract.

Design:
- `references/schema.sql` is the single source of truth for CREATE TABLE/INDEX.
- Because SQLite can't `ALTER TABLE ADD COLUMN IF NOT EXISTS`, we still keep a
  small migration layer to add new columns to pre-existing DBs.

All scripts should call `ensure_schema(con)` on startup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SCHEMA_PATH = SKILL_DIR / "references" / "schema.sql"


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {r[1] for r in rows}


def _add_column_if_missing(con: sqlite3.Connection, table: str, name: str, decl: str):
    cols = _table_columns(con, table)
    if name in cols:
        return
    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_schema(con: sqlite3.Connection):
    """Create tables/indexes and migrate older DBs forward (best effort)."""

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    con.executescript(schema_sql)

    # Best-effort migrations for older DBs.
    # NOTE: schema.sql may evolve faster than deployed DBs; keep this list small
    # and additive only.

    # expenses: add columns that were introduced after initial deploys
    _add_column_if_missing(con, "expenses", "entity_confidence", "REAL")
    _add_column_if_missing(con, "expenses", "source_id", "TEXT")
    _add_column_if_missing(con, "expenses", "email_message_id", "TEXT")
    _add_column_if_missing(con, "expenses", "extraction_source", "TEXT")

    # email_sources: additive columns (if schema expands later)
    # (currently none beyond schema.sql)

    # Helpful index: ensure entity index exists even on older DBs
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_expenses_entity ON expenses (entity, group_name)")
    except sqlite3.OperationalError:
        pass
