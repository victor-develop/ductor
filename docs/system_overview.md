# System Overview

This document is the fastest way to understand how ductor works end-to-end.

## 1) What ductor runs

At runtime, one Python process hosts:

- one mandatory main agent (`AgentStack`)
- zero or more sub-agents from `~/.ductor/agents.json`
- one shared `AgentSupervisor`
- one shared in-memory inter-agent bus (`InterAgentBus`)
- one internal HTTP bridge for CLI tool scripts (`InternalAgentAPI`, port `8799`)
- one shared background task coordinator (`TaskHub`, when `tasks.enabled=true`)

Each agent stack contains:

- `TelegramBot` (ingress + callbacks)
- `Orchestrator` (routing + flows)
- `CLIService` (provider wrappers)
- provider subprocesses (`claude`, `codex`, `gemini`)

## 2) Primary message path (Telegram)

```text
Telegram update
  -> AuthMiddleware
  -> SequentialMiddleware (per-chat lock + queue)
  -> bot/app.py handlers
  -> Orchestrator.handle_message(_streaming)
  -> CLIService
  -> provider subprocess
  -> Telegram response (stream edits or one-shot)
```

Notes:

- `/stop` is handled before normal routing (middleware/bot path).
- `/stop_all` is also middleware/bot-level; on the main agent it aborts active runs across all agents (on sub-agents it falls back to local abort).
- `/new` resets only the active provider bucket, not all provider buckets.
- `group_mention_only=true` allows group/supergroup ingress without allowlist match, but only mention-addressed messages are processed.

## 3) Optional API path (WebSocket)

When `config.api.enabled=true` and PyNaCl is installed:

```text
/ws
  -> auth frame: {type:"auth", token, e2e_pk, chat_id?}
  -> auth_ok
  -> encrypted frames (NaCl Box)
  -> Orchestrator.handle_message_streaming(...)
```

API files:

- upload: `POST /upload` -> `~/.ductor/workspace/api_files/YYYY-MM-DD/...`
- download: `GET /files?path=...` (Bearer auth + file root checks)

## 4) Background systems

Started in-process by orchestrator/supervisor:

- named background sessions (`BackgroundObserver`)
- delegated background tasks (`TaskHub`, shared across agents when enabled)
- cron (`CronObserver`)
- webhooks (`WebhookObserver`)
- heartbeat (`HeartbeatObserver`)
- cleanup (`CleanupObserver`)
- codex model cache observer
- gemini model cache observer
- config hot-reloader (`ConfigReloader`)
- rule sync watcher
- skill sync watcher
- shared knowledge sync (`SHAREDMEMORY.md` -> all agents)

## 5) Session model

Three independent systems exist:

1. chat sessions: `~/.ductor/sessions.json`
   - provider-isolated buckets per chat
   - model/provider switching preserves per-provider context
2. named sessions: `~/.ductor/named_sessions.json`
   - used by `/session` and by inter-agent deterministic sessions (`ia-<sender>`)
3. delegated task registry: `~/.ductor/tasks.json`
   - task metadata for shared `TaskHub` runs
   - task folders under `~/.ductor/workspace/tasks/<task_id>/`

Startup recovery state:

- `~/.ductor/inflight_turns.json` tracks in-flight foreground turns for crash/restart recovery.
- `~/.ductor/startup_state.json` stores boot/session startup metadata (`first_start`, `service_restart`, `system_reboot` detection).
- startup recovery replays safe named-session follow-ups and sends interruption notices for foreground tasks that need manual resend.

## 6) Internal API bridges

CLI tool scripts call internal API endpoints:

- `POST /interagent/send`
- `POST /interagent/send_async`
- `GET /interagent/agents`
- `GET /interagent/health`
- `POST /tasks/create`
- `POST /tasks/resume`
- `POST /tasks/ask_parent`
- `GET /tasks/list`
- `POST /tasks/cancel`

Key behavior:

- sync calls block until recipient result
- async calls return `task_id` immediately
- recipient receives a Telegram preview for async tasks
- sender receives final async result in Telegram
- `new_session=true` forces fresh recipient inter-agent session
- task endpoints enforce owner checks for resume/cancel when `from=<agent>` is provided

## 7) Runtime files you need to know

Main home (`~/.ductor`):

- `config/config.json`
- `sessions.json`
- `named_sessions.json`
- `tasks.json`
- `cron_jobs.json`
- `webhooks.json`
- `agents.json`
- `startup_state.json`
- `inflight_turns.json`
- `SHAREDMEMORY.md`
- `logs/agent.log`
- `workspace/` (rules, memory, tools, tasks, cron_tasks, telegram_files, output_to_user, api_files, skills)

Sub-agent home (`~/.ductor/agents/<name>/`):

- own `config/config.json` (effective runtime view)
- own `sessions.json`, `named_sessions.json`, cron/webhook state
- own `workspace/`

## 8) Where to read code first

1. `ductor_bot/__main__.py` (CLI entry and lifecycle)
2. `ductor_bot/multiagent/supervisor.py` (always-on runtime model)
3. `ductor_bot/bot/app.py` (Telegram handlers)
4. `ductor_bot/orchestrator/core.py` (routing + wiring)
5. `ductor_bot/orchestrator/flows.py` (normal/streaming behavior)
6. `ductor_bot/tasks/hub.py` and `ductor_bot/tasks/registry.py`
7. `ductor_bot/cli/service.py` and provider wrappers

## 9) Command surface (high-level)

Telegram core:

- `/new`, `/stop`, `/stop_all`, `/model`, `/status`, `/memory`, `/session`, `/sessions`, `/tasks`, `/cron`, `/diagnose`, `/upgrade`

Telegram multi-agent (main only):

- `/agents`, `/agent_start`, `/agent_stop`, `/agent_restart`, `/agent_commands`

CLI:

- `ductor`
- `ductor service ...`
- `ductor docker ...`
- `ductor api ...`
- `ductor agents ...`
