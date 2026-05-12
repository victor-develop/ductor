#!/usr/bin/env python3
"""tasker.py — file-backed task DB for the super-tasker skill.

State root: ~/.ductor-slack/workspace/super_tasker_state/

Subcommands (all emit JSON unless --text):
  init                       create the state root
  add                        create a new task (top-level or sub)
  list                       list tasks, with filters
  show <id>                  print task.json + events tail
  update <id>                mutate status / complexity / state / name / desc
  event <id>                 append an event row
  split <id>                 mark a task Splitted, create N children
  merge <ids...>             mark sources Merged, create one new task
  brief                      lead-pass summary (active roots + signals)
  gc                         prune context dirs of long-terminal tasks
  rebuild-index              recompute index.json from task dirs

Status enum: Open WIP Completed Dropped Merged Splitted
Complexity:  S M L
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Iterable

STATUSES = ("Open", "WIP", "Completed", "Dropped", "Merged", "Splitted")
TERMINAL = ("Completed", "Dropped", "Merged", "Splitted")
TERMINAL_POSITIVE = ("Completed", "Merged")  # dependencies count as satisfied
COMPLEXITIES = ("S", "M", "L")
EVENT_KINDS = (
    "created",
    "status",
    "state",
    "complexity",
    "rename",
    "reeval-request",
    "user-note",
    "split",
    "merge",
    "depend",
)
MAX_DEPTH = 3


# ---------------------------------------------------------------------------
# Paths and io helpers
# ---------------------------------------------------------------------------

def state_root() -> Path:
    override = os.environ.get("STASK_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ductor-slack" / "workspace" / "super_tasker_state"


def tasks_dir() -> Path:
    return state_root() / "tasks"


def index_path() -> Path:
    return state_root() / "index.json"


def task_dir(task_id: str) -> Path:
    return tasks_dir() / task_id


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    # 8 hex chars — ~4 billion slots, collision-free for any realistic backlog.
    return secrets.token_hex(4)


def ensure_state() -> None:
    tasks_dir().mkdir(parents=True, exist_ok=True)
    if not index_path().exists():
        index_path().write_text("{}\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)


def load_task(task_id: str) -> dict:
    p = task_dir(task_id) / "task.json"
    if not p.exists():
        raise SystemExit(f"task not found: {task_id}")
    return read_json(p)


def save_task(task: dict) -> None:
    task["updated_at"] = now_iso()
    write_json(task_dir(task["id"]) / "task.json", task)
    update_index_entry(task)


def append_event(task_id: str, kind: str, **extra) -> dict:
    if kind not in EVENT_KINDS:
        raise SystemExit(f"unknown event kind: {kind}")
    row = {"ts": now_iso(), "kind": kind, **extra}
    with (task_dir(task_id) / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_events(task_id: str) -> list[dict]:
    p = task_dir(task_id) / "events.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ---------------------------------------------------------------------------
# Index — flat map of id -> {name, status, complexity, depth, top_level}
# ---------------------------------------------------------------------------

def load_index() -> dict:
    if not index_path().exists():
        return {}
    try:
        return read_json(index_path())
    except json.JSONDecodeError:
        return {}


def save_index(idx: dict) -> None:
    write_json(index_path(), idx)


def update_index_entry(task: dict) -> None:
    idx = load_index()
    idx[task["id"]] = {
        "name": task["name"],
        "status": task["status"],
        "complexity": task["complexity"],
        "depth": task["depth"],
    }
    save_index(idx)


def all_task_ids() -> list[str]:
    if not tasks_dir().exists():
        return []
    return sorted(p.name for p in tasks_dir().iterdir() if (p / "task.json").exists())


def all_tasks() -> list[dict]:
    return [load_task(tid) for tid in all_task_ids()]


# ---------------------------------------------------------------------------
# Relations / top-level
# ---------------------------------------------------------------------------

def is_top_level(task: dict, all_tasks_cache: list[dict] | None = None) -> bool:
    """Victor's strict definition: a user-created root with no parent at all.

    Used for the user-facing `list --top-level` view (major initiatives).
    """
    if task["relations"].get("split_from"):
        return False
    if all_tasks_cache is None:
        all_tasks_cache = all_tasks()
    for other in all_tasks_cache:
        if task["id"] in other["relations"].get("merged_from", []):
            return False
    return True


def is_frontier(task: dict, by_id: dict[str, dict] | None = None) -> bool:
    """A task is on the frontier (lead's working set) if it is non-terminal
    AND its split_from parent is either absent or terminal. Merge sources are
    terminal (`Merged`) and so are not on the frontier; the merge result is.
    """
    if task["status"] in TERMINAL:
        return False
    parent_id = task["relations"].get("split_from")
    if not parent_id:
        return True
    if by_id is None:
        by_id = {t["id"]: t for t in all_tasks()}
    parent = by_id.get(parent_id)
    if parent is None:
        return True
    return parent["status"] in TERMINAL


def deps_satisfied(task: dict) -> tuple[bool, list[str]]:
    """Return (satisfied, unsatisfied_ids)."""
    blockers: list[str] = []
    for dep in task["relations"].get("depends_on", []):
        try:
            dep_task = load_task(dep)
        except SystemExit:
            blockers.append(dep)
            continue
        if dep_task["status"] not in TERMINAL_POSITIVE:
            blockers.append(dep)
    return (not blockers, blockers)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def make_task(
    name: str,
    desc: str,
    complexity: str,
    depth: int = 0,
    split_from: str | None = None,
    merged_from: list[str] | None = None,
    depends_on: list[str] | None = None,
    context_files: list[str] | None = None,
    state_text: str = "",
) -> dict:
    if complexity not in COMPLEXITIES:
        raise SystemExit(f"complexity must be one of {COMPLEXITIES}")
    if depth > MAX_DEPTH:
        raise SystemExit(f"depth {depth} exceeds MAX_DEPTH={MAX_DEPTH}")
    tid = new_id()
    task_dir(tid).mkdir(parents=True, exist_ok=False)
    (task_dir(tid) / "context").mkdir(exist_ok=True)
    (task_dir(tid) / "events.jsonl").touch()
    task = {
        "id": tid,
        "name": name,
        "desc": desc,
        "state": state_text,
        "status": "Open",
        "complexity": complexity,
        "depth": depth,
        "context_files": context_files or [],
        "relations": {
            "split_from": split_from,
            "merged_from": merged_from or [],
            "depends_on": depends_on or [],
        },
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    save_task(task)
    append_event(tid, "created", by="lead", note=f"complexity={complexity} depth={depth}")
    return task


def transition_status(task: dict, new_status: str, by: str, note: str = "") -> dict:
    if new_status not in STATUSES:
        raise SystemExit(f"status must be one of {STATUSES}")
    if task["status"] in TERMINAL:
        raise SystemExit(f"task {task['id']} is terminal ({task['status']}), cannot change")
    if task["status"] == new_status:
        return task
    append_event(task["id"], "status", **{"from": task["status"], "to": new_status, "by": by, "note": note})
    task["status"] = new_status
    save_task(task)
    return task


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> Any:
    ensure_state()
    return {"ok": True, "root": str(state_root())}


def cmd_add(args: argparse.Namespace) -> Any:
    ensure_state()
    task = make_task(
        name=args.name,
        desc=args.desc or "",
        complexity=args.complexity,
        depth=args.depth,
        split_from=args.split_from,
        depends_on=args.depends_on or [],
        context_files=args.context_files or [],
        state_text=args.state or "",
    )
    return task


def cmd_list(args: argparse.Namespace) -> Any:
    ensure_state()
    tasks = all_tasks()
    by_id = {t["id"]: t for t in tasks}
    out: list[dict] = []
    for t in tasks:
        if args.status and t["status"] not in args.status:
            continue
        if args.complexity and t["complexity"] not in args.complexity:
            continue
        if args.top_level and not is_top_level(t, tasks):
            continue
        if args.frontier and not is_frontier(t, by_id):
            continue
        out.append({
            "id": t["id"],
            "name": t["name"],
            "status": t["status"],
            "complexity": t["complexity"],
            "depth": t["depth"],
            "top_level": is_top_level(t, tasks),
            "frontier": is_frontier(t, by_id),
            "deps": t["relations"]["depends_on"],
            "split_from": t["relations"]["split_from"],
            "updated_at": t["updated_at"],
        })
    return out


def cmd_show(args: argparse.Namespace) -> Any:
    task = load_task(args.id)
    events = read_events(args.id)
    if args.tail:
        events = events[-args.tail:]
    return {"task": task, "events": events, "top_level": is_top_level(task)}


def cmd_update(args: argparse.Namespace) -> Any:
    task = load_task(args.id)
    if args.status:
        task = transition_status(task, args.status, by=args.by, note=args.note or "")
    if args.complexity:
        if args.complexity not in COMPLEXITIES:
            raise SystemExit(f"complexity must be one of {COMPLEXITIES}")
        if task["complexity"] != args.complexity:
            append_event(task["id"], "complexity", **{"from": task["complexity"], "to": args.complexity, "by": args.by, "note": args.note or ""})
            task["complexity"] = args.complexity
    if args.name:
        append_event(task["id"], "rename", **{"from": task["name"], "to": args.name, "by": args.by})
        task["name"] = args.name
    if args.desc is not None:
        task["desc"] = args.desc
    if args.state is not None:
        task["state"] = args.state
        append_event(task["id"], "state", by=args.by, note=args.note or "")
    if args.add_context:
        task["context_files"] = sorted(set(task["context_files"]) | set(args.add_context))
    if args.add_dep:
        task["relations"]["depends_on"] = sorted(set(task["relations"]["depends_on"]) | set(args.add_dep))
        for dep in args.add_dep:
            append_event(task["id"], "depend", on=dep, by=args.by)
    save_task(task)
    return task


def cmd_event(args: argparse.Namespace) -> Any:
    load_task(args.id)  # validate existence
    return append_event(args.id, args.kind, by=args.by, note=args.note or "")


def cmd_split(args: argparse.Namespace) -> Any:
    parent = load_task(args.id)
    if parent["status"] in TERMINAL:
        raise SystemExit(f"cannot split terminal task {parent['id']} ({parent['status']})")
    if parent["depth"] >= MAX_DEPTH:
        raise SystemExit(f"depth {parent['depth']} at MAX_DEPTH; cannot split further")
    if not args.children:
        raise SystemExit("provide at least one --child")
    children: list[dict] = []
    for spec in args.children:
        # spec format: "name|complexity[|desc]"
        parts = spec.split("|", 2)
        if len(parts) < 2:
            raise SystemExit(f"bad --child spec: {spec!r}; expected name|complexity[|desc]")
        c_name, c_complexity = parts[0], parts[1]
        c_desc = parts[2] if len(parts) > 2 else ""
        child = make_task(
            name=c_name,
            desc=c_desc,
            complexity=c_complexity,
            depth=parent["depth"] + 1,
            split_from=parent["id"],
            context_files=list(parent["context_files"]),
        )
        children.append(child)
    append_event(parent["id"], "split", children=[c["id"] for c in children], by=args.by, note=args.note or "")
    parent = transition_status(parent, "Splitted", by=args.by, note=args.note or "")
    return {"parent": parent, "children": children}


def cmd_merge(args: argparse.Namespace) -> Any:
    sources = [load_task(sid) for sid in args.sources]
    for s in sources:
        if s["status"] in TERMINAL:
            raise SystemExit(f"cannot merge terminal task {s['id']} ({s['status']})")
    depth = min(s["depth"] for s in sources)
    merged_ctx: list[str] = []
    for s in sources:
        merged_ctx.extend(s["context_files"])
    merged_ctx = sorted(set(merged_ctx))
    result = make_task(
        name=args.name,
        desc=args.desc or "",
        complexity=args.complexity,
        depth=depth,
        merged_from=[s["id"] for s in sources],
        context_files=merged_ctx,
    )
    for s in sources:
        append_event(s["id"], "merge", into=result["id"], by=args.by, note=args.note or "")
        transition_status(s, "Merged", by=args.by, note=f"merged into {result['id']}")
    return {"result": result, "sources": [s["id"] for s in sources]}


def _stale_days(task: dict) -> float:
    try:
        ts = _dt.datetime.strptime(task["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 0.0
    ts = ts.replace(tzinfo=_dt.timezone.utc)
    return (_dt.datetime.now(_dt.timezone.utc) - ts).total_seconds() / 86400.0


def _has_event_kind(task_id: str, kind: str, since_event_kinds: Iterable[str] = ()) -> bool:
    """True if the latest run of `since_event_kinds`-resetting events is followed
    by at least one `kind` event. Used to detect *unhandled* reeval-requests."""
    reset = set(since_event_kinds)
    found = False
    for ev in read_events(task_id):
        if reset and ev["kind"] in reset:
            found = False
        if ev["kind"] == kind:
            found = True
    return found


def cmd_brief(args: argparse.Namespace) -> Any:
    ensure_state()
    stale_days = float(os.environ.get("STASK_STALE_DAYS", "3"))
    tasks = all_tasks()
    by_id = {t["id"]: t for t in tasks}
    active_roots: list[dict] = []
    for t in tasks:
        if not is_frontier(t, by_id):
            continue
        sat, blockers = deps_satisfied(t)
        events = read_events(t["id"])
        last_event = events[-1] if events else None
        # has the executor (or user) asked for re-eval that hasn't been resolved yet?
        # split/merge events reset the signal.
        reeval_pending = _has_event_kind(t["id"], "reeval-request", since_event_kinds=("split", "merge"))
        user_note_pending = _has_event_kind(t["id"], "user-note", since_event_kinds=("split", "merge", "status"))
        planned_yet = any(ev["kind"] in ("split", "merge") for ev in events) or any(
            ev["kind"] == "status" and ev.get("to") == "WIP" for ev in events
        )
        signals = {
            "deps_satisfied": sat,
            "blockers": blockers,
            "reeval_pending": reeval_pending,
            "user_note_pending": user_note_pending,
            "never_planned": not planned_yet,
            "stale_days": round(_stale_days(t), 2),
            "stale": _stale_days(t) >= stale_days and t["status"] == "Open" and not events[1:],
        }
        # suggested action — heuristic, the lead is free to override
        if not sat:
            action = "wait"
        elif t["status"] == "WIP":
            action = "idle"
        elif t["complexity"] == "S":
            action = "execute"
        elif signals["reeval_pending"] or signals["never_planned"] or signals["stale"]:
            action = "plan" if t["depth"] < MAX_DEPTH else "execute"
        else:
            action = "execute"
        active_roots.append({
            "id": t["id"],
            "name": t["name"],
            "status": t["status"],
            "complexity": t["complexity"],
            "depth": t["depth"],
            "split_from": t["relations"].get("split_from"),
            "last_event": last_event,
            "signals": signals,
            "suggested_action": action,
        })
    return {"now": now_iso(), "frontier": active_roots, "total": len(tasks)}


def cmd_gc(args: argparse.Namespace) -> Any:
    gc_days = float(os.environ.get("STASK_GC_DAYS", "30"))
    pruned: list[str] = []
    for t in all_tasks():
        if t["status"] not in TERMINAL:
            continue
        if _stale_days(t) < gc_days:
            continue
        ctx_dir = task_dir(t["id"]) / "context"
        if ctx_dir.exists() and any(ctx_dir.iterdir()):
            for child in ctx_dir.iterdir():
                if child.is_file():
                    child.unlink()
                else:
                    # leave subdirs untouched; user may have nested artefacts
                    pass
            pruned.append(t["id"])
    return {"pruned": pruned, "gc_days": gc_days}


def cmd_rebuild_index(args: argparse.Namespace) -> Any:
    idx: dict = {}
    for t in all_tasks():
        idx[t["id"]] = {
            "name": t["name"],
            "status": t["status"],
            "complexity": t["complexity"],
            "depth": t["depth"],
        }
    save_index(idx)
    return {"ok": True, "count": len(idx)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tasker.py", description=__doc__)
    p.add_argument("--text", action="store_true", help="pretty-print JSON instead of one-line JSON")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--text", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[common])

    pa = sub.add_parser("add", help="create a new task", parents=[common])
    pa.add_argument("--name", required=True)
    pa.add_argument("--desc", default="")
    pa.add_argument("--complexity", choices=COMPLEXITIES, default="M")
    pa.add_argument("--depth", type=int, default=0)
    pa.add_argument("--split-from", default=None, help="parent task id (sets relations.split_from)")
    pa.add_argument("--depends-on", nargs="*", default=[])
    pa.add_argument("--context-files", nargs="*", default=[])
    pa.add_argument("--state", default="")

    pl = sub.add_parser("list", parents=[common])
    pl.add_argument("--status", nargs="*", choices=STATUSES)
    pl.add_argument("--complexity", nargs="*", choices=COMPLEXITIES)
    pl.add_argument("--top-level", action="store_true",
                    help="strict roots only (Victor's definition: no SplitFrom parent + not in any merged_from)")
    pl.add_argument("--frontier", action="store_true",
                    help="lead's working set: non-terminal tasks whose parent is terminal-or-absent")

    ps = sub.add_parser("show", parents=[common])
    ps.add_argument("id")
    ps.add_argument("--tail", type=int, default=0, help="only the last N events (0=all)")

    pu = sub.add_parser("update", parents=[common])
    pu.add_argument("id")
    pu.add_argument("--status", choices=STATUSES)
    pu.add_argument("--complexity", choices=COMPLEXITIES)
    pu.add_argument("--name")
    pu.add_argument("--desc")
    pu.add_argument("--state")
    pu.add_argument("--add-context", nargs="*", default=[])
    pu.add_argument("--add-dep", nargs="*", default=[])
    pu.add_argument("--by", default="agent")
    pu.add_argument("--note", default="")

    pe = sub.add_parser("event", parents=[common])
    pe.add_argument("id")
    pe.add_argument("--kind", required=True, choices=EVENT_KINDS)
    pe.add_argument("--note", default="")
    pe.add_argument("--by", default="agent")

    psp = sub.add_parser("split", parents=[common])
    psp.add_argument("id")
    psp.add_argument("--child", dest="children", action="append", required=True,
                     help="child spec 'name|complexity[|desc]'; repeat for each child")
    psp.add_argument("--by", default="lead")
    psp.add_argument("--note", default="")

    pm = sub.add_parser("merge", parents=[common])
    pm.add_argument("sources", nargs="+")
    pm.add_argument("--name", required=True)
    pm.add_argument("--desc", default="")
    pm.add_argument("--complexity", choices=COMPLEXITIES, default="M")
    pm.add_argument("--by", default="lead")
    pm.add_argument("--note", default="")

    sub.add_parser("brief", parents=[common])
    sub.add_parser("gc", parents=[common])
    sub.add_parser("rebuild-index", parents=[common])

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {
        "init": cmd_init,
        "add": cmd_add,
        "list": cmd_list,
        "show": cmd_show,
        "update": cmd_update,
        "event": cmd_event,
        "split": cmd_split,
        "merge": cmd_merge,
        "brief": cmd_brief,
        "gc": cmd_gc,
        "rebuild-index": cmd_rebuild_index,
    }[args.cmd]
    result = handler(args)
    if args.text:
        if isinstance(result, (dict, list)):
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(result)
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
