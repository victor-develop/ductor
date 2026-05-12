#!/usr/bin/env python3
"""Pause the current task, save its context, and post a Slack ask message.

Generates a short reference id, persists the task context under
~/.ductor-slack/workspace/responsive_state/<ref>/, then posts a NEW Slack
message (not a thread reply) to the target channel mentioning the chosen
human. The message ends with `[ret-ref:<ref>]` on its own line so that the
next agent session, triggered by the human's reply, can resume the task.

Usage:
  python3 pause_and_ask.py \\
    --channel C0XXXXXX \\
    --mention U0XXXXXX \\
    --question "How should I price the X plan?" \\
    --context-stdin

  python3 pause_and_ask.py \\
    --channel C0XXXXXX \\
    --mention U0XXXXXX \\
    --question "..." \\
    --context-file /path/to/ctx.md \\
    --requester-channel C111 \\
    --requester-thread-ts 1700000000.000001
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import secrets
import subprocess
import sys
import urllib.request
from pathlib import Path

WORKSPACE = Path.home() / ".ductor-slack" / "workspace"
STATE_ROOT = WORKSPACE / "responsive_state"
SLACK_SEND = WORKSPACE / "tools" / "user_tools" / "slack_send.py"
CONFIG_PATH = Path.home() / ".ductor-slack" / "config" / "config.json"
IDENTITY_CACHE = STATE_ROOT / ".bot_identity.json"


def load_bot_token() -> str | None:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return ((cfg.get("slack") or {}).get("bot_token")) or None


def get_self_user_id() -> str | None:
    """Return this bot's own Slack user id (U...). Cached on disk."""
    try:
        cached = json.loads(IDENTITY_CACHE.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("user_id"):
            return cached["user_id"]
    except Exception:
        pass

    token = load_bot_token()
    if not token:
        return None
    try:
        req = urllib.request.Request(
            "https://slack.com/api/auth.test",
            headers={"Authorization": f"Bearer {token}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not body.get("ok") or not body.get("user_id"):
        return None
    try:
        IDENTITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
        IDENTITY_CACHE.write_text(
            json.dumps({"user_id": body["user_id"], "user": body.get("user")}) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return body["user_id"]


def new_ref() -> str:
    for _ in range(20):
        ref = secrets.token_hex(3)  # 6 hex chars
        if not (STATE_ROOT / ref).exists():
            return ref
    sys.exit("error: could not allocate a free ref after 20 tries")


def format_mention(s: str) -> str:
    s = s.strip()
    if not s:
        sys.exit("error: empty --mention")
    if s.startswith("<@") and s.endswith(">"):
        return s
    if s[0] in ("U", "W") and s[1:].isalnum():
        return f"<@{s}>"
    return s


def read_context(args: argparse.Namespace) -> str:
    if args.context_file:
        return Path(args.context_file).read_text(encoding="utf-8")
    if args.context_stdin:
        return sys.stdin.read()
    sys.exit("error: provide --context-file or --context-stdin")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--channel", required=True, help="Target channel id (e.g. C0XXX) to post the ask")
    p.add_argument("--mention", required=True, help="Slack user id to mention (U... or W...) — handles like @name will not notify")
    p.add_argument("--question", required=True, help="Short question to the human (one or two lines)")
    p.add_argument("--context-file", help="Path to a markdown file containing the resume context")
    p.add_argument("--context-stdin", action="store_true", help="Read resume context from stdin")
    p.add_argument("--requester-channel", help="Original requester channel id (for reporting back)")
    p.add_argument("--requester-thread-ts", help="Original requester thread ts (for reporting back)")
    p.add_argument("--ref", help="Override the generated ref (advanced, must be 6 hex chars)")
    p.add_argument(
        "--reply-hint",
        help=(
            "Override the auto-appended reply hint. Use the placeholder {bot} which "
            "gets replaced by <@BOT_USER_ID>. Pass an empty string to disable the hint."
        ),
        default=None,
    )
    args = p.parse_args()

    context = read_context(args).strip()
    if not context:
        sys.exit("error: empty context — the resume agent will need at least the task description")

    STATE_ROOT.mkdir(parents=True, exist_ok=True)

    if args.ref:
        ref = args.ref.strip().lower()
        if len(ref) != 6 or not all(c in "0123456789abcdef" for c in ref):
            sys.exit("error: --ref must be 6 lowercase hex chars")
        if (STATE_ROOT / ref).exists():
            sys.exit(f"error: ref {ref} already exists")
    else:
        ref = new_ref()

    state_dir = STATE_ROOT / ref
    state_dir.mkdir(parents=True)

    (state_dir / "context.md").write_text(context + "\n", encoding="utf-8")

    mention = format_mention(args.mention)

    # Build the reply hint. The recipient must @-mention this bot in their
    # reply, otherwise Slack will not deliver the message to the bot and the
    # paused task can never resume.
    self_uid = get_self_user_id()
    if args.reply_hint is None:
        if self_uid:
            hint = f"_Please reply with <@{self_uid}> mentioned so I can continue this task — without the mention I won't see your answer._"
        else:
            hint = "_Please @-mention this bot in your reply — without the mention I won't see your answer and can't continue the task._"
    else:
        hint = args.reply_hint.replace("{bot}", f"<@{self_uid}>" if self_uid else "@this-bot")

    parts = [f"{mention} {args.question.strip()}"]
    if hint.strip():
        parts.append(hint.strip())
    parts.append(f"[ret-ref:{ref}]")
    body = "\n\n".join(parts)

    proc = subprocess.run(
        ["python3", str(SLACK_SEND), "--channel", args.channel, "--text", body],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Roll back state so we don't leave a dangling ref
        try:
            (state_dir / "context.md").unlink()
            state_dir.rmdir()
        except OSError:
            pass
        sys.stderr.write(proc.stderr)
        sys.stdout.write(proc.stdout)
        return proc.returncode

    try:
        send_result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        send_result = {"raw": proc.stdout}

    meta = {
        "ref": ref,
        "ask_channel": send_result.get("channel") or args.channel,
        "ask_ts": send_result.get("ts"),
        "ask_permalink": send_result.get("permalink"),
        "mention": args.mention,
        "requester_channel": args.requester_channel,
        "requester_thread_ts": args.requester_thread_ts,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    (state_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"ok": True, **meta}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
