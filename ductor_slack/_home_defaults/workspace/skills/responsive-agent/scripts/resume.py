#!/usr/bin/env python3
"""Resume a paused task by ref.

Loads ~/.ductor-slack/workspace/responsive_state/<ref>/context.md plus its
meta.json and writes both to stdout so the agent can pick up where the prior
session left off.

Usage:
  python3 resume.py abc123
  echo "...thread context..." | python3 resume.py --stdin
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STATE_ROOT = Path.home() / ".ductor-slack" / "workspace" / "responsive_state"
ANCHOR_RE = re.compile(r"\[ret-ref:([0-9a-f]{6})\]")


def find_ref_in_text(text: str) -> str | None:
    m = ANCHOR_RE.search(text)
    return m.group(1) if m else None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ref", nargs="?", help="6-hex-char reference id")
    p.add_argument("--stdin", action="store_true", help="Scan stdin for the first [ret-ref:XXXXXX] anchor")
    args = p.parse_args()

    ref = args.ref
    if not ref and args.stdin:
        ref = find_ref_in_text(sys.stdin.read())
    if not ref:
        sys.exit("error: provide a ref or pipe text containing [ret-ref:XXXXXX] with --stdin")

    ref = ref.strip().lower()
    if len(ref) != 6 or not all(c in "0123456789abcdef" for c in ref):
        sys.exit(f"error: invalid ref {ref!r} — must be 6 hex chars")

    state_dir = STATE_ROOT / ref
    ctx_path = state_dir / "context.md"
    meta_path = state_dir / "meta.json"

    if not ctx_path.exists():
        sys.exit(f"error: no saved context for ref {ref} at {state_dir}")

    sys.stdout.write(f"=== resume context for ref {ref} ===\n")
    sys.stdout.write(ctx_path.read_text(encoding="utf-8"))
    if meta_path.exists():
        sys.stdout.write("\n=== meta ===\n")
        sys.stdout.write(meta_path.read_text(encoding="utf-8"))
    sys.stdout.write(f"\n=== end ref {ref} ===\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
