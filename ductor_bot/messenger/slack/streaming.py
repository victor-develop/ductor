"""Edit-in-place streaming support for Slack."""

from __future__ import annotations

import time

from ductor_bot.messenger.slack.sender import SlackSendOpts, send_rich, update_message
from ductor_bot.text.response_format import normalize_tool_name

_SYSTEM_LABELS: dict[str, str] = {
    "thinking": "Thinking",
    "compacting": "Compacting",
    "recovering": "Recovering",
    "timeout_warning": "Timeout approaching",
    "timeout_extended": "Timeout extended",
}


class SlackStreamEditor:
    """Maintain one editable Slack message for streaming output."""

    def __init__(
        self,
        client: object,
        channel_id: str,
        *,
        thread_ts: str | None = None,
        edit_interval_seconds: float = 1.0,
    ) -> None:
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._edit_interval_seconds = max(0.0, edit_interval_seconds)
        self._message_ts: str | None = None
        self._thinking_parts: list[str] = []
        self._answer_parts: list[str] = []
        self._status: str | None = None
        self._last_rendered = ""
        self._last_edit_at = 0.0

    async def on_thinking(self, text: str) -> None:
        """Append streamed reasoning text."""
        if not text.strip():
            return
        self._thinking_parts.append(text)
        self._status = "thinking"
        await self._flush_if_due()

    async def on_delta(self, text: str) -> None:
        """Append assistant answer text."""
        if not text:
            return
        self._answer_parts.append(text)
        await self._flush_if_due()

    async def on_tool(self, tool_name: str) -> None:
        """Show tool activity inline in the thinking section."""
        label = normalize_tool_name(tool_name)
        self._thinking_parts.append(f"\n[TOOL: {label}]\n")
        await self._flush_if_due()

    async def on_system(self, status: str | None) -> None:
        """Track transient system status."""
        self._status = status
        await self._flush_if_due()

    async def finalize(self, final_text: str | None) -> None:
        """Flush the final text, falling back to orchestrator result when needed."""
        if final_text and not "".join(self._answer_parts).strip():
            self._answer_parts = [final_text]
        await self._flush(force=True, final=True)

    async def _flush_if_due(self) -> None:
        now = time.monotonic()
        if self._message_ts is None or now - self._last_edit_at >= self._edit_interval_seconds:
            await self._flush(force=False, final=False)

    async def _flush(self, *, force: bool, final: bool) -> None:
        rendered = self._render(final=final)
        if not rendered:
            return
        if not force and rendered == self._last_rendered:
            return

        if self._message_ts is None:
            self._message_ts = await send_rich(
                self._client,
                self._channel_id,
                rendered,
                SlackSendOpts(thread_ts=self._thread_ts),
            )
        elif rendered != self._last_rendered:
            await update_message(
                self._client,
                self._channel_id,
                self._message_ts,
                rendered,
                thread_ts=self._thread_ts,
            )

        self._last_rendered = rendered
        self._last_edit_at = time.monotonic()

    def _render(self, *, final: bool) -> str:
        sections: list[str] = []
        thinking = "".join(self._thinking_parts).strip()
        answer = "".join(self._answer_parts).strip()

        if thinking:
            sections.append(f"💭 *Thinking*\n{thinking}")
        elif not final:
            label = _SYSTEM_LABELS.get(self._status or "")
            if label:
                sections.append(f"💭 *{label}*")

        if answer:
            sections.append(answer)

        if not sections:
            sections.append("…")

        rendered = "\n\n".join(sections).strip()
        if not final:
            rendered = f"{rendered}\n\n_▉_"
        return rendered
