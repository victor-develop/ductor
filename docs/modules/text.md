# text/

Shared user-facing response text helpers used across bot and orchestrator layers.

## Files

- `response_format.py`: section separator, block formatter, session/error response builders, timeout/startup/recovery messages, CLI-error hint classifier

## Public API (`response_format.py`)

- `SEP`: shared visual separator line
- `fmt(*blocks)`: joins non-empty blocks with double newlines
- `classify_cli_error(raw)`: maps known CLI error patterns to short user hints
- `session_error_text(model, cli_detail="")`: standardized runtime error message
- `new_session_text(provider)`: standardized `/new` success text
- `stop_text(killed, provider)`: standardized `/stop` result text
- `timeout_warning_text(remaining)`: short warning label (for timeout-warning UI)
- `timeout_extended_text(extension, remaining_ext)`: extension notification label
- `timeout_result_text(elapsed, configured)`: standardized timeout result block
- `startup_notification_text(kind)`: startup lifecycle message (`first_start`, `system_reboot`, restart silent)
- `recovery_notification_text(kind, prompt_preview, session_name="")`: startup auto-recovery announcement text

## Integration points

- `bot/handlers.py`: `/new`, `/stop` responses
- `bot/app.py`: `/help`, `/restart`, webhook/batch status messages and other shared text blocks
- `bot/file_browser.py`: file browser panels
- `bot/welcome.py`: welcome text formatting
- `orchestrator/commands.py`: command replies (`/status`, `/memory`, `/diagnose`, `/upgrade`, ...)
- `orchestrator/flows.py`: recoverable CLI failure message rendering
- `orchestrator/cron_selector.py`: interactive cron text blocks
- `bot/message_dispatch.py`: maps system status labels (`timeout_warning`, `timeout_extended`) to UI text when emitted
- `bot/app.py`: startup lifecycle + auto-recovery notification text

## Behavior notes

- Error classification is hint-only and pattern-based (`auth`, `rate-limit`, `context-length` families).
- Session errors explicitly state that session context is preserved, so retries are safe without forced `/new`.
- Timeout warning/extension text helpers are implemented and can be surfaced by stream-status events when providers emit them.
