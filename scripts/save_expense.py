#!/usr/bin/env python3
"""
save_expense.py

Reads an extracted expense JSON object from stdin, creates/uses a local SQLite DB,
checks for potential duplicates, and inserts a new row (never overwriting).
Outputs JSON only (the saved record, including expense_id).

Usage:
  cat extracted.json | ./save_expense.py --db ./expenses.sqlite --source-type pdf --source-ref Tikr_2025_invoice.pdf
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

from config import load_config
from db import ensure_schema

# Allowed currencies + currency_sources are configurable (see defaults.json + user override).
# allowed_entity_sources is configurable (see defaults.json + user override).


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_reason(existing: str | None, reason: str) -> str:
    if not existing:
        return reason
    parts = [p.strip() for p in existing.split(";") if p.strip()]
    if reason in parts:
        return existing
    parts.append(reason)
    return "; ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to SQLite database file")
    ap.add_argument("--source-type", default=None, help="e.g. pdf, image, email")
    ap.add_argument("--source-ref", default=None, help="e.g. filename, message id, url")
    args = ap.parse_args()

    try:
        payload = json.load(sys.stdin)
    except Exception as e:
        print(json.dumps({"error": f"invalid_json_input: {str(e)}"}))
        sys.exit(2)

    # Ensure required keys exist (be permissive: fill missing with None)
    fields = [
        "entity",
        "entity_source",
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
        "confidence",
        "needs_review",
        "review_reason",
        # context fields (kept in raw_json, not separate columns)
        "extracted_text_excerpt",
        "email_from",
        "email_subject",
        "selected_attachment_name",
    ]
    for k in fields:
        payload.setdefault(k, None)
    payload.setdefault("needs_review", False)

    defaults = load_config()

    # Basic allowed-value sanity checks (do not "correct" content; just flag review)
    allowed_currency_sources = set(defaults.get("allowed_currency_sources") or [])
    if payload["currency_source"] is not None and allowed_currency_sources and payload["currency_source"] not in allowed_currency_sources:
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "invalid_currency_source")

    allowed_currencies = set(defaults.get("allowed_currencies") or [])
    if payload.get("currency") is not None and allowed_currencies and payload.get("currency") not in allowed_currencies:
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "invalid_currency")

    # Default currency if missing (configurable via defaults.json)
    # If this record is explicitly marked for LLM enrichment, do NOT default currency.
    rr = payload.get("review_reason") or ""
    needs_llm = isinstance(rr, str) and ("needs_llm_enrichment" in rr)
    if payload.get("currency") is None and not needs_llm:
        payload["currency"] = defaults.get("default_currency", "CAD")
        payload["currency_source"] = payload.get("currency_source") or defaults.get(
            "default_currency_source", "currency_symbol_and_context"
        )
        if bool(defaults.get("flag_review_on_default_currency", True)):
            payload["needs_review"] = True
            payload["review_reason"] = add_reason(payload.get("review_reason"), "currency_defaulted")

    # Vendor sanity check: prevent link-like vendor strings
    v = payload.get("vendor")
    vnorm = payload.get("vendor_normalized")
    if isinstance(v, str) and ("http://" in v.lower() or "https://" in v.lower() or "<http" in v.lower()):
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "invalid_vendor_url_like")
    if isinstance(vnorm, str) and ("http://" in vnorm.lower() or "https://" in vnorm.lower() or "<http" in vnorm.lower()):
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "invalid_vendor_normalized_url_like")

    allowed_entity_sources = set(defaults.get("allowed_entity_sources") or [])
    if payload["entity_source"] is not None and allowed_entity_sources and payload["entity_source"] not in allowed_entity_sources:
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "invalid_entity_source")

    allowed_categories = set(defaults.get("allowed_categories") or [])
    if payload["category"] is not None and allowed_categories and payload["category"] not in allowed_categories:
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "invalid_category")

    # If confidence < 0.8, enforce needs_review
    try:
        conf = payload["confidence"]
        if conf is not None and float(conf) < 0.8:
            payload["needs_review"] = True
            payload["review_reason"] = add_reason(payload.get("review_reason"), "low_confidence")
    except Exception:
        payload["needs_review"] = True
        payload["review_reason"] = add_reason(payload.get("review_reason"), "confidence_parse_error")

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")
    ensure_schema(con)

    # Duplicate check: same invoice_number + vendor_normalized + total + expense_date
    # If a duplicate is found, do NOT save a new record.
    inv = payload.get("invoice_number")
    vnorm = payload.get("vendor_normalized")
    total = payload.get("total")
    exp_date = payload.get("expense_date")

    if inv is not None and vnorm is not None and total is not None and exp_date is not None:
        cur = con.execute(
            """
            SELECT expense_id
            FROM expenses
            WHERE invoice_number = ?
              AND vendor_normalized = ?
              AND total = ?
              AND expense_date = ?
            LIMIT 1
            """,
            (inv, vnorm, total, exp_date),
        )
        row = cur.fetchone()
        if row:
            # Return a review-worthy response and exit without inserting.
            record = {
                "expense_id": None,
                "source_type": args.source_type,
                "source_ref": args.source_ref,
                "entity": payload.get("entity"),
                "group_name": payload.get("group_name"),
                "entity_source": payload.get("entity_source"),
                "vendor": payload.get("vendor"),
                "vendor_normalized": vnorm,
                "invoice_number": inv,
                "expense_date": exp_date,
                "subtotal": payload.get("subtotal"),
                "tax": payload.get("tax"),
                "total": total,
                "currency": payload.get("currency"),
                "currency_source": payload.get("currency_source"),
                "description": payload.get("description"),
                "category": payload.get("category"),
                "needs_review": True,
                "review_reason": add_reason(payload.get("review_reason"), "duplicate_invoice_vendor_total_date"),
                "confidence": payload.get("confidence"),
                "created_at": None,
                "duplicate_of_expense_id": row[0],
            }
            print(json.dumps(record, ensure_ascii=False))
            return

    expense_id = str(uuid.uuid4())
    created_at = utc_now_iso()

    record = {
        "expense_id": expense_id,
        "source_type": args.source_type,
        "source_ref": args.source_ref,
        "entity": payload.get("entity"),
        "group_name": payload.get("group_name"),
        "entity_source": payload.get("entity_source"),
        "vendor": payload.get("vendor"),
        "vendor_normalized": payload.get("vendor_normalized"),
        "invoice_number": payload.get("invoice_number"),
        "expense_date": payload.get("expense_date"),
        "subtotal": payload.get("subtotal"),
        "tax": payload.get("tax"),
        "total": payload.get("total"),
        "currency": payload.get("currency"),
        "currency_source": payload.get("currency_source"),
        "description": payload.get("description"),
        "category": payload.get("category"),
        "needs_review": bool(payload.get("needs_review")),
        "review_reason": payload.get("review_reason"),
        "confidence": payload.get("confidence"),
        "created_at": created_at,
        # context for agent/LLM enrichment
        "extracted_text_excerpt": payload.get("extracted_text_excerpt"),
        "email_from": payload.get("email_from"),
        "email_subject": payload.get("email_subject"),
        "selected_attachment_name": payload.get("selected_attachment_name"),
    }

    raw_json = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    try:
        con.execute(
            """
            INSERT INTO expenses (
              expense_id, source_type, source_ref,
              entity, entity_source, group_name,
              vendor, vendor_normalized, invoice_number, expense_date,
              subtotal, tax, total,
              currency, currency_source,
              category, needs_review, review_reason, confidence,
              raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["expense_id"],
                record["source_type"],
                record["source_ref"],
                record["entity"],
                record["entity_source"],
                record["group_name"],
                record["vendor"],
                record["vendor_normalized"],
                record["invoice_number"],
                record["expense_date"],
                record["subtotal"],
                record["tax"],
                record["total"],
                record["currency"],
                record["currency_source"],
                record["category"],
                1 if record["needs_review"] else 0,
                record["review_reason"],
                record["confidence"],
                raw_json,
                record["created_at"],
            ),
        )
        con.commit()
    except sqlite3.IntegrityError:
        record["expense_id"] = None
        record["needs_review"] = True
        record["review_reason"] = add_reason(record.get("review_reason"), "expense_id_collision_not_saved")
        print(json.dumps(record, ensure_ascii=False))
        sys.exit(1)
    finally:
        con.close()

    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()
