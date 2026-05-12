# Opt-in skills

Skills in this directory are **not** auto-installed by ductor — they are
deliberately kept out of `ductor_slack/_home_defaults/` so a fresh ductor
install does not pick them up by the skill-sync mechanism.

To install one into a running ductor instance, copy (or symlink) the skill
directory into the live workspace skills root:

```bash
cp -r skills/<name> ~/.ductor-slack/workspace/skills/<name>
```

The 30-second skill sync will then propagate it to `~/.claude/skills/` and
`$CODEX_HOME/skills/` per the rules in
`ductor_slack/_home_defaults/workspace/skills/RULES.md`.

## What's here

- `responsive-agent/` — pause-and-ask / resume protocol used by
  super-tasker and standalone for human-in-the-loop continuation.
- `super-tasker/` — file-backed multi-track task orchestration. Depends on
  `responsive-agent`; install both together.
