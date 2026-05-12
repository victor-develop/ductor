---
name: super-tasker
description: Multi-track task management for long-running agentic work. The skill owns a file-backed task DB and operates in two modes — Lead (default, walks the open frontier and dispatches one round of work) and Executor (a single task running in its own background-task session). Activates on slash commands like `/super-tasker`, natural-language asks to "run the task lead", cron-injected lead prompts, executor-completion callbacks injected into the parent session, and worker prompts that include `[stask-exec:<task-id>]`. Builds on the responsive-agent skill for human-in-the-loop continuation — there are no hard time / step / token budgets, and the agent cannot self-fire a lead pass; the loop is driven by external triggers.
---

# Super Tasker

Multi-track task orchestration that survives across agent sessions.

Designed for the ductor / Slack environment: there is no continuous agent
loop. A session ends when the model stops emitting tool calls. The "loop"
is a sequence of independent sessions stitched together by external
triggers; the skill keeps state on disk so each session can pick up where
the previous one left off.

## Modes

Two modes — the activation context tells you which one to enter.

- **Lead** (default). Walk the open frontier, dispatch one round of work,
  report, stop. Entered on `/super-tasker`, natural-language equivalents,
  a cron-injected message, a `create_task.py` completion callback (an
  executor finished and its result was injected into the parent session),
  or a `responsive-agent` resume whose saved context is not executor-scoped.
- **Executor**. Carry out one specific task in a dedicated background-task
  session spawned by `scripts/spawn_executor.py`. Entered only when the
  initial prompt contains `[stask-exec:<task-id>]`.

If both signals are present, executor wins — finish the assigned task
before doing any lead work.

## What triggers a lead pass

There is **no in-process loop and no self-trigger**. The agent cannot send
itself a Slack message to wake up a new session — sending a message to
your own chat does not re-enter the model. A new lead pass only fires when
one of these external events drops a prompt into the agent's session:

1. **User Slack message** — the user types `/super-tasker`, "do a lead
   pass", or any natural-language equivalent.
2. **Executor completion callback** — `tools/task_tools/create_task.py`
   delivers the finished task's result back into the parent session as a
   message. That message re-enters the model and the skill should run a
   lead pass on the new state.
3. **Responsive-agent resume** — a paused lead wakes up because the user
   replied. The saved context is replayed, and if it does not carry an
   executor anchor, treat it as a lead-pass continuation.
4. **Cron or webhook** — `tools/cron_tools/` or `tools/webhook_tools/`
   fire a prompt into the agent. Cron is an external scheduler, not the
   agent talking to itself.

A lead pass ends after dispatch + report. Do not poll, do not loop, do
not try to schedule the next pass from inside the current one. The next
pass arrives from one of the four triggers above.

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

`Splitted`, `Merged`, `Completed`, `Dropped` are terminal — **one-way
state transitions, no rollup**. Concretely:

- `a` splits into `a1, a2` → `a` is permanently `Splitted` and is never
  revived, re-opened, or re-tracked. The active set replaces `a` with
  `{a1, a2}`. There is no "complete the parent when all children are done"
  rollup — the parent has already left the working set.
- `b1, b2` merge into `b3` → `b1, b2` are permanently `Merged` and are no
  longer tracked. The active set replaces them with `{b3}`. Completion
  of the original goal is reached when `b3` itself terminates positively.
- A goal is "done" when every currently-active leaf descended from it
  has reached a terminal status. The lead never walks back up to
  reconcile a Splitted/Merged ancestor.

### Frontier definition

The **frontier** is the lead's working set: every non-terminal task whose
`split_from` parent is either absent or itself terminal.

A separate, stricter notion of **top-level** is used only for the
user-facing "list my major initiatives" view:

- `relations.split_from is None` AND
- no other task has this task in its `relations.merged_from`.

The lead pass walks the **frontier**, not top-level. (Children of a
Splitted parent are on the frontier; the Splitted parent is not.)

### Recursion depth limit

`depth` is capped at **3**. A task at depth 3 cannot be split further —
the lead must execute it or merge it sideways. `tasker.py split` rejects
attempts beyond the cap.

## Lead pass

Run on every activation that is not in executor mode.

1. `python3 scripts/tasker.py brief --json` — get the frontier summary
   (every non-terminal task whose split_from parent is terminal-or-absent,
   with depth, complexity, last event, blocked deps, and any pending
   reeval signals).
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
   Then stop. Do not poll, do not sleep, do not try to keep the session
   alive. The next lead pass is fired by one of the four external triggers
   above (user msg, executor completion callback, responsive-agent resume,
   cron/webhook).

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

When pausing from an executor, **always** include the `[stask-exec:<id>]`
anchor in the saved context so the resume can route correctly:

```text
## Task
[stask-exec:ab12cd34]
<rest of context>
```

On resume, `responsive-agent`'s `resume.py` scans the saved context for
`[stask-exec:<id>]` and, if found, surfaces that anchor on the first
output line. The replaying agent therefore re-enters executor mode for
that task without having to read the body first. If no executor anchor is
present, the resume is a lead-pass continuation.

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

- **Manual lead pass**: the user sends `/super-tasker` (or natural
  language) in any Slack chat. This is the primary entry point.
- **Cron-driven lead pass**: create a cron via `tools/cron_tools/` whose
  payload is a prompt like `[super-tasker:lead-pass] do a lead pass`.
  Ductor's cron runner injects the payload into the agent's session as a
  fresh prompt — that is what wakes the model up. The bracketed token is
  purely a hint for the agent; it does nothing on its own.
- **Executor completion**: handled automatically by ductor. When the
  background task spawned via `create_task.py` finishes, its result is
  injected into the parent session as a message. The skill should react
  by running a lead pass on the new state.
- **Worker resume**: handled automatically by responsive-agent — the
  saved context carries the `[stask-exec:<id>]` anchor and `resume.py`
  surfaces it at the top of the replay so the agent re-enters executor
  mode without having to scan the body.

Do **not** try to wake yourself up. Sending a Slack message to your own
chat (whether via the messenger tools or any other means) does not
re-enter the model — only the four external triggers do.

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
