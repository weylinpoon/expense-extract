#!/usr/bin/env python3
"""doctor.py

Environment/installation self-diagnostics for expense-extract.

Checks:
- python3 available
- optional external tools: curl, pdftotext (poppler-utils)
- skill files present
- can create/read SQLite DB
- prints resolved config (optional)

Outputs JSON only.

Usage:
  ./scripts/doctor.py
  ./scripts/doctor.py --db ./expenses.sqlite
  ./scripts/doctor.py --print-config
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from config import load_config
from db import ensure_schema


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Optional DB path to validate (default: temp db)")
    ap.add_argument("--print-config", action="store_true")
    args = ap.parse_args()

    checks = []

    def add(name: str, ok: bool, details: dict | None = None):
        checks.append({"name": name, "ok": bool(ok), **(details or {})})

    add("python", True, {"executable": sys.executable, "version": sys.version.split()[0]})

    for cmd in ["curl", "pdftotext"]:
        path = which(cmd)
        add(f"tool:{cmd}", bool(path), {"path": path})

    skill_dir = Path(__file__).resolve().parent.parent
    add("skill_dir", skill_dir.exists(), {"path": str(skill_dir)})
    add("schema_sql", (skill_dir / "references" / "schema.sql").exists(), {})

    # SQLite check
    db_path = None
    tmp = None
    try:
        if args.db:
            db_path = Path(args.db)
        else:
            tmp = tempfile.NamedTemporaryFile(prefix="bookkeeping-", suffix=".sqlite", delete=True)
            db_path = Path(tmp.name)

        con = sqlite3.connect(str(db_path))
        con.execute("PRAGMA journal_mode=WAL;")
        ensure_schema(con)
        con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='expenses'")
        add("sqlite_schema", True, {"db": str(db_path)})
        con.close()
    except Exception as e:
        add("sqlite_schema", False, {"error": str(e), "db": str(db_path) if db_path else None})

    out = {"ok": all(c["ok"] for c in checks), "checks": checks}
    if args.print_config:
        try:
            out["config"] = load_config()
        except Exception as e:
            out["config_error"] = str(e)

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
