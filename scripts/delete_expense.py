#!/usr/bin/env python3
"""delete_expense.py

Delete an expense and any associated email_sources rows.

Association logic:
- If expenses.source_id is set, delete email_sources where source_id matches.
- Else if expenses.email_message_id is set, delete email_sources where email_message_id matches.

Always creates no backups; caller should backup DB if needed.
JSON-only output.

Usage:
  ./delete_expense.py --db expenses.sqlite --expense-id <id>
"""

import argparse
import json
import sqlite3

from db import ensure_schema


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--expense-id", required=True)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute("PRAGMA journal_mode=WAL;")
    ensure_schema(con)

    row = con.execute(
        "SELECT source_id, email_message_id FROM expenses WHERE expense_id=? LIMIT 1",
        (args.expense_id,),
    ).fetchone()
    if not row:
        print(json.dumps({"ok": False, "error": "expense_not_found", "expense_id": args.expense_id}))
        return

    source_id, email_message_id = row

    deleted_email_sources = 0
    if source_id:
        cur = con.execute("DELETE FROM email_sources WHERE source_id=?", (source_id,))
        deleted_email_sources += cur.rowcount
    elif email_message_id:
        cur = con.execute("DELETE FROM email_sources WHERE email_message_id=?", (email_message_id,))
        deleted_email_sources += cur.rowcount

    cur2 = con.execute("DELETE FROM expenses WHERE expense_id=?", (args.expense_id,))
    deleted_expenses = cur2.rowcount

    con.commit()
    con.close()

    print(
        json.dumps(
            {
                "ok": True,
                "expense_id": args.expense_id,
                "deleted_expenses": deleted_expenses,
                "deleted_email_sources": deleted_email_sources,
                "matched_source_id": source_id,
                "matched_email_message_id": email_message_id,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
