---
name: super-tasker
description: Multi-track task management for long-running agentic work. The skill owns a file-backed task DB and operates in two modes — Lead (default, scans the open tree and orchestrates work) and Executor (a single task is delegated to a worker). Activates on slash commands like `/super-tasker`, natural-language asks to "run the task lead", cron pings that include `[super-tasker:lead-pass]`, and worker prompts that include `[stask-exec:<task-id>]`. Builds on the responsive-agent skill for human-in-the-loop continuation — there are no hard time / step / token budgets; the loop is driven by Slack events and cron ticks.
---

# Super Tasker

Multi-track task orchestration that survives across agent sessions.

Designed for the ductor / Slack environment: there is no continuous agent
loop. The "loop" is a sequence of independent sessions (cron ticks, Slack
replies, responsive-agent resumes). The skill keeps state on disk so each
session can pick up exactly where the previous one left off.

## Modes

Two modes — the activation context tells you which one to enter.

- **Lead** (default). Scan the open task tree, decide what to do next, and
  delegate work. Entered on `/super-tasker`, natural-language equivalents,
  cron pings, or any time the skill is invoked without an executor anchor.
- **Executor**. Carry out one specific task. Entered only when the prompt
  contains `[stask-exec:<task-id>]`. The wrapper that spawned you put that
  anchor there.

If both signals are present, executor wins — finish the assigned task
before doing any lead work.

## Data model

Backing store: `~/.ductor-slack/workspace/super_tasker_state/`.

```
super_tasker_state/
├── tasks/
│   └── <id>/
│       ├── task.json     # canonical attributes + relations
│       ├── events.jsonl  # append-only event feed
│       └── context/      # files the executor is allowed to read
└── index.json            # id → name/status/depth cache (rebuildable)
```

`task.json` fields:

| field          | type                                  | notes                                                                                 |
| -------------- | ------------------------------------- | ------------------------------------------------------------------------------------- |
| `id`           | 8-hex string                          | stable, never reused                                                                  |
| `name`         | string                                | one-line title                                                                        |
| `desc`         | markdown string                       | what + why                                                                            |
| `state`        | markdown string                       | latest snapshot of progress; the executor overwrites this when it reports             |
| `status`       | `Open` `WIP` `Completed` `Dropped` `Merged` `Splitted` | see transitions below                              |
| `complexity`   | `S` `M` `L`                           | size estimate                                                                         |
| `depth`        | int                                   | 0 for user-created roots, +1 per split                                                |
| `context_files`| list of absolute paths                | the executor MUST limit reading to these + the task's own dir                          |
| `relations`    | object                                | `split_from`, `merged_from`, `depends_on` (see below)                                  |
| `created_at`   | ISO-8601                              |                                                                                       |
| `updated_at`   | ISO-8601                              |                                                                                       |

Relations:

- `split_from: <id> | null` — the parent this task was split out of.
- `merged_from: [<id>, ...]` — sources that were collapsed into this task.
- `depends_on: [<id>, ...]` — must reach a terminal-positive status before
  this task can move to `WIP`.

`events.jsonl` rows (one per line):

```json
{"ts":"2026-05-12T03:00:00Z","kind":"created","by":"lead","note":"..."}
{"ts":"...","kind":"status","from":"Open","to":"WIP","by":"executor"}
{"ts":"...","kind":"state","by":"executor","note":"<short>"}
{"ts":"...","kind":"reeval-request","by":"executor","note":"too big, want split"}
{"ts":"...","kind":"user-note","by":"user","note":"prefer option B"}
{"ts":"...","kind":"split","children":["...","..."],"by":"lead"}
{"ts":"...","kind":"merge","sources":["...","..."],"into":"<id>","by":"lead"}
```

The event feed is the source of truth for "why did this move". `state` is
the latest snapshot for quick reading.

### Status transitions

```
Open ──► WIP ──► Completed
  │        │ ──► Dropped
  │        │ ──► Splitted    (children created — this task is now terminal)
  │        │ ──► Merged      (collapsed into another — terminal)
  └─► Splitted / Merged / Dropped (allowed without entering WIP)
```

`Splitted`, `Merged`, `Completed`, `Dropped` are terminal. Per Victor's
spec: a split task stays in `Splitted` forever and is never revived.

### Top-level definition

A task is **top-level** iff:

- `relations.split_from is None` AND
- no other task has this task in its `relations.merged_from`.

The lead pass only considers top-level tasks with `status in {Open, WIP}`.

### Recursion depth limit

`depth` is capped at **3**. A task at depth 3 cannot be split further —
the lead must execute it or merge it sideways. `tasker.py split` rejects
attempts beyond the cap.

## Lead pass

Run on every activation that is not in executor mode.

1. `python3 scripts/tasker.py brief --json` — get the active-root summary
   (Open + WIP top-level tasks, with their depth, complexity, last event,
   blocked deps, and any pending reeval signals).
2. For each active root, decide one of:
   - **idle** — task is `WIP` with an executor already in flight (last
     event is recent and there is no completion). Skip.
   - **execute** — task is `Open`, complexity `S`, dependencies all
     terminal-positive (`Completed` or `Merged`). Spawn an executor.
   - **plan** — task is `Open`, complexity `M`/`L`, depth < 3, AND has a
     fresh `reeval-request` event OR has never been planned. Spawn a
     planner subagent (still uses `spawn_executor.py`, with the planner
     prompt template). The planner decides: stay-and-execute, split into
     N children, or merge with siblings.
   - **wait** — dependencies not satisfied. Skip; lead will re-check
     next pass.
   - **ask** — task is ambiguous in a way only the user can resolve.
     Pause via the **responsive-agent** skill. Do not invent a default.
3. After delegating, write a short summary to Slack:
   - which roots you looked at,
   - what you delegated and why,
   - what is waiting on the user or on dependencies.
   Then stop. Do not poll, do not sleep. The next lead pass is triggered
   by the next user message, the cron tick, or the executor completing.

### Re-evaluation cadence

Open design point in the draft — the chosen default is **triggered, not
on-every-pass**. Reasons:

- Every re-eval is a subagent call. Doing it for every Open task on every
  lead pass is expensive and noisy.
- The cases that actually need re-eval are well-defined and observable
  in the event feed.

Re-eval is triggered when **any** of these is true for a task:

- The executor appended a `reeval-request` event.
- The task is `Open` and has never been planned (no prior `split`,
  `merge`, or `execute` event).
- The user appended a `user-note` that explicitly says "reevaluate" /
  "rethink" / "拆一下".
- The task has been `Open` for ≥ `STASK_STALE_DAYS` (default 3) with no
  events — likely abandoned, worth reconsidering.

Tune the staleness threshold per workspace via env or by editing the
`brief` output filter in `tasker.py`.

## Executor mode

Entered only when the prompt contains `[stask-exec:<task-id>]`.

Hard rules:

1. **Scope.** Read only the task's own dir (`task.json`, `events.jsonl`,
   `context/`) and the absolute paths in `context_files`. Do not read
   other tasks or unrelated parts of the workspace. The launcher already
   filtered context — trust it.
2. **First action.** `tasker.py update <id> --status WIP` and append a
   `state` event with one line of plan.
3. **Progress reporting.** Each meaningful step → append a `state` event.
   Overwrite `task.state` with the latest snapshot. Keep it short — the
   feed has the history.
4. **Need info from a human?** Pause via the **responsive-agent** skill.
   Include the task id in the question. Do not block.
5. **Realize it is too big?** Append a `reeval-request` event with a
   concrete proposed breakdown and stop. The lead will pick it up next
   pass.
6. **Done.** `tasker.py update <id> --status Completed` and write a
   final `state` event with the deliverable pointer (file path, link,
   summary). Do not message the requester directly unless the launcher's
   prompt told you to — the lead is responsible for reporting up.

## User intervention surface

The user does not edit `events.jsonl` directly. All interventions are
verbs the user can speak to ductor in Slack — the agent translates them
to tasker.py calls.

| user says…                                            | agent runs                                                      |
| ----------------------------------------------------- | ---------------------------------------------------------------- |
| "list my tasks" / "show open tasks"                   | `tasker.py list --top-level --status Open WIP`                  |
| "show task abc12345"                                  | `tasker.py show abc12345`                                       |
| "add a task: build the X" / "new task: …"             | `tasker.py add --name "…" --desc "…" [--complexity M]`          |
| "drop task abc12345 — out of date"                    | `tasker.py update abc12345 --status Dropped --note "out of date"`|
| "for task abc12345, prefer option B"                  | `tasker.py event abc12345 --kind user-note --note "prefer B"`   |
| "reeval task abc12345"                                | `tasker.py event abc12345 --kind reeval-request --note "user"`  |
| "merge abc12345 + def67890 into one"                  | `tasker.py merge abc12345 def67890 --name "…" --desc "…"`        |
| "run the task lead" / `/super-tasker`                 | enter Lead mode (run a lead pass)                                |

The lead reads `user-note` events on the next pass and adjusts plans
accordingly.

## Integration with responsive-agent

The lead and the executor both pause through the responsive-agent skill.
The responsive-agent skill is the only continuation mechanism — there are
no internal timeouts or retry loops in super-tasker.

When pausing, **always** include the task id and the mode in the question
context so the resume can route correctly:

```text
## Task
[super-tasker mode=executor task=ab12cd34]
<rest of context>
```

On resume, responsive-agent will replay the saved context. If the context
contains `[stask-exec:<id>]`, treat the next session as executor for that
task; otherwise treat it as a lead-pass continuation.

## Scripts

All scripts live under `scripts/` and are invoked via `python3`:

- `scripts/tasker.py` — the only DB tool. Subcommands: `init`, `add`,
  `list`, `show`, `update`, `event`, `split`, `merge`, `brief`, `gc`.
  Run with `--help` per subcommand. Output is JSON unless `--text` is
  passed.
- `scripts/spawn_executor.py` — wrapper that builds the executor prompt
  (with `[stask-exec:<id>]` and the task's context window) and calls
  `tools/task_tools/create_task.py`. Use this from lead instead of
  hand-rolling the prompt.

## Cron / activation patterns

- Daily / hourly lead tick (suggested): create a cron via
  `tools/cron_tools/` that pings ductor with a message like
  `[super-tasker:lead-pass] do a lead pass`. The bracketed token both
  triggers activation and reminds the agent which mode.
- Manual: send `/super-tasker` (or natural language) in any Slack chat.
- Worker resume: handled automatically by responsive-agent — the saved
  context carries the `[stask-exec:<id>]` anchor.

## Storage hygiene

- `tasker.py gc` prunes context dirs of tasks in terminal status older
  than `STASK_GC_DAYS` (default 30). It leaves `task.json` and
  `events.jsonl` as audit trail.
- `tasker.py rebuild-index` recomputes `index.json` from the on-disk
  task dirs. Run after manual edits.

## Non-goals

- This is **not** an agentic loop framework. There are no in-loop
  budgets (time, steps, tokens). Control comes from invocation cadence
  (cron + Slack), file persistence, and responsive-agent.
- This is **not** a project manager UI. The user lives in Slack; the
  surface is verbs, not a board.
- The skill does not auto-spawn sub-agents (different bots). It uses
  background tasks via `tools/task_tools/`. Sub-agent creation remains
  user-initiated only, per ductor rules.
