#!/usr/bin/env python3
"""
query_expenses.py

Query the local SQLite `expenses` table.

Default output is a user-friendly text list (for chat).
Use `--json` to output JSON.

Default behavior:
- return most recent 10 expenses (by created_at desc)

Supports:
- show recent expenses
- show expenses needing review
- show expense <expense_id>
- expense summary this month

Usage examples:
  ./query_expenses.py --db ./expenses.sqlite
  ./query_expenses.py --db ./expenses.sqlite --needing-review
  ./query_expenses.py --db ./expenses.sqlite --expense-id EXP-1234
  ./query_expenses.py --db ./expenses.sqlite --summary-this-month
  ./query_expenses.py --db ./expenses.sqlite --telegram "show expenses needing review"
"""

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone

from config import load_config
from db import ensure_schema

BASE_FIELDS = [
    "expense_id",
    "entity",
    "group_name",
    "vendor_normalized",
    "expense_date",
    "total",
    "currency",
    "category",
    "description",
    "needs_review",
]


def utc_now():
    return datetime.now(timezone.utc)


def ym(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def ensure_expenses_table_exists(con: sqlite3.Connection) -> bool:
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expenses' LIMIT 1")
    return cur.fetchone() is not None


def get_existing_columns(con: sqlite3.Connection) -> set[str]:
    cur = con.execute("PRAGMA table_info(expenses)")
    return {r[1] for r in cur.fetchall()}


def fields_for_db(con: sqlite3.Connection) -> list[str]:
    cols = get_existing_columns(con)
    # Always fetch raw_json to allow deriving fields like description even if not a column.
    fields = [f for f in BASE_FIELDS if f in cols]
    if "raw_json" in cols and "raw_json" not in fields:
        fields.append("raw_json")
    return fields


def rows_to_dicts(fields: list[str], rows):
    out = []
    has_raw = "raw_json" in fields
    for r in rows:
        d = dict(zip(fields, r))
        raw = None
        if has_raw:
            raw_text = d.get("raw_json")
            try:
                raw = json.loads(raw_text) if raw_text else None
            except Exception:
                raw = None

        # Derive description if not present as a column
        if d.get("description") is None and raw and isinstance(raw, dict):
            if raw.get("description"):
                d["description"] = raw.get("description")

        if "needs_review" in d:
            d["needs_review"] = bool(d.get("needs_review"))

        # Drop raw_json from output by default (can still be requested via --json)
        d.pop("raw_json", None)

        out.append(d)
    return out


def short_id(s: str | None, n: int = 8) -> str | None:
    if s is None:
        return None
    s = str(s)
    return s if len(s) <= n else s[:n]


def fmt_money(total, currency):
    if total is None:
        return "—"
    if currency:
        return f"{total} {currency}"
    return str(total)


def format_list(records: list[dict], *, desc_max_len: int = 80) -> str:
    if not records:
        return "(no records)"

    lines = []
    for i, r in enumerate(records, 1):
        eid = r.get("expense_id")
        vendor = r.get("vendor_normalized") or r.get("vendor") or "(unknown vendor)"
        date = r.get("expense_date") or "(no date)"
        total = fmt_money(r.get("total"), r.get("currency"))
        ent = r.get("entity")
        cat = r.get("category")
        desc = r.get("description")
        nr = r.get("needs_review")

        parts = [f"{i}. {vendor}", f"{date}", f"{total}"]
        if desc:
            # keep it short
            d = str(desc).strip().replace("\n", " ")
            if len(d) > desc_max_len:
                d = d[: max(0, desc_max_len - 3)] + "..."
            parts.append(f"desc: {d}")
        if ent:
            parts.append(f"entity: {ent}")
        if cat:
            parts.append(f"cat: {cat}")
        if nr:
            parts.append("REVIEW")
        if eid:
            parts.append(f"id: {short_id(eid)}")

        lines.append(" • ".join(parts))

    return "\n".join(lines)


def query_recent(con: sqlite3.Connection, limit: int):
    fields = fields_for_db(con)
    cur = con.execute(
        f"""
        SELECT {", ".join(fields)}
        FROM expenses
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return rows_to_dicts(fields, cur.fetchall())


def query_needing_review(con: sqlite3.Connection, limit: int):
    fields = fields_for_db(con)
    cur = con.execute(
        f"""
        SELECT {", ".join(fields)}
        FROM expenses
        WHERE needs_review = 1
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return rows_to_dicts(fields, cur.fetchall())


def query_by_expense_id(con: sqlite3.Connection, expense_id: str):
    fields = fields_for_db(con)
    cur = con.execute(
        f"""
        SELECT {", ".join(fields)}
        FROM expenses
        WHERE expense_id = ?
        LIMIT 1
        """,
        (expense_id,),
    )
    row = cur.fetchone()
    return None if row is None else rows_to_dicts(fields, [row])[0]


def summary_this_month(con: sqlite3.Connection, year_month: str):
    # Summary by currency + category; totals stored as TEXT, cast best-effort to REAL.
    cur = con.execute(
        """
        SELECT
          currency,
          category,
          COUNT(*) AS count,
          SUM(CAST(total AS REAL)) AS total_sum,
          SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS needs_review_count
        FROM expenses
        WHERE substr(created_at, 1, 7) = ?
        GROUP BY currency, category
        ORDER BY currency, category
        """,
        (year_month,),
    )
    rows = cur.fetchall()

    cur2 = con.execute(
        """
        SELECT
          COUNT(*) AS count,
          SUM(CASE WHEN needs_review = 1 THEN 1 ELSE 0 END) AS needs_review_count
        FROM expenses
        WHERE substr(created_at, 1, 7) = ?
        """,
        (year_month,),
    )
    overall = cur2.fetchone()

    return {
        "year_month": year_month,
        "overall": {
            "count": int(overall[0] or 0),
            "needs_review_count": int(overall[1] or 0),
        },
        "by_currency_category": [
            {
                "currency": r[0],
                "category": r[1],
                "count": int(r[2] or 0),
                "total_sum": r[3],
                "needs_review_count": int(r[4] or 0),
            }
            for r in rows
        ],
    }


def parse_telegram_query(q: str):
    s = (q or "").strip().lower()

    if not s:
        return {"action": "recent"}

    if "needing review" in s or "needs review" in s or "need review" in s:
        return {"action": "needing_review"}

    if "summary" in s and "this month" in s:
        return {"action": "summary_this_month"}

    m = re.search(r"\bexpense\s+([A-Za-z0-9\-:_]+)\b", s)
    if m:
        return {"action": "by_id", "expense_id": m.group(1)}

    if "recent" in s:
        return {"action": "recent"}

    return {"action": "recent"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to SQLite database")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="Output machine-readable JSON instead of a formatted list")
    ap.add_argument("--recent", action="store_true")
    ap.add_argument("--needing-review", action="store_true")
    ap.add_argument("--expense-id", default=None)
    ap.add_argument("--summary-this-month", action="store_true")
    ap.add_argument("--month", default=None, help="YYYY-MM (for summaries); default current UTC month")
    ap.add_argument("--telegram", default=None, help="Natural language Telegram query to parse")
    args = ap.parse_args()

    defaults = load_config()
    limit = int(args.limit) if args.limit is not None else int(defaults.get("query_default_limit", 10))
    desc_max_len = int(defaults.get("query_desc_max_len", 80))

    con = sqlite3.connect(args.db)
    con.row_factory = None
    ensure_schema(con)

    if args.telegram is not None:
        intent = parse_telegram_query(args.telegram)
        action = intent["action"]
        if action == "needing_review":
            out = query_needing_review(con, limit)
            print(json.dumps(out, ensure_ascii=False) if args.json else format_list(out, desc_max_len=desc_max_len))
            return
        if action == "by_id":
            out = query_by_expense_id(con, intent["expense_id"])
            print(json.dumps(out, ensure_ascii=False) if args.json else format_list([] if out is None else [out], desc_max_len=desc_max_len))
            return
        if action == "summary_this_month":
            month = args.month or ym(utc_now())
            out = summary_this_month(con, month)
            print(json.dumps(out, ensure_ascii=False) if args.json else json.dumps(out, ensure_ascii=False))
            return
        out = query_recent(con, limit)
        print(json.dumps(out, ensure_ascii=False) if args.json else format_list(out, desc_max_len=desc_max_len))
        return

    if args.expense_id:
        out = query_by_expense_id(con, args.expense_id)
        print(json.dumps(out, ensure_ascii=False) if args.json else format_list([] if out is None else [out], desc_max_len=desc_max_len))
        return

    if args.summary_this_month:
        month = args.month or ym(utc_now())
        out = summary_this_month(con, month)
        # summary is naturally JSON-ish; keep as JSON for now
        print(json.dumps(out, ensure_ascii=False))
        return

    if args.needing_review:
        out = query_needing_review(con, limit)
        print(json.dumps(out, ensure_ascii=False) if args.json else format_list(out, desc_max_len=desc_max_len))
        return

    out = query_recent(con, limit)
    print(json.dumps(out, ensure_ascii=False) if args.json else format_list(out, desc_max_len=desc_max_len))


if __name__ == "__main__":
    main()
