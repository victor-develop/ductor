---
name: responsive-agent
description: Pause a task to ask a human in Slack, then resume with full saved context when they reply. Use when a task is blocked on info you don't have. Also activates automatically whenever the conversation context contains an anchor of the form `[ret-ref:XXXXXX]` — that anchor means a prior session of this agent paused work and the current Slack reply is the answer; the skill resumes the paused task by loading the saved context.
---

# Responsive Agent

Turn the agent into a reactive system: pause mid-task to ask a specific person
in Slack, exit the session, and resume cleanly when that person replies.

The mechanism relies on:

- A short reference id `XXXXXX` (6 hex chars) saved to disk with the task
  context.
- An anchor `[ret-ref:XXXXXX]` posted as the last line of the Slack ask message.
- When the human replies, the Slack thread context is fed into a fresh agent
  session. The anchor lives in that context, so this skill triggers, loads the
  saved task, and continues from where the previous session left off.

## When to activate

Two distinct entry points:

1. **Pause** — Mid-task you need information from a specific human before you
   can proceed. You decide who to ask and what to ask.
2. **Resume** — The conversation context (usually the thread parent) contains a
   line matching `[ret-ref:XXXXXX]`. The Slack message you are now reading IS
   the human's answer to a prior session's question. Pick up the saved task.

If the conversation contains an anchor, always resume first — do not start
fresh work.

## Pause flow

When you need to ask a human:

1. Pick the Slack channel id and the user to mention. Mentioning needs the
   Slack user id (`U...` or `W...`) — handles like `@victor` do not notify.
2. Write a one- or two-line question for the human. Be specific.
3. Write the resume context for your future self — this is everything the
   next agent session needs to continue the work without seeing this
   conversation.
4. Run:

   ```bash
   python3 ~/.ductor-slack/workspace/skills/responsive-agent/scripts/pause_and_ask.py \
     --channel C0XXXXXX \
     --mention U0XXXXXX \
     --question "<short, specific question>" \
     --requester-channel "$ORIG_CHANNEL" \
     --requester-thread-ts "$ORIG_THREAD_TS" \
     --context-stdin <<'EOF'
   ## Task
   <what we are doing and why>

   ## Progress so far
   <what has been done, with file paths / commit refs / search results>

   ## What I need from the human
   <exactly what info will unblock me>

   ## How to resume once answered
   <step-by-step continuation plan: which scripts to run, which files to edit,
   how to report the final result back to the original requester>
   EOF
   ```

   The script automatically appends two things to the Slack message — do not
   type them yourself:
   - A short reply hint telling the recipient to `@`-mention this bot when
     replying. Without that mention Slack will not deliver their reply to the
     bot, so the paused task can never resume.
   - The `[ret-ref:<ref>]` anchor on its own line.

   You can override the hint with `--reply-hint "<text>"` (use `{bot}` as a
   placeholder for the bot mention, e.g. `--reply-hint "回复时请 @ {bot} 才能继续"`)
   or pass `--reply-hint ""` to disable it. Default hint is in English.

5. The script prints `{"ok": true, "ref": "abc123", "channel": "...", "ts": "..."}`.
6. Briefly tell the user you posted the question to `<@U...>` in `#channel`
   and that you will resume when they reply. Then stop. Do not poll, sleep, or
   wait — the session ends naturally and the next reply will re-trigger you.

`--requester-channel` / `--requester-thread-ts` are optional but recommended:
they let the resume session post the final answer back to the original
requester's thread.

## Resume flow

When this skill triggers because the context contains `[ret-ref:XXXXXX]`:

1. Run:

   ```bash
   python3 ~/.ductor-slack/workspace/skills/responsive-agent/scripts/resume.py XXXXXX
   ```

   The script prints the saved task context followed by a JSON `meta` line
   with the original requester pointers and the ask message ts.

   If the saved context contains a scope anchor that another skill cares
   about (currently `[stask-exec:<id>]` for super-tasker), `resume.py`
   re-emits it on the very first output line so the resumed agent re-enters
   the right skill mode without having to scan the body first.

2. Treat the latest Slack message in the current conversation context (the
   human's reply) as the answer to the question that was asked.
3. Continue the task following the "How to resume" section of the saved
   context. You can also re-enter the pause flow if you need to ask someone
   else.
4. When the task is complete, decide per scenario where to reply:
   - Whether to send any follow-up in the new thread (where the human just
     answered) — and what to say there.
   - Whether to post the final result back to the original requester's thread
     using `requester_channel` / `requester_thread_ts` from meta — and what to
     say there.
   Either, both, or neither may be appropriate. Judge by what actually serves
   the requester and the human who answered: e.g. thank/acknowledge the
   answerer in-thread when warranted, deliver the deliverable to the original
   requester when they are waiting, skip a channel when a message there would
   be noise. Use `~/.ductor-slack/workspace/tools/user_tools/slack_send.py` to
   send.
5. Once you have delivered the result, clean up:

   ```bash
   python3 ~/.ductor-slack/workspace/skills/responsive-agent/scripts/cleanup.py XXXXXX
   ```

## Robustness notes

- Bot identity lookup is cached at `responsive_state/.bot_identity.json` after
  the first successful `auth.test` call. If the bot token changes, delete this
  file so the next run re-resolves the bot user id.
- The anchor may appear multiple times in the conversation (someone may quote
  or copy it). All duplicates resolve to the same ref — pick any.
- The anchor must be on its own line, but it does not have to be the only
  line in the message. If `resume.py` is given stdin instead of an arg, it
  scans the input for the first `[ret-ref:XXXXXX]` match.
- Refs are 6 lowercase hex chars. Collisions are astronomically unlikely for
  the active set, and the script refuses to overwrite an existing ref.
- State lives under `~/.ductor-slack/workspace/responsive_state/<ref>/`. It is
  not auto-expired — clean up after successful resume, or run `cleanup.py`
  with `--older-than 7d` to prune stale state.

## Storage layout

```
workspace/responsive_state/
└── <ref>/
    ├── context.md     # task context to reload on resume
    └── meta.json      # channel, mention, ask_ts, requester_*, created_at
```
