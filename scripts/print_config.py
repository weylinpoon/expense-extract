#!/usr/bin/env python3
"""print_config.py

Print the resolved expense-extract configuration.

This is a convenience/debug tool to confirm which settings are in effect after
merging defaults + user override + env override.

Usage:
  ./print_config.py
  ./print_config.py --json
"""

import argparse
import json

from config import load_config_debug


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Output JSON")
    args = ap.parse_args()

    info = load_config_debug()

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    print("expense-extract config")
    print(f"- defaults: {info.get('defaults_path')}")
    print(f"- override: {info.get('override_path') or '(none)'}")
    print("\nResolved config:\n")
    # pretty print key/value pairs, stable order
    cfg = info.get("config") or {}
    for k in sorted(cfg.keys()):
        v = cfg[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        print(f"- {k}: {v}")


if __name__ == "__main__":
    main()
