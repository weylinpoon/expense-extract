#!/usr/bin/env python3
"""install.py

Simple installer for the expense-extract skill.

What it does:
- Copies this skill directory into ~/.openclaw/skills/expense-extract
  (or a custom destination)
- Ensures scripts are executable
- Optionally writes an example user override config

This is intentionally dependency-free (stdlib only).

Usage:
  ./scripts/install.py
  ./scripts/install.py --dest ~/.openclaw/skills/expense-extract
  ./scripts/install.py --write-example-config

Notes:
- Existing destination is not overwritten unless --force is provided.
- For OpenClaw: you generally just need the files under ~/.openclaw/skills/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
from pathlib import Path


def chmod_x(path: Path):
    try:
        st = path.stat()
        path.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dest",
        default=str(Path.home() / ".openclaw" / "skills" / "expense-extract"),
        help="Destination path (default: ~/.openclaw/skills/expense-extract)",
    )
    ap.add_argument("--force", action="store_true", help="Overwrite destination if it exists")
    ap.add_argument(
        "--write-example-config",
        action="store_true",
        help="Write ~/.openclaw/config/expense-extract.json if it doesn't exist",
    )
    args = ap.parse_args()

    skill_dir = Path(__file__).resolve().parent.parent
    dest = Path(os.path.expanduser(args.dest)).resolve()

    if dest.exists():
        if not args.force:
            raise SystemExit(
                f"Destination already exists: {dest}\n\nRe-run with --force to overwrite."
            )
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(skill_dir, dest)

    # Ensure key scripts are executable
    scripts_dir = dest / "scripts"
    if scripts_dir.exists():
        for p in scripts_dir.glob("*.py"):
            chmod_x(p)

    if args.write_example_config:
        cfg_dir = Path.home() / ".openclaw" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "expense-extract.json"
        if not cfg_path.exists():
            example = {
                "default_currency": "CAD",
                "query_default_limit": 10,
                "allowed_categories": [
                    "Uncategorized",
                    "Software",
                    "Telecom",
                    "Home Office",
                    "Travel",
                    "Professional Fees",
                    "Utilities",
                ],
            }
            cfg_path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "installed_to": str(dest),
                "scripts_executable": True,
                "wrote_example_config": bool(args.write_example_config),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
