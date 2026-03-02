# Architecture

## Runtime Overview

```text
Telegram Update
  -> aiogram Dispatcher/Router
  -> AuthMiddleware (allowlist; group/supergroup pass-through when `group_mention_only=true`)
  -> SequentialMiddleware (message updates only)
       - exact /stop_all (or stop-all phrase): kill local active CLI process(es) + optional cross-agent callback + drain pending queue
       - exact /stop or bare abort keyword: kill active local CLI process(es) + drain pending queue
       - quick commands (/status /memory /cron /diagnose /model /showfiles /sessions /tasks): lock bypass
       - otherwise: dedupe + per-chat lock (+ queue tracking)
  -> TelegramBot handler
       - /start /help /info /showfiles /stop /restart /new /session /sessions /tasks
       - normal text/media -> Orchestrator
       - callback routes (model selector, cron selector, session selector, task selector, file browser, named-session buttons, upgrade, queue cancel)
  -> Orchestrator
       - slash command -> CommandRegistry
       - directives (@...)
       - normal/streaming flow -> CLIService
  -> CLI provider subprocess (Claude or Codex or Gemini)
  -> Telegram output (stream edits/appends, buttons, files)

Direct API message (optional, `api.enabled=true`)
  -> ApiServer (`/ws`)
  -> per-chat API lock + auth/session routing
  -> Orchestrator.handle_message_streaming(...)
  -> CLI provider subprocess (Claude or Codex or Gemini)
  -> WebSocket stream events + final result
```

Background systems:

- `CronObserver`: schedules `cron_jobs.json` entries.
- `HeartbeatObserver`: periodic checks in existing sessions.
- `WebhookObserver`: HTTP ingress for external triggers.
- `CleanupObserver`: daily retention cleanup for workspace file directories.
- `BackgroundObserver`: named session (`/session`) task execution and result delivery.
- `TaskHub`: delegated background task execution (`/tasks` UI + `/tasks/*` internal API endpoints).
- `GeminiCacheObserver`: periodic Gemini model-cache refresh (`~/.ductor/config/gemini_models.json`).
- `CodexCacheObserver`: periodic Codex model-cache refresh (`~/.ductor/config/codex_models.json`).
- `UpdateObserver`: periodic PyPI version check + Telegram notification (upgradeable installs only).
- `ConfigReloader`: hot-reload watcher for safe `config.json` fields.
- Rule-sync task: keeps existing `CLAUDE.md`, `AGENTS.md`, `GEMINI.md` siblings mtime-synced inside `~/.ductor/workspace/`.
- Skill-sync task: syncs skills across `~/.ductor/workspace/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/`.

Optional network service:

- `ApiServer`: direct WebSocket + HTTP file endpoints (`/ws`, `/health`, `/files`, `/upload`).

Multi-agent runtime core (always active; sub-agents optional):

- `AgentSupervisor`: bootstraps the main agent and manages dynamic sub-agents with crash recovery.
- `InterAgentBus`: in-memory async message passing between agents.
- `InternalAgentAPI`: HTTP bridge (`127.0.0.1:8799` on host, `0.0.0.0:8799` in Docker mode) for CLI tool scripts to reach the bus and task hub.
- `SharedKnowledgeSync`: watches `SHAREDMEMORY.md` and injects content into all agents' `MAINMEMORY.md`.

## Startup Flow

### `ductor` (`ductor_bot/__main__.py`)

Default path:

1. `_is_configured()` validates `telegram_token` and access mode: either `allowed_user_ids` is non-empty or `group_mention_only=true`.
2. If unconfigured: run onboarding wizard (`init_wizard.run_onboarding()`).
3. If onboarding successfully installed a service, exit early.
4. Configure logging.
5. Load/create `~/.ductor/config/config.json`.
6. Deep-merge runtime config with `AgentConfig` defaults.
7. Run `init_workspace(paths)` inside `load_config()`.
8. Validate required runtime fields in `run_telegram()`.
9. Acquire PID lock (`bot.pid`, `kill_existing=True`).
10. Start `AgentSupervisor` (always-on runtime wrapper for main + optional sub-agents).

### `AgentSupervisor` startup (`ductor_bot/multiagent/supervisor.py`)

1. Start `InterAgentBus`.
2. Start `InternalAgentAPI` (`127.0.0.1:8799` host mode, `0.0.0.0:8799` Docker mode).
3. If `tasks.enabled=true`: create shared `TaskHub` (`~/.ductor/tasks.json` + `~/.ductor/workspace/tasks/`) and attach it to `InternalAgentAPI`.
4. Create/start main `AgentStack` (`TelegramBot` + `Orchestrator`).
5. Wait up to 120s for main readiness (`_main_ready`) before sub-agent startup.
6. Load/start sub-agents from `agents.json`.
7. Start `SharedKnowledgeSync` (`SHAREDMEMORY.md` -> all agent `MAINMEMORY.md`).
8. Start `agents.json` watcher.
9. Block on main-agent completion and return its exit code.

### `TelegramBot` startup (`ductor_bot/bot/app.py`)

1. Create orchestrator via `Orchestrator.create(config)`.
2. Fetch bot identity (`get_me`).
3. Consume restart sentinel and notify chat if present.
4. Attach cron, heartbeat, webhook, and session result handlers + webhook wake handler.
   - task result/question handlers are wired by supervisor hook when `TaskHub` is enabled.
5. Consume upgrade sentinel and notify chat if present.
6. Detect startup kind (`first_start` / `service_restart` / `system_reboot`) from `startup_state.json` and persist current state.
7. Broadcast startup notification only when:
   - no restart sentinel was consumed, and
   - startup kind is not `service_restart`.
8. Run `RecoveryPlanner` on:
   - interrupted foreground turns (`inflight_turns.json`, max age `config.timeouts.normal * 2`),
   - named sessions recovered from persisted `running` state.
9. Send per-chat recovery notifications; for safe named-session actions, auto-submit background follow-ups.
10. Clear inflight tracker file after recovery pass.
11. Start `UpdateObserver` only for upgradeable installs.
12. Sync Telegram command list.
13. Start restart-marker watcher.

### `Orchestrator.create()` (`ductor_bot/orchestrator/core.py`)

1. Resolve paths from `ductor_home`.
2. Set process-wide `DUCTOR_HOME` env var only for the main agent (sub-agents use per-subprocess env to avoid races).
3. If Docker enabled: run `DockerManager.setup()` (includes auth mounts + optional `mount_host_cache` + `docker.mounts`).
4. If Docker container is active: re-sync skills in Docker-safe copy mode.
5. Inject runtime environment notice into workspace rule files (`inject_runtime_environment`).
6. Build orchestrator instance.
   - initialize `InflightTracker` (`~/.ductor/inflight_turns.json`)
   - load named-session registry (downgrades persisted `running` entries to `idle` and marks them for recovery planning)
7. Check provider auth (`check_all_auth`) and set authenticated provider set.
8. Start `GeminiCacheObserver` (`~/.ductor/config/gemini_models.json`) and refresh runtime Gemini model registry from its callback.
9. Start `CodexCacheObserver` (`~/.ductor/config/codex_models.json`).
10. Create `BackgroundObserver`, `CronObserver`, and `WebhookObserver` (cron/webhook share Codex cache).
   - named background timeout path uses `config.timeouts.background`
11. Start cron, heartbeat, webhook, cleanup observers (disabled observers no-op in `start()`).
12. If `api.enabled=true`: start `ApiServer` (auto-generate token when empty, wire message/abort handlers and file context). If PyNaCl is unavailable, startup logs a warning and skips API server startup.
13. Start rule-sync and skill-sync watcher tasks.
14. Start `ConfigReloader` (`config.json` poll every 5s; hot fields applied, restart-required fields logged).

## Message Routing

### Command ownership

- Bot-level handlers: `/start`, `/help`, `/info`, `/showfiles`, `/stop`, `/stop_all`, `/restart`, `/new`, `/session`, `/sessions`, `/tasks`, `/agent_commands`.
- Orchestrator command registry: `/new`, `/status`, `/model`, `/memory`, `/cron`, `/diagnose`, `/upgrade`, `/sessions`, `/tasks`.
- Main agent also registers `/agents`, `/agent_start`, `/agent_stop`, `/agent_restart` handlers in the bot layer (routed into orchestrator via supervisor hooks).
- `/stop` and `/stop_all` are middleware/bot-local and do not route through orchestrator command dispatch.
- `/stop_all` uses supervisor callback wiring on the main agent to abort active work across all agent stacks (sub-agent fallback is local-only).
- Quick-command bypass applies to `/status`, `/memory`, `/cron`, `/diagnose`, `/model`, `/showfiles`, `/sessions`, `/tasks`.
- `/showfiles` is handled directly in bot layer.
- `/model` bypass has busy check: when active work/queue exists, it returns immediate "agent is working" feedback.

### Directives (`ductor_bot/orchestrator/directives.py`)

- Only directives at message start are parsed.
- Model directive syntax: `@<model-id>`.
- Known model IDs come from:
  - `CLAUDE_MODELS` (`haiku`, `sonnet`, `opus`)
  - `_GEMINI_ALIASES` (`auto`, `pro`, `flash`, `flash-lite`)
  - dynamically discovered Gemini model IDs from local Gemini CLI files.
- Parser token syntax is `@([a-zA-Z][a-zA-Z0-9_-]*)`; dots are not part of a directive token.
- Other `@key` / `@key=value` directives are collected as raw directives.
- Directive-only messages (`@sonnet`) return guidance text instead of executing.

### Input security scan

`Orchestrator._handle_message_impl()` always runs `detect_suspicious_patterns(text)` before routing. Matches are logged as warnings; routing is not blocked at this layer.

## Normal Conversation Flow

`normal()` / `normal_streaming()` in `ductor_bot/orchestrator/flows.py`:

1. Determine requested model/provider.
2. Resolve session (`SessionManager.resolve_session`) with provider-isolated buckets.
3. New session only: append `MAINMEMORY.md` as `append_system_prompt`, then append multi-agent roster context when available.
4. Apply message hooks (`MAINMEMORY_REMINDER` every 6th message).
5. Build `AgentRequest` with `resume_session` if available.
   - foreground timeout path resolves via `resolve_timeout(config, "normal")` -> `config.timeouts.normal`
6. Gemini safeguard: if target provider is Gemini, auth mode is API-key, and `gemini_api_key` in config is empty/`"null"`, return warning text and skip CLI call.
7. Persist in-flight turn record (`InflightTracker.begin`) before CLI execution.
8. Execute CLI (`CLIService.execute` or `execute_streaming`).
9. Always clear in-flight turn record in `finally` (`InflightTracker.complete(chat_id)`).
10. Error behavior:
   - recoverable:
     - SIGKILL: reset only active provider bucket and retry once.
     - invalid resumed session (`invalid session` / `session not found`): reset active provider bucket and retry once.
   - other errors: kill processes, preserve session, return session-error guidance.
11. On success: persist session ID (if changed), counters, cost/tokens, and optional session-age note.

## Streaming Path

Bot runtime path uses `bot/message_dispatch.py`:

1. `run_streaming_message()` creates stream editor + `StreamCoalescer`.
2. Orchestrator callbacks feed text/tool/system events.
3. System status mapping:
   - `thinking` -> `THINKING`
   - `compacting` -> `COMPACTING`
   - `recovering` -> `Please wait, recovering...`
   - `timeout_warning` -> `TIMEOUT APPROACHING`
   - `timeout_extended` -> `TIMEOUT EXTENDED`
   - timeout labels appear only when stream paths emit those system-status events
4. Finalization:
   - flush coalescer,
   - finalize editor,
   - fallback/no-stream-content -> send full text with `send_rich`,
   - otherwise only send `<file:...>` outputs.

`CLIService.execute_streaming()` fallback behavior:

- checks `ProcessRegistry.was_aborted()` on each event, so `/stop` exits quickly,
- if stream errors or result event is missing:
  - aborted chat -> empty result,
  - non-error stream with accumulated text -> return accumulated text,
  - otherwise retry non-streaming and mark `stream_fallback=True`.

## Direct API Flow

`ApiServer` (`ductor_bot/api/server.py`) runs independently from aiogram and calls orchestrator callbacks directly.

Per connection:

1. `ws://<host>:<port>/ws` handshake.
2. First frame must be auth JSON with `type="auth"`, `token`, and `e2e_pk` (10s timeout).
   - `auth_ok` returns E2E server key plus provider metadata (`providers`) and active runtime fields when available.
3. Session `chat_id` default is computed from `config.api.chat_id` using truthiness (`0` falls back to first `allowed_user_ids` entry, then `1`); auth payload may override with a positive int (no allowlist membership check).
4. `message` frames run under per-`chat_id` lock and use streaming callbacks (`text_delta`, `tool_activity`, `system_status`, `result`).
5. `abort` frame or `/stop` message calls orchestrator abort path (`abort(chat_id)`), which kills CLI processes, cancels background tasks, and ends named sessions for that chat.

Additional HTTP endpoints:

- `GET /health` (no auth),
- `GET /files?path=...` (Bearer auth + `file_access` root checks),
- `POST /upload` (Bearer auth + multipart save to `workspace/api_files/YYYY-MM-DD/`).

## Callback Query Flow

`TelegramBot._on_callback_query()`:

1. answer callback.
2. resolve welcome shortcut callbacks (`w:*`).
3. route special namespaces:
   - `mq:*` queue cancel,
   - `upg:*` upgrade flow,
   - `ms:*` model selector,
   - `crn:*` cron selector,
   - `nsc:*` session selector,
   - `tsc:*` task selector,
   - `ns:*` named-session follow-up buttons,
   - `sf:*` / `sf!` file browser.
4. generic callback path:
   - append `[USER ANSWER] ...` when possible,
   - acquire per-chat lock,
   - route callback payload through normal message pipeline.

Lock usage is path-dependent:

- queue cancel and upgrade callbacks do not acquire the per-chat message lock
- selector/file-request callbacks and `ns:*` named-session follow-up callbacks run under the per-chat lock

## Background Systems

### Named sessions (`/session`) flow

- `TelegramBot._on_session(...)` creates a named session via `Orchestrator.submit_named_session(...)`.
- `BackgroundObserver` enforces max 5 active tasks per chat and runs tasks asynchronously.
- `NamedSessionRegistry` enforces max 10 user-created named sessions per chat (`/session`) and persists to `~/.ductor/named_sessions.json`.
- Inter-agent sessions use deterministic names (`ia-<sender>`) and are inserted via `NamedSessionRegistry.add(...)` (separate path from user `/session` cap checks).
- Named sessions use `CLIService.execute()` with `resume_session` for follow-up persistence.
- named-session background timeout path is `config.timeouts.background`.
- Follow-ups: `@session-name <message>` (foreground streaming) or `/session @session-name <message>` (background).
- Completion callback (`TelegramBot._on_session_result`) sends a tagged Telegram message with session name.
- `/stop` kills active CLI subprocesses, cancels background tasks, and ends all named sessions for the chat.
- `/sessions` shows interactive management UI with end/refresh controls.

### Delegated tasks (`TaskHub` + `/tasks`) flow

- shared task hub is created by supervisor when `tasks.enabled=true`.
- parent agents submit/manage tasks through task tools (`tools/task_tools/*.py`) calling `InternalAgentAPI /tasks/*`.
- task metadata persists in `~/.ductor/tasks.json`; per-task folders live in `~/.ductor/workspace/tasks/<task_id>/`.
- `/tasks` renders interactive management UI (cancel/cleanup/refresh) via `task_selector.py` (`tsc:*` callbacks).
- task worker questions (`/tasks/ask_parent`) are forwarded to parent agent chat, then injected back into parent session via `handle_task_question`.
- task completions/failures are delivered to Telegram and injected into parent session via `handle_task_result`.
- `abort_all_agents()` and supervisor shutdown also cancel in-flight task-hub tasks.

### Cron flow

- `CronObserver` watches `cron_jobs.json` mtime every 5 seconds.
- Uses timezone-aware scheduling (`job.timezone` -> `config.user_timezone` -> host TZ -> UTC).
- Execution path:
  - optional dependency lock,
  - quiet-hour gate (job-level fields only; no fallback to heartbeat quiet settings),
  - validate task folder,
  - resolve task overrides (`provider`, `model`, `reasoning_effort`, `cli_parameters`),
  - build provider command,
  - run subprocess with timeout,
  - parse output,
  - persist status.
- Result delivery wiring:
  - `Orchestrator.set_cron_result_handler(...)` -> `CronObserver.set_result_handler(...)`
  - `TelegramBot._on_cron_result(...)` posts results to all allowed users.

### Heartbeat flow

- Observer loop runs every `interval_minutes`.
- Skips quiet hours and busy chats; performs stale process cleanup first.
- `heartbeat_flow()`:
  - read-only session lookup,
  - skip if no resumable session,
  - enforce cooldown,
  - execute heartbeat prompt with `resume_session`,
  - strip `ack_token`.
- ACK-only result is suppressed (no Telegram send, no session metric update).
- Non-ACK result updates session and is delivered by bot handler.

### Webhook flow

- `WebhookObserver.start()` auto-generates and persists global webhook token if empty.
- HTTP route: `POST /hooks/{hook_id}`.
- Validation chain: rate limit -> content-type -> JSON object -> hook exists/enabled -> per-hook auth.
- Valid requests return `202` immediately; dispatch runs async.
- Mode routing:
  - `wake`: uses bot wake handler (`_handle_webhook_wake`) and normal message pipeline under per-chat lock.
  - `cron_task`: runs one-shot provider execution in `cron_tasks/<task_folder>` with task overrides + hook-level quiet hours (no heartbeat fallback) + dependency queue.
- Bot forwards only `cron_task` results from webhook result callback (`wake` responses already delivered by wake handler).

### Cleanup flow

- Hourly check in `user_timezone`.
- Runs at most once per day when local hour equals `cleanup.check_hour`.
- Deletes old files recursively in `workspace/telegram_files/`, `workspace/output_to_user/`, and `workspace/api_files/`.
- Prunes empty subdirectories after file deletion (including date-based upload folders).

## Restart & Supervisor

### In-process restart triggers

- `/restart`: write restart sentinel, set exit code `42`, stop polling.
- Marker-based restart: if `restart-requested` file appears, set exit code `42` and stop polling.
- `__main__` restart handling:
  - when supervisor env is present (`DUCTOR_SUPERVISOR` or `INVOCATION_ID`), process exits with `42`,
  - otherwise process re-execs itself (`_re_exec_bot`) for direct foreground usage.

Service backends (systemd/launchd/Task Scheduler) handle restart semantics in installed mode.

## Workspace Seeding Model

Template source:

- `ductor_bot/_home_defaults/` mirrors runtime `~/.ductor/` layout.

Copy rules in `workspace/init.py` (`_walk_and_copy`):

- Zone 2 (always overwrite):
  - `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`
  - `.py` files in `workspace/tools/cron_tools/`, `workspace/tools/webhook_tools/`, `workspace/tools/agent_tools/`, and `workspace/tools/task_tools/`
- Zone 3 (seed once): all other files.
- `RULES*.md` templates are skipped in raw copy and deployed by `RulesSelector`.
- Hidden/ignored dirs are skipped.

Rule deployment (`workspace/rules_selector.py`):

- discovers template directories containing `RULES*.md`,
- selects variant by auth status:
  - `all-clis` when 2+ providers are authenticated,
  - otherwise one of `claude-only`, `codex-only`, `gemini-only`,
  - fallback `RULES.md`.
- deploys runtime files based on auth:
  - Claude -> `CLAUDE.md`
  - Codex -> `AGENTS.md`
  - Gemini -> `GEMINI.md`
- removes stale provider files for unauthenticated providers (except user-owned cron-task rule files).

## Logging Context

- `log_context.py` uses `ContextVar` fields (`agent_name`, `operation`, `chat_id`, `session_id`) to enrich logs as `[agent:op:chat_id:session_id_8]` (missing fields omitted).
- ingress operation labels include: `msg`, `cb`, `cron`, `hb`, `wh`, `api`, `ia-async`.
- multi-agent supervisor sets `agent_name` per agent task, so all agents can share one central log file with clear attribution.
- `logging_config.py` configures colored console logs and rotating file logs (`~/.ductor/logs/agent.log`).

## Multi-Agent System

Always-on supervisor model: main agent always runs under `AgentSupervisor`; sub-agents are optional.

```text
AgentSupervisor
  +-- AgentStack "main"    (TelegramBot -> Orchestrator -> CLIService)
  +-- AgentStack "sub-1"   (TelegramBot -> Orchestrator -> CLIService)
  +-- AgentStack "sub-2"   (TelegramBot -> Orchestrator -> CLIService)
  +-- InterAgentBus        (in-memory sync + async messaging)
  +-- InternalAgentAPI     (127.0.0.1:8799 host / 0.0.0.0:8799 Docker, bridges CLI tools to bus + task hub)
  +-- TaskHub              (shared task registry/execution when enabled)
  +-- SharedKnowledgeSync  (SHAREDMEMORY.md -> all agents' MAINMEMORY.md)
```

Each sub-agent has its own Telegram bot token, workspace, sessions, and CLI service. Agents communicate via the `InterAgentBus` (in-memory) or `InternalAgentAPI` (localhost HTTP for CLI tool scripts). Delegated tasks use one shared `TaskHub` in the main home.

`AgentSupervisor` watches `agents.json` via `FileWatcher` and auto-starts/stops agents on changes (running-agent auto-restart is currently token-change driven). Each agent runs in a supervised asyncio task with crash recovery (exponential backoff, max 5 retries). Sub-agent restart requests (exit code 42) trigger in-process hot-reload; main agent restart requests propagate to the service/runtime restart path.

Inter-agent execution nuance:

- sync inter-agent turns run via `Orchestrator.handle_interagent_message()` with `chat_id=allowed_user_ids[0]` when available, otherwise `0` (with warning)
- async inter-agent result delivery is routed to the agent's first allowed user ID
- provider changes during inter-agent conversations auto-end the old `ia-<sender>` session and start a fresh one, with a user-facing notice

Detailed behavior: `docs/modules/multiagent.md`.

## Core Design Trade-offs

- JSON files over DB: transparent and easy to inspect.
- In-process observers: simple deployment, lifecycle tied to bot process.
- Per-chat lock + queue tracking: strong ordering and race prevention at chat level.
- Stream coalescing + edit mode: better Telegram UX with controlled update frequency.
