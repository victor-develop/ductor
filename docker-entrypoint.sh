#!/usr/bin/env sh
set -eu

mkdir -p \
  "$HOME/.ductor-slack" \
  "$HOME/.claude" \
  "$HOME/.codex" \
  "$HOME/.gemini" \
  "$HOME/.cache/claude-cli-nodejs" \
  "$HOME/.config"

exec "$@"
