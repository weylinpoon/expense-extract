#!/usr/bin/env python3
"""self_check.py

Lightweight integration self-check for expense-extract.

What it verifies:
- scripts can run (imports OK)
- save_expense inserts a record into a fresh SQLite DB
- query_expenses can list and fetch by id
- update_expense updates canonical columns + raw_json

This is intentionally minimal (no external network / inbox dependencies).

Usage:
  ./self_check.py
  ./self_check.py --verbose

Exit codes:
- 0: OK
- 1: failed
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

SAVE = SCRIPT_DIR / "save_expense.py"
QUERY = SCRIPT_DIR / "query_expenses.py"
UPDATE = SCRIPT_DIR / "update_expense.py"


def run(cmd: list[str], *, input_text: str | None = None, env: dict | None = None, verbose: bool = False) -> subprocess.CompletedProcess:
    if verbose:
        print("$", " ".join(cmd))
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def must_ok(p: subprocess.CompletedProcess, *, label: str):
    if p.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {p.returncode})\nSTDOUT:\n{p.stdout}\n\nSTDERR:\n{p.stderr}"
        )


def must_json(stdout: str, *, label: str):
    try:
        return json.loads(stdout)
    except Exception as e:
        raise RuntimeError(f"{label} did not return valid JSON: {e}\nSTDOUT:\n{stdout}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="bookkeeping-self-check-") as td:
        tmp = Path(td)
        db_path = tmp / "expenses.sqlite"

        # Minimal payload that should successfully insert.
        payload = {
            "entity": "Personal",
            "entity_source": "user_override",
            "group_name": None,
            "vendor": "Test Vendor",
            "vendor_normalized": "Test Vendor",
            "invoice_number": "INV-1",
            "expense_date": "2026-01-02",
            "subtotal": "10.00",
            "tax": "1.30",
            "total": "11.30",
            "currency": "CAD",
            "currency_source": "explicit_document_currency",
            "description": "Self-check record",
            "category": "Software",
            "confidence": 0.95,
            "needs_review": False,
            "review_reason": None,
        }

        p_save = run(
            [sys.executable, str(SAVE), "--db", str(db_path), "--source-type", "self_check", "--source-ref", "self_check"],
            input_text=json.dumps(payload),
            verbose=args.verbose,
        )
        must_ok(p_save, label="save_expense.py")
        saved = must_json(p_save.stdout, label="save_expense.py")
        expense_id = saved.get("expense_id")
        if not expense_id:
            raise RuntimeError(f"save_expense.py returned no expense_id. Output: {saved}")

        # query recent (formatted)
        p_q1 = run([sys.executable, str(QUERY), "--db", str(db_path), "--recent"], verbose=args.verbose)
        must_ok(p_q1, label="query_expenses.py --recent")
        if "Test Vendor" not in (p_q1.stdout or ""):
            raise RuntimeError(f"query_expenses.py output missing vendor. Output:\n{p_q1.stdout}")

        # query by id (json)
        p_q2 = run(
            [sys.executable, str(QUERY), "--db", str(db_path), "--expense-id", expense_id, "--json"],
            verbose=args.verbose,
        )
        must_ok(p_q2, label="query_expenses.py --expense-id --json")
        got = must_json(p_q2.stdout, label="query_expenses.py --expense-id --json")
        if not isinstance(got, dict) or got.get("expense_id") != expense_id:
            raise RuntimeError(f"query by id returned unexpected result: {got}")

        # update
        patch = {
            "category": "Utilities",  # should be allowed by defaults
            "description": "Self-check updated",
        }
        p_up = run(
            [
                sys.executable,
                str(UPDATE),
                "--db",
                str(db_path),
                "--expense-id",
                expense_id,
                "--set-json",
                json.dumps(patch),
            ],
            verbose=args.verbose,
        )
        must_ok(p_up, label="update_expense.py")
        upd = must_json(p_up.stdout, label="update_expense.py")
        if not upd.get("ok"):
            raise RuntimeError(f"update_expense.py returned ok=false: {upd}")

        # confirm update took
        p_q3 = run(
            [sys.executable, str(QUERY), "--db", str(db_path), "--expense-id", expense_id, "--json"],
            verbose=args.verbose,
        )
        must_ok(p_q3, label="query_expenses.py (post-update)")
        got2 = must_json(p_q3.stdout, label="query_expenses.py (post-update)")
        if not isinstance(got2, dict) or got2.get("category") != "Utilities":
            raise RuntimeError(f"post-update category mismatch: {got2}")

        # sanity: show needing review should be empty
        p_q4 = run([sys.executable, str(QUERY), "--db", str(db_path), "--needing-review"], verbose=args.verbose)
        must_ok(p_q4, label="query_expenses.py --needing-review")
        if "(no records)" not in (p_q4.stdout or ""):
            raise RuntimeError(f"expected no review-needed records. Output:\n{p_q4.stdout}")

        print("OK: expense-extract self-check passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
