#!/usr/bin/env python3
"""config.py

Central config loader for the expense-extract skill.

Goal: make the skill configurable for anyone who installs it, without needing to
edit the skill package itself.

Precedence (later wins):
1) Skill defaults.json (bundled with the skill)
2) User override file (recommended): ~/.openclaw/config/expense-extract.json
3) Optional env override: EXPENSE_EXTRACT_CONFIG=/path/to/file.json

Back-compat:
- Also supports ~/.openclaw/config/bookkeeping-expense-extractor.json
- Also supports BOOKKEEPING_EXPENSE_EXTRACTOR_CONFIG

Merge behavior:
- Shallow dict merge (override replaces keys).
- Lists are replaced whole (not concatenated).

Keep this module small: it is imported by deterministic scripts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict


SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = SKILL_ROOT / "defaults.json"
DEFAULT_OVERRIDE_PATH = Path.home() / ".openclaw" / "config" / "expense-extract.json"
BACKCOMPAT_OVERRIDE_PATH = (
    Path.home() / ".openclaw" / "config" / "bookkeeping-expense-extractor.json"
)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _resolve_override_path() -> Path | None:
    """Return the override path that should be used (if any).

    Precedence:
    - EXPENSE_EXTRACT_CONFIG (env)
    - BOOKKEEPING_EXPENSE_EXTRACTOR_CONFIG (env, back-compat)
    - ~/.openclaw/config/expense-extract.json (if exists)
    - ~/.openclaw/config/bookkeeping-expense-extractor.json (if exists, back-compat)
    """

    override_path = os.environ.get("EXPENSE_EXTRACT_CONFIG")
    if override_path:
        return Path(override_path).expanduser()

    override_path = os.environ.get("BOOKKEEPING_EXPENSE_EXTRACTOR_CONFIG")
    if override_path:
        return Path(override_path).expanduser()

    if DEFAULT_OVERRIDE_PATH.exists():
        return DEFAULT_OVERRIDE_PATH

    if BACKCOMPAT_OVERRIDE_PATH.exists():
        return BACKCOMPAT_OVERRIDE_PATH

    return None


def load_config() -> Dict[str, Any]:
    base = _read_json(DEFAULTS_PATH)

    override_path = _resolve_override_path()
    override = _read_json(override_path) if override_path else {}

    # shallow merge
    cfg = dict(base)
    cfg.update(override)
    return cfg


def load_config_debug() -> Dict[str, Any]:
    """Load config and include provenance info for debugging."""

    override_path = _resolve_override_path()
    return {
        "defaults_path": str(DEFAULTS_PATH),
        "override_path": str(override_path) if override_path else None,
        "config": load_config(),
    }
