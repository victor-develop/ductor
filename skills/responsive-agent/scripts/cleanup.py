#!/usr/bin/env python3
"""Remove saved state for a resolved ref, or prune old refs.

Usage:
  python3 cleanup.py abc123
  python3 cleanup.py --older-than 7d
  python3 cleanup.py --list
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import sys
from pathlib import Path

STATE_ROOT = Path.home() / ".ductor-slack" / "workspace" / "responsive_state"


def parse_duration(s: str) -> datetime.timedelta:
    m = re.fullmatch(r"(\d+)([smhd])", s.strip())
    if not m:
        sys.exit(f"error: invalid duration {s!r} (use forms like 30m, 6h, 7d)")
    n = int(m.group(1))
    unit = m.group(2)
    return datetime.timedelta(
        seconds={"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
    )


def load_created_at(state_dir: Path) -> datetime.datetime | None:
    meta_path = state_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("created_at"):
                return datetime.datetime.fromisoformat(meta["created_at"])
        except (ValueError, OSError):
            pass
    return datetime.datetime.fromtimestamp(
        state_dir.stat().st_mtime, tz=datetime.timezone.utc
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ref", nargs="?", help="Ref to remove")
    p.add_argument("--older-than", help="Remove all refs older than this (e.g. 7d, 12h)")
    p.add_argument("--list", action="store_true", help="List active refs and exit")
    args = p.parse_args()

    if not STATE_ROOT.exists():
        print(json.dumps({"ok": True, "removed": [], "note": "state root not present"}))
        return 0

    if args.list:
        entries = []
        for d in sorted(STATE_ROOT.iterdir()):
            if not d.is_dir():
                continue
            created = load_created_at(d)
            entries.append({"ref": d.name, "created_at": created.isoformat() if created else None})
        print(json.dumps({"ok": True, "refs": entries}, ensure_ascii=False))
        return 0

    removed: list[str] = []

    if args.ref:
        target = STATE_ROOT / args.ref.strip().lower()
        if not target.exists():
            sys.exit(f"error: ref {args.ref} not found at {target}")
        shutil.rmtree(target)
        removed.append(target.name)

    if args.older_than:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - parse_duration(args.older_than)
        for d in STATE_ROOT.iterdir():
            if not d.is_dir():
                continue
            created = load_created_at(d)
            if created and created < cutoff:
                shutil.rmtree(d)
                removed.append(d.name)

    if not args.ref and not args.older_than:
        sys.exit("error: provide a ref, --older-than, or --list")

    print(json.dumps({"ok": True, "removed": removed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
