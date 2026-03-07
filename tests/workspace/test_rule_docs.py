"""Tests for agent-facing rule docs that are consumed by Gemini memory imports."""

from __future__ import annotations

from pathlib import Path


def test_agent_tools_rules_escape_botfather_handle() -> None:
    rules_path = (
        Path(__file__).resolve().parents[2]
        / "ductor_bot"
        / "_home_defaults"
        / "workspace"
        / "tools"
        / "agent_tools"
        / "RULES.md"
    )

    content = rules_path.read_text(encoding="utf-8")

    assert "Telegram BotFather" in content
    assert "@BotFather" not in content
