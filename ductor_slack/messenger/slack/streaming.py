"""Native chat stream support for Slack with graceful fallback."""

from __future__ import annotations

import logging
from typing import Any

from ductor_slack.messenger.slack.sender import SlackSendOpts, send_rich
from ductor_slack.text.response_format import normalize_tool_name

logger = logging.getLogger(__name__)

_STREAM_BUFFER_SIZE = 64
_SYSTEM_LABELS: dict[str, str] = {
    "thinking": "Thinking",
    "compacting": "Compacting",
    "recovering": "Recovering",
    "timeout_warning": "Timeout approaching",
    "timeout_extended": "Timeout extended",
}


class SlackStreamEditor:
    """Render Slack output through the native chat streaming APIs."""

    def __init__(  # noqa: PLR0913
        self,
        client: Any,
        channel_id: str,
        *,
        thread_ts: str,
        recipient_user_id: str | None = None,
        recipient_team_id: str | None = None,
        edit_interval_seconds: float = 1.0,
    ) -> None:
        del edit_interval_seconds
        self._client = client
        self._channel_id = channel_id
        self._thread_ts = thread_ts
        self._recipient_user_id = recipient_user_id
        self._recipient_team_id = recipient_team_id
        self._stream: Any | None = None
        self._native_failed = False
        self._thinking_parts: list[str] = []
        self._answer_parts: list[str] = []
        self._status: str | None = None
        self._thinking_started = False
        self._answer_started = False
        self._tool_counter = 0
        self._active_task_id: str | None = None
        self._active_task_title: str | None = None

    async def on_thinking(self, text: str) -> None:
        """Append streamed reasoning text."""
        if not text.strip():
            return
        self._thinking_parts.append(text)
        self._status = "thinking"
        prefix = "💭 *Thinking*\n" if not self._thinking_started else ""
        self._thinking_started = True
        await self._append_markdown(prefix + text)

    async def on_delta(self, text: str) -> None:
        """Append assistant answer text."""
        if not text:
            return
        await self._complete_active_task()
        self._answer_parts.append(text)
        prefix = ""
        if not self._answer_started:
            prefix = "\n\n" if self._thinking_started else ""
            self._answer_started = True
        await self._append_markdown(prefix + text)

    async def on_tool(self, tool_name: str) -> None:
        """Show tool activity using Slack's task timeline."""
        label = normalize_tool_name(tool_name)
        self._thinking_parts.append(f"\n[TOOL: {label}]\n")
        await self._complete_active_task()
        task_id = f"tool_{self._tool_counter}"
        self._tool_counter += 1
        self._active_task_id = task_id
        self._active_task_title = f"Running {label}"
        await self._append_chunks(
            [
                {
                    "type": "task_update",
                    "id": task_id,
                    "title": self._active_task_title,
                    "status": "in_progress",
                }
            ]
        )

    async def on_system(self, status: str | None) -> None:
        """Track transient system status."""
        self._status = status
        if status is None or status == "thinking":
            return
        label = _SYSTEM_LABELS.get(status)
        if label:
            await self._append_markdown(f"\n\n_{label}_")

    async def finalize(self, final_text: str | None) -> None:
        """Finalize the stream, falling back to a single rich message if needed."""
        if final_text and not "".join(self._answer_parts).strip():
            self._answer_parts = [final_text]
        if self._native_failed:
            rendered = self._render_fallback(final_text)
            if rendered:
                await send_rich(
                    self._client,
                    self._channel_id,
                    rendered,
                    SlackSendOpts(thread_ts=self._thread_ts),
                )
            return

        await self._complete_active_task()
        stream = await self._ensure_stream()
        stop_text = None
        if final_text and not self._answer_started:
            stop_text = final_text
        await stream.stop(markdown_text=stop_text)

    async def _append_markdown(self, text: str) -> None:
        if not text or self._native_failed:
            return
        try:
            stream = await self._ensure_stream()
            await stream.append(markdown_text=text)
        except Exception as exc:
            self._mark_native_failure(exc)

    async def _append_chunks(self, chunks: list[dict[str, object]]) -> None:
        if not chunks or self._native_failed:
            return
        try:
            stream = await self._ensure_stream()
            await stream.append(chunks=chunks)
        except Exception as exc:
            self._mark_native_failure(exc)

    async def _complete_active_task(self) -> None:
        if self._active_task_id is None or self._active_task_title is None:
            return
        task_id = self._active_task_id
        title = self._active_task_title
        self._active_task_id = None
        self._active_task_title = None
        await self._append_chunks(
            [
                {
                    "type": "task_update",
                    "id": task_id,
                    "title": title,
                    "status": "complete",
                }
            ]
        )

    async def _ensure_stream(self) -> Any:
        if self._stream is not None:
            return self._stream
        kwargs: dict[str, object] = {
            "channel": self._channel_id,
            "thread_ts": self._thread_ts,
            "task_display_mode": "timeline",
            "buffer_size": _STREAM_BUFFER_SIZE,
        }
        if self._recipient_team_id is not None:
            kwargs["recipient_team_id"] = self._recipient_team_id
        if self._recipient_user_id is not None:
            kwargs["recipient_user_id"] = self._recipient_user_id
        self._stream = await self._client.chat_stream(**kwargs)
        return self._stream

    def _mark_native_failure(self, exc: Exception) -> None:
        if self._native_failed:
            return
        self._native_failed = True
        logger.warning("Slack native stream failed; falling back to plain reply: %r", exc)

    def _render_fallback(self, final_text: str | None) -> str:
        sections: list[str] = []
        thinking = "".join(self._thinking_parts).strip()
        answer = "".join(self._answer_parts).strip() or (final_text or "").strip()

        if thinking:
            sections.append(f"💭 *Thinking*\n{thinking}")
        elif self._status is not None:
            label = _SYSTEM_LABELS.get(self._status or "")
            if label:
                sections.append(f"💭 *{label}*")

        if answer:
            sections.append(answer)

        if not sections:
            sections.append("…")
        return "\n\n".join(sections).strip()
