#!/usr/bin/env python3
"""update_expense.py

Single unified updater for expense records.

Updates both table columns (when present) and `raw_json` in ONE transaction.
Designed to be called by the agent after LLM enrichment.

Inputs:
- --db <path>
- --expense-id <id>
- --set-json '<json object>'  (recommended)
  Keys may include any of:
    vendor, vendor_normalized, vendor_confidence
    invoice_number, expense_date, subtotal, tax, total
    currency, currency_source
    description
    category, category_confidence
    entity, entity_source, entity_confidence, group_name
    needs_review (bool), review_reason
    extraction_source, email_message_id, source_id
  Any extra keys are merged into raw_json but ignored for table columns.

Optional:
- --clear-review : sets needs_review=0, review_reason=NULL (and raw_json equivalents)

Output:
- JSON only.
"""

import argparse
import json
import sqlite3

from db import ensure_schema


COLUMN_KEYS = {
    "source_type",
    "source_ref",
    "source_id",
    "email_message_id",
    "extraction_source",
    "entity",
    "entity_source",
    "entity_confidence",
    "group_name",
    "vendor",
    "vendor_normalized",
    "invoice_number",
    "expense_date",
    "subtotal",
    "tax",
    "total",
    "currency",
    "currency_source",
    "description",
    "category",
    "needs_review",
    "review_reason",
    "confidence",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--expense-id", required=True)
    ap.add_argument("--set-json", required=True, help="JSON object of fields to set")
    ap.add_argument("--clear-review", action="store_true")
    args = ap.parse_args()

    try:
        patch = json.loads(args.set_json)
        if not isinstance(patch, dict):
            raise ValueError("set-json must be a JSON object")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"invalid_set_json: {str(e)}"}))
        return

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")
    ensure_schema(con)

    row = con.execute(
        "SELECT raw_json FROM expenses WHERE expense_id=? LIMIT 1", (args.expense_id,)
    ).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": "expense_not_found", "expense_id": args.expense_id}))
        return

    raw_text = row[0]
    try:
        raw = json.loads(raw_text) if raw_text else {}
    except Exception:
        raw = {}

    cols = {r[1] for r in con.execute("PRAGMA table_info(expenses)").fetchall()}

    updates = {}
    # normalize needs_review bool to int for sqlite
    if "needs_review" in patch and isinstance(patch["needs_review"], bool):
        patch["needs_review"] = 1 if patch["needs_review"] else 0

    for k, v in patch.items():
        if k in COLUMN_KEYS and k in cols:
            if k == "review_reason" and v == "":
                v = None
            updates[k] = v

    if args.clear_review:
        if "needs_review" in cols:
            updates["needs_review"] = 0
        if "review_reason" in cols:
            updates["review_reason"] = None
        raw["needs_review"] = False
        raw["review_reason"] = None

    if updates:
        set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
        vals = list(updates.values()) + [args.expense_id]
        con.execute(f"UPDATE expenses SET {set_clause} WHERE expense_id=?", vals)

    # merge into raw_json
    raw.update(patch)
    con.execute(
        "UPDATE expenses SET raw_json=? WHERE expense_id=?",
        (json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True), args.expense_id),
    )

    con.commit()
    con.close()

    print(
        json.dumps(
            {
                "ok": True,
                "expense_id": args.expense_id,
                "updated_columns": sorted(list(updates.keys())),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
