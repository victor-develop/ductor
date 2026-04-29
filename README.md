<p align="center">
  <img src="https://raw.githubusercontent.com/PleasePrompto/ductor/main/ductor_slack/messenger/telegram/ductor_images/logo_text.png" alt="ductor" width="100%" />
</p>

<p align="center">
  <strong>Claude Code, Codex CLI, and Gemini CLI as your coding assistant — on Telegram, Matrix, and Slack.</strong><br>
  Uses only official CLIs. Nothing spoofed, nothing proxied. Multi-transport, automation, and sub-agents in one runtime.
</p>

<p align="center">
  <a href="https://pypi.org/project/ductor-slack/"><img src="https://img.shields.io/pypi/v/ductor-slack?color=blue" alt="PyPI" /></a>
  <a href="https://pypi.org/project/ductor-slack/"><img src="https://img.shields.io/pypi/pyversions/ductor-slack?v=1" alt="Python" /></a>
  <a href="https://github.com/PleasePrompto/ductor/blob/main/LICENSE"><img src="https://img.shields.io/github/license/PleasePrompto/ductor" alt="License" /></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &middot;
  <a href="#how-chats-work">How chats work</a> &middot;
  <a href="#commands">Commands</a> &middot;
  <a href="docs/README.md">Docs</a> &middot;
  <a href="#contributing">Contributing</a>
</p>

---

If you want to control Claude Code, Google's Gemini CLI, or OpenAI's Codex CLI via Telegram, Matrix, or Slack, build automations, or manage multiple agents easily — ductor is the right tool for you. The messaging layer is modular and transports plug into the same transport-agnostic core.

ductor runs on your machine and sends simple console commands as if you were typing them yourself, so you can use your active subscriptions (Claude Max, etc.) directly. No API proxying, no SDK patching, no spoofed headers. Just the official CLIs, executed as subprocesses, with all state kept in plain JSON and Markdown under `~/.ductor-slack/`.

<p align="center">
  <img src="https://raw.githubusercontent.com/PleasePrompto/ductor/main/docs/images/ductor-start.jpeg" alt="ductor /start screen" width="49%" />
  <img src="https://raw.githubusercontent.com/PleasePrompto/ductor/main/docs/images/ductor-quick-actions.jpeg" alt="ductor quick action buttons" width="49%" />
</p>

## Quick start

```bash
pipx install ductor-slack    # or: uv tool install ductor-slack
ductor-slack
```

The onboarding wizard handles CLI checks, transport setup, timezone, optional Docker, and optional background service install.

**Requirements:** Python 3.11+, at least one CLI installed (`claude`, `codex`, or `gemini`), and either:

- a Telegram Bot Token from [@BotFather](https://t.me/BotFather), or
- a Matrix account on a homeserver (homeserver URL, user ID, password/access token), or
- a Slack bot token + Socket Mode app token (plus the Slack app scopes/events listed in [`docs/installation.md#slack-setup`](docs/installation.md#slack-setup))

For Matrix support: `ductor-slack install matrix` — see [Matrix setup guide](docs/matrix-setup.md).
For Slack support: `pip install "ductor-slack[slack]"`, then follow [`docs/installation.md#slack-setup`](docs/installation.md#slack-setup) and configure `slack.bot_token` + `slack.app_token`.

Detailed setup: [`docs/installation.md`](docs/installation.md)

## New in v0.16.0

- **Memory maintenance is now built in** — streaming compaction boundaries can trigger a silent memory flush, optional reflection hook, and LLM-driven `MAINMEMORY.md` compaction.
- **Telegram UX is tighter** — stage-based emoji status reactions are enabled by default, while `seen_reaction` remains available as the simpler one-shot alternative.
- **Lifecycle notifications are routable** — startup and upgrade notices can be pinned to specific chats/topics instead of always broadcasting.
- **Media transcription is extensible** — bundled media tools now accept external audio/video transcription commands via config-driven env-var hand-off.
- **Task and multi-agent automation got sharper** — background tasks support priorities, and `ask_agent_async.py` now supports `--reply-to` and `--silent` for cleaner pipelines.

Release summary: [`docs/release_notes_v0.16.0.md`](docs/release_notes_v0.16.0.md)

## How chats work

ductor gives you multiple ways to interact with your coding agents. Each level builds on the previous one.

### 1. Single chat (your main agent)

This is where everyone starts. You get a private 1:1 chat with your bot (Telegram or Matrix). Every message goes to the CLI you have active (`claude`, `codex`, or `gemini`), responses stream back in real time.

```text
You:   "Explain the auth flow in this codebase"
Bot:   [streams response from Claude Code]

You:   /model
Bot:   [interactive model/provider picker]

You:   "Now refactor the parser"
Bot:   [streams response, same session context]
```

This single chat is all you need. Everything else below is optional.

### 2. Groups with topics (multiple isolated chats)

**Telegram:** Create a group, enable topics (forum mode), and add your bot.
**Matrix:** Invite the bot to multiple rooms — each room is its own context.

Every topic (Telegram) or room (Matrix) becomes an isolated chat with its own CLI context.

```text
Group: "My Projects"
  ├── General           ← own context (isolated from your single chat)
  ├── Topic: Auth       ← own context
  ├── Topic: Frontend   ← own context
  ├── Topic: Database   ← own context
  └── Topic: Refactor   ← own context
```

That's 5 independent conversations from a single group. Your private single chat stays separate too — 6 total contexts, all running in parallel.

Each topic can use a different model. Run `/model` inside a topic to change just that topic's provider.

All chats share the same `~/.ductor-slack/` workspace — same tools, same memory, same files. The only thing isolated is the conversation context.

> **Telegram note:** The Bot API has no method to list existing forum topics.
> ductor learns topic names from `forum_topic_created` and `forum_topic_edited`
> events — pre-existing topics show as "Topic #N" until renamed.
> This is a Telegram limitation, not a ductor limitation.

### 3. Named sessions (extra contexts within any chat)

Need to work on something unrelated without losing your current context? Start a named session. It runs inside the same chat but has its own CLI conversation.

```text
You:   "Let's work on authentication"        ← main context builds up
Bot:   [responds about auth]

/session Fix the broken CSV export            ← starts session "firmowl"
Bot:   [works on CSV in separate context]

You:   "Back to auth — add rate limiting"     ← main context is still clean
Bot:   [remembers exactly where you left off]

@firmowl Also add error handling              ← follow-up to the session
```

Sessions work everywhere — in your single chat, in group topics, in sub-agent chats. Think of them as opening a second terminal window next to your current one.

### 4. Background tasks (async delegation)

Any chat can delegate long-running work to a background task. You keep chatting while the task runs autonomously. When it finishes, the result flows back into your conversation.

```text
You:   "Research the top 5 competitors and write a summary"
Bot:   → delegates to background task, you keep chatting
Bot:   → task finishes, result appears in your chat

You:   "Delegate this: generate reports for all Q4 metrics"
Bot:   → explicitly delegated, runs in background
Bot:   → task has a question? It asks the agent → agent asks you → you answer → task continues
```

Each task gets its own memory file (`TASKMEMORY.md`) and can be resumed with follow-ups.

### 5. Sub-agents (fully isolated second agent)

Sub-agents are completely separate bots — own chat, own workspace, own memory, own CLI auth, own config settings (heartbeat, timeouts, model defaults, etc.). Each sub-agent can use a different transport (e.g. main on Telegram, sub-agent on Matrix).

```bash
ductor-slack agents add codex-agent    # creates a new bot (needs its own BotFather token)
```

```text
Your main chat (Claude):        "Explain the auth flow"
codex-agent chat (Codex):       "Refactor the parser module"
```

Sub-agents live under `~/.ductor-slack/agents/<name>/` with their own workspace, tools, and memory — fully isolated from the main agent.

You can delegate tasks between agents:

```text
Main chat:  "Ask codex-agent to write tests for the API"
  → Claude sends the task to Codex
  → Codex works in its own workspace
  → Result flows back to your main chat
```

### Comparison

| | Single chat | Group topics | Named sessions | Background tasks | Sub-agents |
|---|---|---|---|---|---|
| **What it is** | Your main 1:1 chat | One topic = one chat | Extra context in any chat | "Do this while I keep working" | Separate bot, own everything |
| **Context** | One per provider | One per topic per provider | Own context per session | Own context, result flows back | Fully isolated |
| **Workspace** | `~/.ductor-slack/` | Shared with main | Shared with parent chat | Shared with parent agent | Own under `~/.ductor-slack/agents/` |
| **Config** | Main config | Shared with main | Shared with parent chat | Shared with parent agent | Own config (heartbeat, timeouts, model, ...) |
| **Setup** | Automatic | Create group + enable topics | `/session <prompt>` | Automatic or "delegate this" | Telegram: `ductor-slack agents add`; Matrix: `agents.json` / tool scripts |

### How it all fits together

```text
~/.ductor-slack/                          ← shared workspace (tools, memory, files)
  │
  ├── Single chat                   ← main agent, private 1:1
  │     ├── main context
  │     └── named sessions
  │
  ├── Group: "My Projects"          ← same agent, same workspace
  │     ├── General (own context)
  │     ├── Topic: Auth (own context, own model)
  │     ├── Topic: Frontend (own context)
  │     └── each topic can have named sessions too
  │
  └── agents/codex-agent/           ← sub-agent, fully isolated workspace
        ├── own single chat
        ├── own group support
        ├── own named sessions
        └── own background tasks
```

## Features

- **Multi-transport** — run Telegram, Matrix, and Slack simultaneously, or pick any one
- **Multi-language** — UI in English, Deutsch, Nederlands, Français, Русский, Español, Português
- **Real-time streaming** — live message edits (Telegram) or segment-based output (Matrix)
- **Provider switching** — `/model` to change provider/model (never blocks, even during active processes)
- **Persistent memory** — plain Markdown files that survive across sessions
- **Memory maintenance** — pre-compaction flush, optional reflection cadence, and LLM-driven compaction
- **Cron jobs** — in-process scheduler with timezone support, per-job overrides, result routing to originating chat
- **Webhooks** — `wake` (inject into active chat) and `cron_task` (isolated task run) modes
- **Heartbeat** — proactive checks with per-target settings, group/topic support, chat validation
- **Image processing** — auto-resize and WebP conversion for incoming images (configurable)
- **Media transcription hooks** — configurable external audio/video transcription commands for bundled media tools
- **Notification routing** — startup/upgrade lifecycle messages can target specific chats/topics
- **Task priorities** — `interactive`, `background`, and `batch` scheduling modes for background work
- **Telegram status reactions** — stage-aware emoji tracker on the user message while the agent works
- **Config hot-reload** — most settings update without restart (including language, scene, image)
- **Docker sandbox** — optional sidecar container with configurable host mounts
- **Service manager** — Linux (systemd), macOS (launchd), Windows (Task Scheduler)
- **Cross-tool skill sync** — shared skills across `~/.claude/`, `~/.codex/`, `~/.gemini/`

## Messenger support

Telegram is the primary transport — full feature set, battle-tested, zero extra dependencies.

| Messenger | Status | Streaming | Buttons | Install |
|---|---|---|---|---|
| **Telegram** | primary | Live message edits | Inline keyboards | `pip install ductor-slack` |
| **Matrix** | supported | Segment-based (new messages) | Emoji reactions | `ductor-slack install matrix` |
| **Slack** | supported | Non-streaming | Native threads | `pip install "ductor-slack[slack]"` |

Both transports can run **in parallel** on the same agent:

```json
{"transport": "telegram"}
{"transport": "matrix"}
{"transport": "slack"}
{"transports": ["telegram", "slack"]}
```

### Modular transport architecture

Each messenger is a self-contained module under `messenger/<name>/` implementing a
shared `BotProtocol`. The core (orchestrator, sessions, CLI, cron, etc.) is completely
transport-agnostic — it never knows which messenger delivered the message.

Adding a new messenger (Discord, Slack, Signal, ...) means implementing `BotProtocol`
in a new sub-package and registering it — the rest of ductor works without changes.
Guide: [`docs/modules/messenger.md`](docs/modules/messenger.md)

## Auth

### Telegram

ductor uses a dual-allowlist model. Every message must pass both checks.

| Chat type | Check |
|---|---|
| **Private** | `user_id ∈ allowed_user_ids` |
| **Group** | `group_id ∈ allowed_group_ids` AND `user_id ∈ allowed_user_ids` |

- **`allowed_user_ids`** — Telegram user IDs that may talk to the bot. At least one required.
- **`allowed_group_ids`** — Telegram group IDs where the bot may operate. Default `[]` = no groups.
- **`group_mention_only`** — When `true`, the bot only responds in groups when @mentioned or replied to.

All three are **hot-reloadable** — edit `config.json` and changes take effect within seconds.

> **Privacy Mode:** Telegram bots have Privacy Mode enabled by default and only see `/commands` in groups. To let the bot see all messages, make it a **group admin** or disable Privacy Mode via BotFather (`/setprivacy` → Disable). If changed after joining, remove and re-add the bot.

**Group management:** When the bot is added to a group not in `allowed_group_ids`, it warns and auto-leaves. Use `/where` to see tracked groups and their IDs.

**Channel allowlist:** Telegram channels are tracked separately via `allowed_channel_ids`. Unauthorized channels are announced and auto-left on join/audit just like unauthorized groups.

> **Tip — adding a group for the first time:**
> 1. Create a Telegram group, enable topics if you want isolated chats
> 2. Add the bot and make it **admin** (required for full message access)
> 3. Send a message mentioning `@your_bot` — the bot won't respond yet
> 4. In your private chat with the bot, run `/where` — you'll see the group listed under "Rejected" with its ID
> 5. Tell the bot: *"Add this as an allowed group in the config"* — it updates `config.json` for you
> 6. Run `/restart` — the bot now responds in the group

### Matrix

Matrix auth uses room and user allowlists in the `matrix` config block:

- **`allowed_rooms`** — Room IDs or aliases where the bot may operate.
- **`allowed_users`** — Matrix user IDs allowed to interact with the bot.

`group_mention_only` nuance on Matrix:

- In non-DM rooms, when `group_mention_only=true`, the bot requires @mention/reply and bypasses `allowed_users` checks for those group messages.
- Room-level filtering (`allowed_rooms`) still applies.

The bot logs in with password on first start, then persists `access_token` and `device_id` for subsequent runs. E2EE is supported via `matrix-nio[e2e]`.

### Slack

Slack runs through **Socket Mode**, so ductor does not need a public webhook URL.

Create a Slack app, then configure these permissions before installing it to your workspace. If you prefer Slack's **manifest import** flow, use [`docs/slack-app-manifest.example.yaml`](docs/slack-app-manifest.example.yaml) as a starting template.

**Bot token scopes**

| Scope | Why ductor needs it |
|---|---|
| `chat:write` | send replies as the bot |
| `reactions:write` | add/remove the `:eyes:` receipt reaction while a turn is processing |
| `app_mentions:read` | detect `@bot` in channels |
| `channels:history` | read public-channel messages and thread history |
| `channels:read` | resolve public channel metadata |
| `groups:history` | read private-channel messages and thread history |
| `im:history` | read direct messages |
| `im:read` | access DM metadata |
| `im:write` | open/manage DMs |
| `users:read` | resolve user display names for thread backfill/context |
| `files:read` | download attached files |
| `files:write` | upload generated files |

**Optional bot token scope**

| Scope | When to add it |
|---|---|
| `groups:read` | if you want private-channel metadata lookups beyond history access |

If you skip `reactions:write`, the bot still replies normally, but you will not see the Slack `:eyes:` acknowledgement reaction.

**App-level token scope**

| Scope | Why ductor needs it |
|---|---|
| `connections:write` | required for Socket Mode (`xapp-...`) |

**Event subscriptions**

| Event | Required | Purpose |
|---|---|---|
| `message.im` | yes | direct messages |
| `message.channels` | yes | public-channel messages |
| `message.groups` | recommended | private-channel messages |
| `app_mention` | yes | mention handling in channels |

Also enable **App Home → Messages Tab** so users can DM the bot, then **Install App to Workspace** and copy:

- **Bot User OAuth Token** → `slack.bot_token` (`xoxb-...`)
- **App-Level Token** → `slack.app_token` (`xapp-...`)

If you change scopes or subscribed events later, **reinstall the Slack app** so the new permissions take effect.

ductor's Slack allowlist lives in the `slack` config block:

- **`allowed_users`** — Slack member IDs allowed to use the bot
- **`allowed_channels`** — Slack channel IDs where the bot may respond
- **`group_mention_only`** — when `true`, channel conversations start on `@bot` and continue in the activated thread

After setup, invite the app into each target channel. Full step-by-step setup is in [`docs/installation.md#slack-setup`](docs/installation.md#slack-setup).

## Language

ductor's UI (commands, status messages, onboarding) is available in multiple languages:

| Code | Language |
|---|---|
| `en` | English (default) |
| `de` | Deutsch |
| `nl` | Nederlands |
| `fr` | Français |
| `ru` | Русский |
| `es` | Español |
| `pt` | Português |

Set the language in `config.json`:

```json
{"language": "de"}
```

This is **hot-reloadable** — change the language without restarting the bot.

## Commands

| Command | Description |
|---|---|
| `/model` | Interactive model/provider selector |
| `/new` | Reset the configured default-provider session for this chat/topic |
| `/stop` | Stop current message and discard queued messages |
| `/interrupt` | Interrupt current message, queued messages continue |
| `/stop_all` | Kill everything — all messages, sessions, tasks, all agents |
| `/status` | Session/provider/auth status |
| `/memory` | Show persistent memory |
| `/session <prompt>` | Start a named background session |
| `/sessions` | View/manage active sessions |
| `/tasks` | View/manage background tasks |
| `/cron` | Interactive cron management |
| `/showfiles` | Browse `~/.ductor-slack/` |
| `/diagnose` | Runtime diagnostics |
| `/upgrade` | Check/apply updates |
| `/agents` | Multi-agent status |
| `/agent_commands` | Multi-agent command reference |
| `/where` | Show tracked chats/groups |
| `/leave <id>` | Manually leave a group |
| `/info` | Version + links |

`/new` is intentionally a factory reset for the current `SessionKey`: it clears the bucket tied to the configured default model/provider for that chat or topic, not whichever provider you last switched to temporarily via `/model`.

On Slack, these same commands also work as normal message commands (for example `help`, `status`, or `model`) even though ductor does not register native Slack slash commands.

## Common CLI commands

```bash
ductor-slack            # Start bot (auto-onboarding if needed)
ductor-slack onboarding       # Re-run setup wizard
ductor-slack reset            # Full reset + onboarding
ductor-slack stop             # Stop bot
ductor-slack restart          # Restart bot
ductor-slack upgrade          # Upgrade and restart
ductor-slack status           # Runtime status
ductor-slack help             # CLI overview
ductor-slack uninstall        # Remove bot + workspace

ductor-slack service install  # Install as background service
ductor-slack service status   # Show service status
ductor-slack service start    # Start service
ductor-slack service stop     # Stop service
ductor-slack service logs     # View service logs
ductor-slack service uninstall

ductor-slack docker enable    # Enable Docker sandbox
ductor-slack docker rebuild   # Rebuild sandbox container
ductor-slack docker mount /p  # Add host mount
ductor-slack docker extras    # List optional sandbox packages

ductor-slack agents list      # List configured sub-agents
ductor-slack agents add NAME  # Add a sub-agent
ductor-slack agents remove NAME

ductor-slack api enable       # Enable WebSocket API (beta)
ductor-slack api disable      # Disable WebSocket API

ductor-slack install matrix   # Install Matrix transport extra
ductor-slack install api      # Install API/PyNaCl extra
```

`ductor-slack agents add` currently scaffolds Telegram sub-agents interactively. Matrix
sub-agents are supported at runtime, but you configure them via `agents.json` or
the bundled agent tool scripts.

## Workspace layout

```text
~/.ductor-slack/
  config/config.json                 # Bot configuration
  sessions.json                      # Chat session state
  named_sessions.json                # Named background sessions
  tasks.json                         # Background task registry
  cron_jobs.json                     # Scheduled tasks
  webhooks.json                      # Webhook definitions
  agents.json                        # Sub-agent registry (optional)
  SHAREDMEMORY.md                    # Shared knowledge across all agents
  CLAUDE.md / AGENTS.md / GEMINI.md  # Rule files
  logs/agent.log
  workspace/
    memory_system/MAINMEMORY.md      # Persistent memory
    cron_tasks/ skills/ tools/       # Scripts and tools
    tasks/                           # Per-task folders
    telegram_files/ matrix_files/    # Media files (per transport)
    api_files/                       # Uploaded/downloadable API files
    output_to_user/                  # Generated deliverables
  agents/<name>/                     # Sub-agent workspaces (isolated)
```

Full config reference: [`docs/config.md`](docs/config.md) — full example with all options: [`config.example.json`](config.example.json)

## Documentation

| Doc | Content |
|---|---|
| [System Overview](docs/system_overview.md) | End-to-end runtime overview |
| [Developer Quickstart](docs/developer_quickstart.md) | Quickest path for contributors |
| [Architecture](docs/architecture.md) | Startup, routing, streaming, callbacks |
| [Configuration](docs/config.md) | Config schema and merge behavior |
| [Release Notes v0.16.0](docs/release_notes_v0.16.0.md) | Change summary since `v0.15.0` |
| [Matrix Setup](docs/matrix-setup.md) | Adding Matrix as transport |
| [Automation](docs/automation.md) | Cron, webhooks, heartbeat setup |
| [Service Management](docs/modules/service_management.md) | systemd, launchd, Task Scheduler backends |
| [Module docs](docs/modules/) | Per-module deep dives |

## Why ductor?

Other projects manipulate SDKs or patch CLIs and risk violating provider terms of service. ductor simply runs the official CLI binaries as subprocesses — nothing more.

- Official CLIs only (`claude`, `codex`, `gemini`)
- Rule files are plain Markdown (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`)
- Memory is one Markdown file per agent
- All state is JSON — no database, no external services

## Disclaimer

ductor runs official provider CLIs and does not impersonate provider clients. Validate your own compliance requirements before unattended automation.

- [Anthropic Terms](https://www.anthropic.com/policies/terms)
- [OpenAI Terms](https://openai.com/policies/terms-of-use)
- [Google Terms](https://policies.google.com/terms)

## Contributing

```bash
git clone https://github.com/PleasePrompto/ductor.git
cd ductor
uv sync --extra dev
```

Run checks with [just](https://github.com/casey/just):

```bash
just check   # linters + type checks (parallel)
just test    # test suite
just fix     # auto-fix formatting and lint issues
```

Or directly with uv:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy ductor_slack
```

Zero warnings, zero errors.

## License

[MIT](LICENSE)
