from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from ductor_bot.messenger.slack.sender import (
    SlackSendOpts,
    _split_text,
    add_reaction,
    remove_reaction,
    send_rich,
    update_message,
)


class TestSendRich:
    async def test_sends_text_message(self) -> None:
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "1.0"}

        result = await send_rich(client, "C123", "Hello **world**")

        assert result == "1.0"
        client.chat_postMessage.assert_awaited_once()
        assert client.chat_postMessage.call_args.kwargs["channel"] == "C123"
        assert client.chat_postMessage.call_args.kwargs["text"] == "Hello *world*"

    async def test_sends_tagged_file(self, tmp_path: Path) -> None:
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "1.0"}
        client.files_upload_v2.return_value = {"ok": True}
        file_path = tmp_path / "out.txt"
        file_path.write_text("hello", encoding="utf-8")

        await send_rich(
            client,
            "C123",
            f"See this <file:{file_path}>",
            SlackSendOpts(allowed_roots=[tmp_path]),
        )

        client.files_upload_v2.assert_awaited_once()
        assert client.files_upload_v2.call_args.kwargs["channel"] == "C123"

    async def test_updates_existing_message(self) -> None:
        client = AsyncMock()

        await update_message(client, "C123", "1.0", "Hello **world**", thread_ts="1.0")

        client.chat_update.assert_awaited_once()
        assert client.chat_update.call_args.kwargs["text"] == "Hello *world*"

    async def test_adds_and_removes_reaction(self) -> None:
        client = AsyncMock()

        await add_reaction(client, "C123", "1.0", "eyes")
        await remove_reaction(client, "C123", "1.0", "eyes")

        client.reactions_add.assert_awaited_once_with(channel="C123", timestamp="1.0", name="eyes")
        client.reactions_remove.assert_awaited_once_with(
            channel="C123", timestamp="1.0", name="eyes"
        )


def test_split_text_splits_long_messages() -> None:
    chunks = _split_text("x" * 40_500)
    assert len(chunks) == 2
