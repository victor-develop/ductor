#!/usr/bin/env python3
"""spawn_executor.py — launch a background task to execute one super-tasker task.

Wraps tools/task_tools/create_task.py with the super-tasker executor prompt.
The prompt contains the `[stask-exec:<id>]` anchor so the spawned session
enters Executor mode (see SKILL.md).

Usage:
    python3 scripts/spawn_executor.py <task-id> [--role execute|plan]
                                                [--extra "extra instructions"]
                                                [--name "human name"]
                                                [--priority background|interactive|batch]

Defaults: role=execute (run the task), priority=background.

Pass --role plan to spawn a planner subagent for an M/L task — the planner
decides whether to split / merge / execute and writes the corresponding events.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
SKILL_DIR = THIS_DIR.parent
TASKER = THIS_DIR / "tasker.py"

DUCTOR_HOME = Path(os.environ.get("DUCTOR_HOME") or (Path.home() / ".ductor-slack"))
TASK_TOOLS = DUCTOR_HOME / "workspace" / "tools" / "task_tools"
CREATE_TASK = TASK_TOOLS / "create_task.py"


EXEC_TEMPLATE = """\
[stask-exec:{task_id}]
[super-tasker mode=executor role={role} task={task_id}]

You are running as the **{role}** for super-tasker task `{task_id}`.

Read the skill spec at:
  {skill_dir}/SKILL.md

The DB CLI is at:
  {tasker}

Task snapshot at spawn time:
{task_snapshot}

Recent events:
{events_tail}

Allowed reads (context window for this task):
  - {task_dir}/task.json
  - {task_dir}/events.jsonl
  - {task_dir}/context/  (everything in here)
  - The absolute paths in task.context_files (listed above)

Hard rules:
  1. Read ONLY the files listed above. Do not browse the wider workspace.
  2. First call: `python3 {tasker} update {task_id} --status WIP --by executor`
     and append a one-line plan via `--state "<plan>"`.
  3. Append a `state` event for each meaningful step.
  4. If you need a human, use the responsive-agent skill. Include the
     `[stask-exec:{task_id}]` anchor in the saved context so the resumed
     session re-enters executor mode for this task. responsive-agent's
     resume.py will surface that anchor on its first output line.
  5. If the task is too big, append a `reeval-request` event with a concrete
     proposed breakdown and stop. Do NOT split it yourself — that is the
     lead's job.
  6. When done: `update --status Completed --state "<final summary>"`.

{role_extra}

{extra}
"""

ROLE_EXTRAS = {
    "execute": (
        "Your role is to FINISH this task. Do not split or merge. If you "
        "discover it is actually too big, follow rule 5 and stop."
    ),
    "plan": (
        "Your role is PLANNER. Decide one of:\n"
        "  (a) stay-and-execute (downgrade complexity if needed and run the task)\n"
        "  (b) split into N children with dependencies\n"
        "  (c) merge with a sibling and create a combined task\n"
        "Use these tasker.py commands:\n"
        "  - split: `tasker.py split {task_id} --child 'name|complexity[|desc]' ...`\n"
        "  - merge: `tasker.py merge <id1> <id2> --name '...' --complexity M`\n"
        "  - downgrade: `tasker.py update {task_id} --complexity S` then execute.\n"
        "After deciding, append a `state` event explaining the choice. If you "
        "split, the lead will pick up the children on the next pass — you do "
        "NOT spawn workers for them."
    ),
}


def load_snapshot(task_id: str) -> tuple[dict, list[dict], Path]:
    # Run tasker.py show to get a consistent snapshot.
    proc = subprocess.run(
        ["python3", str(TASKER), "show", task_id, "--tail", "20"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"tasker.py show failed: {proc.stderr.strip() or proc.stdout.strip()}")
    data = json.loads(proc.stdout)
    task = data["task"]
    events = data["events"]
    # Resolve task_dir from STASK_STATE_DIR or default.
    root = Path(os.environ.get("STASK_STATE_DIR") or (DUCTOR_HOME / "workspace" / "super_tasker_state"))
    return task, events, root / "tasks" / task_id


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("task_id")
    ap.add_argument("--role", choices=("execute", "plan"), default="execute")
    ap.add_argument("--extra", default="", help="extra instructions appended to the prompt")
    ap.add_argument("--name", default="", help="human-readable task name for ductor's task list")
    ap.add_argument("--priority", default="background", choices=("interactive", "background", "batch"))
    ap.add_argument("--dry-run", action="store_true", help="print the prompt instead of spawning")
    args = ap.parse_args(argv)

    if not CREATE_TASK.exists():
        raise SystemExit(f"create_task.py not found at {CREATE_TASK} — is DUCTOR_HOME set correctly?")

    task, events, task_dir = load_snapshot(args.task_id)
    snapshot_lines = json.dumps(task, indent=2, ensure_ascii=False)
    events_tail = (
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events[-10:])
        if events else "(no events yet)"
    )

    prompt = EXEC_TEMPLATE.format(
        task_id=args.task_id,
        role=args.role,
        role_extra=ROLE_EXTRAS[args.role],
        skill_dir=SKILL_DIR,
        tasker=TASKER,
        task_dir=task_dir,
        task_snapshot=snapshot_lines,
        events_tail=events_tail,
        extra=args.extra,
    )

    if args.dry_run:
        sys.stdout.write(prompt)
        return 0

    cmd = ["python3", str(CREATE_TASK)]
    name = args.name or f"super-tasker {args.role} {args.task_id}"
    cmd += ["--name", name, "--priority", args.priority, prompt]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
