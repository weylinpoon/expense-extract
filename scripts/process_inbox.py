#!/usr/bin/env python3
"""process_inbox.py

Inbox ingestion + deterministic extraction only.

Goal: get raw material into SQLite reliably (dedupe + attachment handling + text extraction),
then let the *agent/LLM* make higher-level judgments (vendor/category/description cleanup)
and update the saved expense records.

What this script does:
- Reads latest messages from an AgentMail inbox
- Skips already-processed messages (email_message_id or source_hash)
- Prefers PDF attachments; otherwise uses email body
- Extracts PDF text using `pdftotext` (poppler-utils)
- Extracts a minimal set of deterministic fields (invoice_number/date/total/currency when obvious)
- Saves an expense row via save_expense.py, embedding extracted text context in raw_json
- Inserts an email_sources row linked to the expense

What this script intentionally does NOT do:
- No vendor inference beyond trivial normalization; vendor is left null when uncertain
- No category inference (leave Uncategorized unless explicitly present)
- No semantic judgments (leave to agent/LLM)

Outputs JSON only.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import load_config

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SAVE_EXPENSE = SCRIPT_DIR / "save_expense.py"

# Currency codes are configurable via defaults.json + user override file.


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_defaults() -> dict:
    # Backwards-compatible wrapper: other code expects this name.
    # Under the hood we use the shared config loader (supports user override file).
    cfg = load_config()
    if cfg:
        return cfg
    # Fallbacks if config can't be read for any reason
    return {
        "default_currency": "CAD",
        "default_currency_source": "currency_symbol_and_context",
        "flag_review_on_default_currency": True,
        "default_entity": "RanchGlen Capital Inc.",
        "default_entity_source": "conversation_context",
        "default_entity_confidence": 0.8,
        "default_category": "Uncategorized",
        "allowed_categories": ["Uncategorized"],
        "pdf_text_min_chars": 50,
    }


def norm_text(text: str) -> str:
    return "\n".join([ln.strip() for ln in (text or "").splitlines() if ln.strip()])


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def curl_json(api_key: str, url: str) -> dict:
    out = subprocess.check_output(["curl", "-sS", "-H", f"Authorization: Bearer {api_key}", url])
    return json.loads(out)


def url_quote(s: str) -> str:
    import urllib.parse

    return urllib.parse.quote(s, safe="")


def pdftotext_text(path: Path) -> str:
    try:
        return subprocess.check_output(["pdftotext", str(path), "-"], stderr=subprocess.DEVNULL).decode(
            "utf-8", "ignore"
        )
    except Exception:
        return ""


from db import ensure_schema


def ensure_tables(con: sqlite3.Connection):
    """Backward-compat wrapper; prefer ensure_schema()."""
    ensure_schema(con)


def already_processed(con: sqlite3.Connection, email_message_id: str | None, source_hash: str) -> bool:
    if email_message_id:
        if con.execute(
            "SELECT 1 FROM email_sources WHERE email_message_id=? LIMIT 1", (email_message_id,)
        ).fetchone():
            return True
    if con.execute("SELECT 1 FROM email_sources WHERE source_hash=? LIMIT 1", (source_hash,)).fetchone():
        return True
    return False


def insert_email_source(con: sqlite3.Connection, row: dict):
    cols = [
        "source_id",
        "email_message_id",
        "thread_id",
        "email_from",
        "email_to",
        "email_cc",
        "subject",
        "received_at",
        "has_attachments",
        "selected_attachment_name",
        "triage_status",
        "triage_reason",
        "entity",
        "entity_source",
        "processing_status",
        "processed_at",
        "linked_expense_id",
        "source_hash",
    ]
    con.execute(
        """
INSERT INTO email_sources (
  source_id, email_message_id, thread_id, email_from, email_to, email_cc,
  subject, received_at, has_attachments, selected_attachment_name,
  triage_status, triage_reason, entity, entity_source,
  processing_status, processed_at, linked_expense_id, source_hash
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""",
        tuple(row.get(c) for c in cols),
    )


def update_expense_linkage(
    con: sqlite3.Connection,
    expense_id: str,
    source_id: str,
    email_message_id: str | None,
    extraction_source: str,
    entity_confidence: float,
):
    con.execute(
        "UPDATE expenses SET source_id=?, email_message_id=?, extraction_source=?, entity_confidence=? WHERE expense_id=?",
        (source_id, email_message_id, extraction_source, entity_confidence, expense_id),
    )

    row = con.execute("SELECT raw_json FROM expenses WHERE expense_id=?", (expense_id,)).fetchone()
    if row and row[0]:
        try:
            raw = json.loads(row[0])
        except Exception:
            raw = {}
        raw["source_id"] = source_id
        raw["email_message_id"] = email_message_id
        raw["extraction_source"] = extraction_source
        raw["entity_confidence"] = entity_confidence
        con.execute(
            "UPDATE expenses SET raw_json=? WHERE expense_id=?",
            (json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True), expense_id),
        )


def save_expense(db_path: str, payload: dict) -> dict:
    p = subprocess.run(
        [
            str(SAVE_EXPENSE),
            "--db",
            db_path,
            "--source-type",
            payload.get("source_type") or "email",
            "--source-ref",
            payload.get("source_ref") or "",
        ],
        input=json.dumps(payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore") + p.stdout.decode("utf-8", "ignore"))
    return json.loads(p.stdout.decode("utf-8"))


def parse_date_any(s: str) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s.strip())

    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r"\b(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(20\d{2})\b", s)
    if m:
        a, b, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Try MM/DD then DD/MM
        for mm, dd in [(a, b), (b, a)]:
            if 1 <= mm <= 12 and 1 <= dd <= 31:
                return f"{yy:04d}-{mm:02d}-{dd:02d}"

    m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),\s+(20\d{2})\b", s)
    if m:
        mon, dd, yy = m.group(1), int(m.group(2)), int(m.group(3))
        for fmt in ("%b", "%B"):
            try:
                mm = datetime.strptime(mon, fmt).month
                return f"{yy:04d}-{mm:02d}-{dd:02d}"
            except Exception:
                pass
    return None


def extract_min_fields(text: str) -> dict:
    """Deterministic regex extraction of minimal fields. Vendor/category left to agent."""
    t = text or ""

    inv = None
    for pat in [
        r"\bInvoice\s*(?:#|No\.?|Number)?\s*[:#]?\s*([A-Za-z0-9\-]+)",
        r"\bReference\s*(?:#|ID)?\s*[:#]?\s*([A-Za-z0-9\-]+)",
    ]:
        m = re.search(pat, t, re.I)
        if m:
            inv = m.group(1)
            break

    exp_date = None
    for pat in [
        r"\bBilled\s+On\b\s*([A-Za-z0-9, /\-]+)",
        r"\bBilling\s+date\b\s*([A-Za-z0-9, /\-]+)",
        r"\bDate\b\s*[:\-]?\s*([A-Za-z0-9, /\-]+)",
        r"\bPaid\s+on\b\s*([A-Za-z0-9, /\-]+)",
    ]:
        m = re.search(pat, t, re.I)
        if m:
            exp_date = parse_date_any(m.group(1))
            if exp_date:
                break

    # Total / amount
    total = None
    m = re.search(r"\b(Total|Amount Due|Amount)\b[^\$]*\$\s*([0-9]+\.[0-9]{2})", t, re.I)
    if m:
        total = m.group(2)
    if not total:
        # fallback: largest $ amount
        vals = [float(x) for x in re.findall(r"\$\s*([0-9]+\.[0-9]{2})", t)]
        if vals:
            total = f"{max(vals):.2f}"

    # currency
    currency = None
    currency_codes = defaults.get("allowed_currencies") or ["USD", "CAD"]
    mm = re.search(r"\b(" + "|".join([re.escape(c) for c in currency_codes]) + r")\b", t)
    if mm:
        currency = mm.group(1)

    return {"invoice_number": inv, "expense_date": exp_date, "total": total, "currency": currency}


def triage_is_expense(text: str, has_attachments: bool) -> tuple[bool, str]:
    t = (text or "").lower()
    if re.search(r"\$\s*[0-9]+\.[0-9]{2}", t):
        return True, "contains_amount"
    if re.search(r"\b(invoice|receipt|paid|amount due|payment)\b", t):
        # might still be portal-link-only, but record for review
        return True, "invoice_keywords"
    if has_attachments:
        return True, "has_attachments"
    return False, "no_expense_signals"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--default-entity", default=None)
    args = ap.parse_args()

    defaults = load_defaults()

    lst = curl_json(args.api_key, f"https://api.agentmail.to/v0/inboxes/{args.inbox}/messages")
    msgs = [m for m in (lst.get("messages") or []) if "received" in (m.get("labels") or [])]
    msgs = sorted(msgs, key=lambda m: m.get("timestamp") or "", reverse=True)[: args.limit]

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")
    ensure_tables(con)
    con.commit()

    processed = []
    skipped = []

    for m in msgs:
        msg_id = m.get("message_id")
        if not msg_id:
            continue

        full = curl_json(
            args.api_key,
            f"https://api.agentmail.to/v0/inboxes/{args.inbox}/messages/{url_quote(msg_id)}",
        )

        subject = full.get("subject")
        body_norm = norm_text(full.get("text") or "")
        atts = full.get("attachments") or []

        selected_attachment_name = None
        extraction_source = "text_body"

        # pick first PDF attachment
        pdfs = [
            a
            for a in atts
            if (a.get("content_type") == "application/pdf")
            or ((a.get("filename") or "").lower().endswith(".pdf"))
        ]

        source_hash = None
        chosen_text = body_norm

        if pdfs:
            att = pdfs[0]
            selected_attachment_name = att.get("filename")
            extraction_source = "pdf_attachment"

            meta = curl_json(
                args.api_key,
                f"https://api.agentmail.to/v0/inboxes/{args.inbox}/messages/{url_quote(msg_id)}/attachments/{att['attachment_id']}",
            )
            dl = meta.get("download_url")
            pdf_path = Path(f"/tmp/agentmail_{att['attachment_id']}.pdf")
            subprocess.check_call(["curl", "-sS", "-L", dl, "-o", str(pdf_path)])
            pdf_bytes = pdf_path.read_bytes()
            source_hash = sha256_bytes(pdf_bytes)

            pdf_text = pdftotext_text(pdf_path)
            min_chars = int(defaults.get("pdf_text_min_chars", 50))
            chosen_text = pdf_text if len(pdf_text.strip()) > min_chars else body_norm
        else:
            source_hash = sha256_text(body_norm)

        if already_processed(con, msg_id, source_hash):
            skipped.append({"message_id": msg_id, "subject": subject, "reason": "already_processed"})
            continue

        is_expense, triage_reason = triage_is_expense(chosen_text, bool(atts))

        entity = args.default_entity or defaults.get("default_entity")
        entity_source = defaults.get("default_entity_source", "conversation_context")
        entity_conf = float(defaults.get("default_entity_confidence", 0.8))

        if not is_expense:
            src_id = str(uuid.uuid4())
            insert_email_source(
                con,
                {
                    "source_id": src_id,
                    "email_message_id": msg_id,
                    "thread_id": full.get("thread_id"),
                    "email_from": full.get("from"),
                    "email_to": ",".join(full.get("to") or []),
                    "email_cc": ",".join(full.get("cc") or []) if full.get("cc") else None,
                    "subject": subject,
                    "received_at": full.get("timestamp"),
                    "has_attachments": 1 if bool(atts) else 0,
                    "selected_attachment_name": selected_attachment_name,
                    "triage_status": "not_expense",
                    "triage_reason": triage_reason,
                    "entity": entity,
                    "entity_source": entity_source,
                    "processing_status": "skipped",
                    "processed_at": utc_now_iso(),
                    "linked_expense_id": None,
                    "source_hash": source_hash,
                },
            )
            con.commit()
            processed.append({"message_id": msg_id, "source_id": src_id, "triage_status": "not_expense"})
            continue

        # Deterministic extraction may find multiple candidates; LLM must choose final fields.
        # We store candidates in raw_json, but DO NOT write them into the canonical columns yet.
        min_fields = extract_min_fields(chosen_text)

        # Save a draft expense row; LLM decides vendor/category/amount/date/currency.
        payload = {
            "source_type": "email",
            "source_ref": subject,
            "source_id": None,
            "email_message_id": msg_id,
            "extraction_source": extraction_source,
            "entity": entity,
            "group_name": None,
            "entity_source": entity_source,
            "entity_confidence": entity_conf,
            "vendor": None,
            "vendor_normalized": None,
            "invoice_number": None,
            "expense_date": None,
            "subtotal": None,
            "tax": None,
            "total": None,
            "currency": None,
            "currency_source": None,
            "description": f"Email expense: {subject}",
            "category": defaults.get("default_category", "Uncategorized"),
            "confidence": 0.7,
            "needs_review": True,
            "review_reason": defaults.get("draft_review_reason", "needs_llm_enrichment"),
            "detected_fields": min_fields,
        }

        # embed context for agent/LLM in raw_json
        payload["extracted_text_excerpt"] = chosen_text[:4000]
        payload["email_from"] = full.get("from")
        payload["email_subject"] = subject
        payload["selected_attachment_name"] = selected_attachment_name

        saved = save_expense(args.db, payload)

        src_id = str(uuid.uuid4())
        if saved.get("expense_id") is None:
            insert_email_source(
                con,
                {
                    "source_id": src_id,
                    "email_message_id": msg_id,
                    "thread_id": full.get("thread_id"),
                    "email_from": full.get("from"),
                    "email_to": ",".join(full.get("to") or []),
                    "email_cc": ",".join(full.get("cc") or []) if full.get("cc") else None,
                    "subject": subject,
                    "received_at": full.get("timestamp"),
                    "has_attachments": 1 if bool(atts) else 0,
                    "selected_attachment_name": selected_attachment_name,
                    "triage_status": "invoice_or_receipt",
                    "triage_reason": "duplicate_blocked",
                    "entity": entity,
                    "entity_source": entity_source,
                    "processing_status": "skipped",
                    "processed_at": utc_now_iso(),
                    "linked_expense_id": None,
                    "source_hash": source_hash,
                },
            )
            con.commit()
            skipped.append(
                {
                    "message_id": msg_id,
                    "subject": subject,
                    "reason": "duplicate_blocked",
                    "duplicate_of_expense_id": saved.get("duplicate_of_expense_id"),
                }
            )
            continue

        insert_email_source(
            con,
            {
                "source_id": src_id,
                "email_message_id": msg_id,
                "thread_id": full.get("thread_id"),
                "email_from": full.get("from"),
                "email_to": ",".join(full.get("to") or []),
                "email_cc": ",".join(full.get("cc") or []) if full.get("cc") else None,
                "subject": subject,
                "received_at": full.get("timestamp"),
                "has_attachments": 1 if bool(atts) else 0,
                "selected_attachment_name": selected_attachment_name,
                "triage_status": "invoice_or_receipt",
                "triage_reason": triage_reason,
                "entity": entity,
                "entity_source": entity_source,
                "processing_status": "processed",
                "processed_at": utc_now_iso(),
                "linked_expense_id": saved["expense_id"],
                "source_hash": source_hash,
            },
        )
        update_expense_linkage(con, saved["expense_id"], src_id, msg_id, extraction_source, entity_conf)
        con.commit()

        processed.append(
            {
                "message_id": msg_id,
                "subject": subject,
                "source_id": src_id,
                "expense_id": saved["expense_id"],
            }
        )

    con.close()
    print(json.dumps({"processed": processed, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
