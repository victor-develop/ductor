# bot/

Telegram interface layer (`aiogram`): handlers, middleware, streaming delivery, callbacks, and rich sender.

## Files

- `app.py`: `TelegramBot` lifecycle, handler registration, callback routing, observer bridges
- `message_dispatch.py`: shared streaming/non-streaming execution paths
- `handlers.py`: command helper handlers (`/new`, `/stop`, generic command path)
- `text/response_format.py` (outside `bot/`): shared command/error text builders (`/new`, `/stop`, session error hints)
- `middleware.py`: `AuthMiddleware`, `SequentialMiddleware`, quick-command bypass, queue tracking
- `welcome.py`: `/start` text + quick action callbacks (`w:*`)
- `file_browser.py`: interactive `~/.ductor/` browser (`sf:`/`sf!`)
- `streaming.py`, `edit_streaming.py`: stream editors
- `sender.py`: rich text/file sending (`send_rich`, `<file:...>` handling, MIME-based photo/document choice)
- `formatting.py`: markdown-to-Telegram HTML conversion/chunking
- `buttons.py`: `[button:...]` parsing
- `media.py`: media download/index/prompt conversion (delegates shared helpers in `ductor_bot/files/`)
- `abort.py`, `dedup.py`, `typing.py`, `topic.py`: shared runtime helpers

## Command ownership

Bot-level handlers (`app.py`):

- `/start`, `/help`, `/info`, `/showfiles`, `/stop`, `/stop_all`, `/restart`, `/new`, `/session`, `/sessions`, `/tasks`, `/agent_commands`
- main-agent only bot handlers: `/agents`, `/agent_start`, `/agent_stop`, `/agent_restart` (routed into orchestrator command path)

Command-menu note:

- `/stop_all` is handled but not included in `BOT_COMMANDS` popup list (intentional "power command" behavior).

Orchestrator-routed commands:

- `/status`, `/memory`, `/model`, `/cron`, `/diagnose`, `/upgrade`, `/sessions`, `/tasks`

## Middleware behavior

### `AuthMiddleware`

- drops message/callback updates from users outside `allowed_user_ids`
- when `group_mention_only=true`, group/supergroup message events bypass the allowlist check (access is then gated by mention/reply checks in message resolution)

### `SequentialMiddleware`

Message flow order:

1. abort trigger check before lock:
   - `/stop_all` or stop-all phrases (`stop all`, `stopp alle`, `alles stoppen`, `cancel all`, `abort all`)
   - `/stop` and bare abort words
2. quick command bypass (`/status`, `/memory`, `/cron`, `/diagnose`, `/model`, `/showfiles`, `/sessions`, `/tasks`)
3. dedupe by `chat_id:message_id`
4. acquire per-chat lock for normal messages
5. queued messages get indicator + cancel button (`mq:<entry_id>`)

`/stop_all` behavior detail:

- main agent: local abort + supervisor callback (`abort_all_agents`) to stop active runs across all agent stacks and cancel in-flight async inter-agent tasks
- sub-agent: callback is not wired, so it degrades to local-only abort

`/model` special case in quick-command handler: when chat is busy (active process or queued messages), bot returns immediate \"agent is working\" text instead of opening the selector.

Queue API:

- `is_busy(chat_id)`
- `has_pending(chat_id)`
- `cancel_entry(chat_id, entry_id)`
- `drain_pending(chat_id)`

## Message dispatch (`message_dispatch.py`)

### Non-streaming

`run_non_streaming_message()`:

- `TypingContext`
- `orchestrator.handle_message()`
- `send_rich()`

### Streaming

`run_streaming_message()`:

- create stream editor
- use `StreamCoalescer` for text batching
- forward callbacks:
  - text delta
  - tool activity
  - system status (`thinking`, `compacting`, `recovering`, `timeout_warning`, `timeout_extended`)
- finalize editor
- fallback path:
  - `stream_fallback` or empty stream -> `send_rich(full_text)`
  - otherwise only send extracted files via `send_files_from_text()`

Timeout-status note:

- `message_dispatch.py` maps `timeout_warning` and `timeout_extended` to visible labels.
- timeout warning/extension callbacks are not wired by default, so these labels are not emitted in current runtime paths unless custom status events are introduced.

## Callback routing

Handled namespaces in `TelegramBot._route_special_callback`:

- `mq:*` queue cancel
- `upg:*` upgrade callbacks
- `ms:*` model selector
- `crn:*` cron selector
- `nsc:*` session selector
- `tsc:*` task selector
- `ns:*` named-session follow-up callbacks from result buttons
- `sf:*` / `sf!` file browser

Lock behavior:

- model selector, cron selector, session selector, `ns:*` follow-up callbacks, and `sf!` file-request callbacks acquire per-chat lock
- queue cancel, upgrade callbacks, and `sf:` directory navigation do not

Generic callbacks are converted to user answer text and routed through normal message flow.

## Forum topic support

All send paths propagate `message_thread_id` from topic messages via `get_thread_id()`.

Sessions remain keyed by `chat_id` (no per-topic session split).

## File safety and `file_access`

`send_file()` validates paths against allowed roots.

`file_access` mapping:

- `all` -> unrestricted
- `home` -> only under home directory
- `workspace` -> only under `~/.ductor/workspace`

Implementation note:

- allowed roots are resolved through `files.allowed_roots.resolve_allowed_roots(...)` (shared with API server).
- MIME detection for send path uses `files.tags.guess_mime(...)` (magic bytes + extension fallback), and SVG is sent as document.

## Observer bridges in bot layer

`TelegramBot._on_startup()` wires:

- cron result handler
- heartbeat result handler
- webhook result handler
- webhook wake handler
- session result handler

Supervisor task wiring (`AgentSupervisor._wire_task_hub`) additionally attaches:

- `on_task_result(...)` delivery callback
- `on_task_question(...)` delivery callback

Wake handler path (`_handle_webhook_wake`) acquires per-chat lock, routes prompt through orchestrator, then sends response.

Webhook result forwarding sends only `cron_task` results because wake responses are sent directly by wake handler.

## Startup lifecycle and auto-recovery

`TelegramBot._on_startup()` also performs restart-aware lifecycle steps:

1. consume restart/upgrade sentinels and send completion messages
2. detect startup kind via `startup_state.detect_startup_kind(...)`
3. persist current startup state via `save_startup_state(...)`
4. broadcast startup notification on:
   - `first_start`
   - `system_reboot`
   (`service_restart` is intentionally silent; restart-sentinel startup is also silent)
5. run `RecoveryPlanner` over:
   - interrupted foreground turns (`inflight_turns.json`)
   - named sessions that were `running` before restart (downgraded to `idle` on load)
6. send per-chat auto-recovery notice text and re-submit named-session follow-ups where safe
7. clear inflight tracker state after recovery pass
