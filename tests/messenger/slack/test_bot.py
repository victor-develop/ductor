from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from ductor_bot.config import AgentConfig
from ductor_bot.messenger.slack.bot import SlackBot
from ductor_bot.session.manager import SessionData


def _make_bot() -> SlackBot:
    bot = object.__new__(SlackBot)
    bot._config = AgentConfig(
        transport="slack",
        slack={
            "bot_token": "xoxb-test",
            "app_token": "xapp-test",
            "allowed_channels": ["C123"],
            "allowed_users": ["U123"],
        },
        group_mention_only=True,
    )
    bot._agent_name = "main"
    bot._app = SimpleNamespace(client=AsyncMock())
    bot._lock_pool = MagicMock()
    bot._bus = MagicMock()
    bot._id_map = MagicMock()
    bot._id_map.channel_to_int.return_value = 11
    bot._id_map.thread_to_int.return_value = 22
    bot._orchestrator = MagicMock()
    bot._orchestrator._sessions.list_active_for_chat = AsyncMock(return_value=[])
    bot._startup_hooks = []
    bot._bot_user_id = "B123"
    bot._bot_name = "ductor"
    bot._last_active_channel = None
    bot._mentioned_threads = set()
    bot._sent_messages = set()
    bot._user_name_cache = {}
    bot._thread_context_cache = {}
    bot._THREAD_CACHE_TTL = 60.0
    bot._dispatch_with_lock = AsyncMock()
    bot._handle_command = AsyncMock()
    return bot


class TestThreadSessionLookup:
    async def test_detects_active_thread_session(self) -> None:
        bot = _make_bot()
        active = SessionData(chat_id=11, transport="sl", topic_id=22, provider_sessions={})
        active.session_id = "sid-1"
        bot._orchestrator._sessions.list_active_for_chat.return_value = [active]

        result = await bot._has_active_session_for_thread("C123", "1710000000.123")

        assert result is True


class TestThreadContextFetching:
    async def test_fetches_prior_thread_messages_once(self) -> None:
        bot = _make_bot()
        bot._app.client.conversations_replies.return_value = {
            "messages": [
                {"ts": "1710000000.100", "user": "U111", "text": "First context"},
                {"ts": "1710000000.123", "user": "U222", "text": "<@B123> Parent message"},
                {"ts": "1710000000.200", "bot_id": "BOT", "text": "Bot output"},
                {"ts": "1710000000.300", "user": "U333", "text": "Current message"},
            ]
        }
        bot._resolve_user_name = AsyncMock(side_effect=["Alice", "Bob"])

        content = await bot._fetch_thread_context(
            channel_id="C123",
            thread_ts="1710000000.123",
            current_ts="1710000000.300",
        )

        assert "Alice: First context" in content
        assert "[thread parent] Bob: Parent message" in content
        assert "Current message" not in content
        assert "Bot output" not in content

        again = await bot._fetch_thread_context(
            channel_id="C123",
            thread_ts="1710000000.123",
            current_ts="1710000000.300",
        )
        assert again == content
        bot._app.client.conversations_replies.assert_awaited_once()


class TestMessageRouting:
    async def test_backfills_first_thread_reply_after_mention(self) -> None:
        bot = _make_bot()
        bot._fetch_thread_context = AsyncMock(return_value="[ctx]\n")

        await bot._on_message(
            {
                "user": "U123",
                "channel": "C123",
                "channel_type": "channel",
                "thread_ts": "1710000000.123",
                "ts": "1710000000.456",
                "text": "<@B123> help here",
            }
        )

        bot._dispatch_with_lock.assert_awaited_once()
        assert bot._dispatch_with_lock.await_args.args[1] == "[ctx]\nhelp here"
        assert ("C123", "1710000000.123") in bot._mentioned_threads

    async def test_existing_thread_session_routes_without_mention(self) -> None:
        bot = _make_bot()
        active = SessionData(chat_id=11, transport="sl", topic_id=22, provider_sessions={})
        active.session_id = "sid-1"
        bot._orchestrator._sessions.list_active_for_chat.return_value = [active]
        bot._fetch_thread_context = AsyncMock()

        await bot._on_message(
            {
                "user": "U123",
                "channel": "C123",
                "channel_type": "channel",
                "thread_ts": "1710000000.123",
                "ts": "1710000000.456",
                "text": "follow-up without mention",
            }
        )

        bot._dispatch_with_lock.assert_awaited_once()
        bot._fetch_thread_context.assert_not_awaited()
