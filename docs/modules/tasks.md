# tasks/

Background task delegation system (`TaskHub`) used by agents and task tools.

## Files

- `ductor_bot/tasks/hub.py`: `TaskHub` lifecycle (submit, run, resume, question forwarding, cancel, maintenance)
- `ductor_bot/tasks/registry.py`: persistent task registry + task-folder seeding + orphan cleanup
- `ductor_bot/tasks/models.py`: `TaskSubmit`, `TaskEntry`, `TaskInFlight`, `TaskResult`
- `ductor_bot/orchestrator/task_selector.py`: `/tasks` interactive UI (`tsc:*` callbacks)
- `ductor_bot/_home_defaults/workspace/tools/task_tools/*.py`: CLI tool scripts (`create_task.py`, `resume_task.py`, `ask_parent.py`, `list_tasks.py`, `cancel_task.py`)

## Purpose

Run long background work without blocking the parent chat, then inject outcomes back into the parent agent session.

High-level flow:

1. create task (`/tasks/create`)
2. run in background (`TaskHub._run`)
3. optional question to parent (`/tasks/ask_parent`)
4. optional resume with follow-up (`/tasks/resume`)
5. result delivery + parent-session injection

## Persistence and Folders

Main-home task files:

- registry: `~/.ductor/tasks.json`
- task folders: `~/.ductor/workspace/tasks/<task_id>/`

Each task folder is seeded with:

- `TASKMEMORY.md`
- `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` (task-scoped rules)

Registry startup behavior:

- loads `tasks.json`
- downgrades stale `running` entries to `failed` (`"Bot restarted while task was running"`)
- removes orphans:
  - registry entries without folders
  - folders without registry entries

Periodic maintenance:

- `TaskHub.start_maintenance()` starts a cleanup loop (every 5 hours) that runs `TaskRegistry.cleanup_orphans()`.

## Config

`AgentConfig.tasks`:

- `enabled` (default `true`)
- `max_parallel` (per-chat concurrent running tasks)
- `timeout_seconds` (task execution timeout)

TaskHub enforces:

- system enabled check
- CLI service availability
- per-chat active-task cap (`max_parallel`)

## Execution Model (`TaskHub`)

`submit(TaskSubmit)`:

- resolves `chat_id` from `parent_agent` mapping when request came from CLI tools
- creates registry entry
- appends mandatory task rules suffix to prompt
- spawns `TaskHub._run(...)` as asyncio task

`_run(...)`:

- builds `AgentRequest` with:
  - `process_label="task:<task_id>"`
  - provider/model overrides from task entry
  - timeout from `tasks.timeout_seconds`
- resolves effective provider/model before first execution and persists them
- executes via the agent-specific `CLIService` when available (fallback shared CLI otherwise)
- updates status:
  - `done`: successful completion
  - `waiting`: task asked parent question during run
  - `failed`: timeout/CLI/internal error
  - `cancelled`: explicit cancel path
- appends resume hint on success when session ID exists (`resume_task.py`)

No retry queue: each task run is single-shot unless explicitly resumed.

## Question and Resume Flow

Question flow:

- task calls `python3 tools/task_tools/ask_parent.py "..."`
- script hits `POST /tasks/ask_parent`
- `TaskHub.forward_question(...)`:
  - increments `question_count`
  - stores `last_question`
  - marks in-flight state so final status becomes `waiting`
  - fire-and-forget delivery to parent handler

Resume flow:

- parent calls `python3 tools/task_tools/resume_task.py TASK_ID "..."`
- script hits `POST /tasks/resume`
- `TaskHub.resume(...)` allows resume from:
  - `done`, `failed`, `cancelled`, `waiting`
- reuses same `task_id` and task folder
- requires stored `session_id` and `provider`

## InternalAgentAPI Endpoints

Task endpoints (always registered):

- `POST /tasks/create`
- `POST /tasks/resume`
- `POST /tasks/ask_parent`
- `GET /tasks/list`
- `POST /tasks/cancel`

Behavior details:

- when no TaskHub is attached: task routes return `503` (`/tasks/list` returns empty list)
- `/tasks/list` supports `?from=<agent_name>` filtering by `parent_agent`
- `/tasks/resume` and `/tasks/cancel` enforce ownership when `from` is provided
- API class supports task-only mode (`bus=None`) where `/interagent/*` routes are absent but `/tasks/*` still work

## Supervisor Wiring

`AgentSupervisor` creates one shared TaskHub (main home paths) when `tasks.enabled=true`:

- registry path: main `tasks.json`
- folders: main `workspace/tasks`

Post-startup wiring per agent stack:

- inject shared hub into orchestrator (`orch.set_task_hub(...)`)
- register per-agent CLI service in hub
- register per-agent result/question callbacks:
  - `TelegramBot.on_task_result`
  - `TelegramBot.on_task_question`
- register agent primary chat ID for CLI-submitted task resolution

Abort/shutdown:

- main `/stop_all` callback cancels in-flight tasks across all agent stacks
- supervisor shutdown calls `TaskHub.shutdown()`

## Telegram UX (`/tasks`)

`/tasks` is orchestrator command + bot quick-command path.

UI groups tasks into:

- Running
- Waiting for answer
- Finished

Callbacks (`tsc:*`):

- refresh
- cancel one
- cancel all
- delete finished

When task system is disabled, `/tasks` returns `Task system is not enabled.`
