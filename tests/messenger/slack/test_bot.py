from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ductor_slack.config import AgentConfig
from ductor_slack.messenger.slack.bot import SlackBot, _ThreadContextCache
from ductor_slack.orchestrator.registry import OrchestratorResult
from ductor_slack.session.manager import SessionData


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
    bot._mentioned_threads = {}
    bot._user_name_cache = {}
    bot._thread_context_cache = {}
    bot._MENTIONED_THREAD_TTL = 3600.0
    bot._MENTIONED_THREAD_MAX_SIZE = 200
    bot._THREAD_CACHE_TTL = 60.0
    bot._THREAD_CONTEXT_CACHE_MAX_SIZE = 200
    bot._dispatch_with_lock = AsyncMock()
    bot._handle_command = AsyncMock()
    bot._send_rich = AsyncMock()
    return bot


class TestThreadSessionLookup:
    async def test_detects_active_thread_session(self) -> None:
        bot = _make_bot()
        active = SessionData(chat_id=11, transport="sl", topic_id=22, provider_sessions={})
        active.session_id = "sid-1"
        bot._orchestrator._sessions.list_active_for_chat.return_value = [active]

        result = await bot._has_active_session_for_thread("C123", "1710000000.123")

        assert result is True


class TestMentionedThreadCache:
    def test_prunes_expired_entries(self) -> None:
        bot = _make_bot()
        now = time.monotonic()
        bot._MENTIONED_THREAD_TTL = 10.0
        bot._mentioned_threads = {
            ("C123", "old"): now - 20.0,
            ("C123", "fresh"): now - 1.0,
        }

        with patch("ductor_slack.messenger.slack.bot.time") as mock_time:
            mock_time.monotonic.return_value = now
            bot._mark_mentioned_thread("C123", "new")

        assert ("C123", "old") not in bot._mentioned_threads
        assert ("C123", "fresh") in bot._mentioned_threads
        assert ("C123", "new") in bot._mentioned_threads

    def test_enforces_max_size(self) -> None:
        bot = _make_bot()
        bot._MENTIONED_THREAD_MAX_SIZE = 2
        now = time.monotonic()

        with patch("ductor_slack.messenger.slack.bot.time") as mock_time:
            mock_time.monotonic.return_value = now
            bot._mark_mentioned_thread("C123", "one")
            mock_time.monotonic.return_value = now + 1.0
            bot._mark_mentioned_thread("C123", "two")
            mock_time.monotonic.return_value = now + 2.0
            bot._mark_mentioned_thread("C123", "three")

        assert list(bot._mentioned_threads) == [("C123", "two"), ("C123", "three")]


class TestThreadContextFetching:
    async def test_fetches_prior_thread_messages_once(self) -> None:
        bot = _make_bot()
        bot._app.client.conversations_replies.return_value = {
            "messages": [
                {"ts": "1710000000.100", "user": "U111", "text": "First context"},
                {"ts": "1710000000.123", "user": "U222", "text": "<@B123> Parent message"},
                {"ts": "1710000000.200", "bot_id": "BOT", "text": "Bot output"},
                {"ts": "1710000000.300", "user": "U333", "text": "Current message"},
                {"ts": "1710000000.301", "user": "U444", "text": "Future message"},
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
        assert "Future message" not in content
        assert "Bot output" not in content

        again = await bot._fetch_thread_context(
            channel_id="C123",
            thread_ts="1710000000.123",
            current_ts="1710000000.300",
        )
        assert again == content
        bot._app.client.conversations_replies.assert_awaited_once()

    def test_prunes_expired_cached_context_entries(self) -> None:
        bot = _make_bot()
        now = time.monotonic()
        bot._THREAD_CACHE_TTL = 10.0
        bot._thread_context_cache = {
            "expired": _ThreadContextCache(content="old", fetched_at=now - 20.0),
            "fresh": _ThreadContextCache(content="keep", fetched_at=now - 1.0),
        }

        bot._cache_thread_context(cache_key="new", content_parts=["Alice: hi"], fetched_at=now)

        assert "expired" not in bot._thread_context_cache
        assert "fresh" in bot._thread_context_cache
        assert "new" in bot._thread_context_cache

    def test_enforces_thread_context_cache_max_size(self) -> None:
        bot = _make_bot()
        bot._THREAD_CONTEXT_CACHE_MAX_SIZE = 2
        now = time.monotonic()

        bot._cache_thread_context(cache_key="one", content_parts=["a"], fetched_at=now)
        bot._cache_thread_context(cache_key="two", content_parts=["b"], fetched_at=now + 1.0)
        bot._cache_thread_context(cache_key="three", content_parts=["c"], fetched_at=now + 2.0)

        assert list(bot._thread_context_cache) == ["two", "three"]


class TestCommandPresentation:
    async def test_info_uses_slack_i18n_description(self) -> None:
        bot = _make_bot()

        await bot._cmd_info(text="/info", channel_id="C123", key=MagicMock(), thread_ts=None)

        sent_text = bot._send_rich.await_args.args[1]
        assert "Slack" in sent_text
        assert "Matrix" not in sent_text

    def test_help_uses_slack_footer(self) -> None:
        bot = _make_bot()

        help_text = bot._build_help_text()

        assert "`help` or `/help`" in help_text
        assert "`!`" not in help_text


class TestMessageRouting:
    async def test_on_message_wraps_processing_reaction(self) -> None:
        bot = _make_bot()
        bot._add_processing_reaction = AsyncMock(return_value=True)
        bot._remove_processing_reaction = AsyncMock()

        await bot._on_message(
            {
                "user": "U123",
                "channel": "C123",
                "channel_type": "im",
                "ts": "1710000000.456",
                "text": "hello",
            }
        )

        bot._add_processing_reaction.assert_awaited_once_with("C123", "1710000000.456")
        bot._remove_processing_reaction.assert_awaited_once_with("C123", "1710000000.456")

    async def test_app_mention_event_routes_like_message(self) -> None:
        bot = _make_bot()
        bot._on_message = AsyncMock()

        event = {
            "user": "U123",
            "channel": "C123",
            "channel_type": "channel",
            "ts": "1710000000.456",
            "text": "<@B123> status",
        }

        await bot._handle_mention_event(event, object())

        bot._on_message.assert_awaited_once_with(event)

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

    async def test_routes_bare_message_command_without_leading_slash(self) -> None:
        bot = _make_bot()

        await bot._on_message(
            {
                "user": "U123",
                "channel": "C123",
                "channel_type": "im",
                "ts": "1710000000.456",
                "text": "status",
            }
        )

        bot._handle_command.assert_awaited_once()
        assert bot._handle_command.await_args.args[0] == "/status"
        bot._dispatch_with_lock.assert_not_awaited()

    async def test_routes_bare_message_command_with_arguments_when_supported(self) -> None:
        bot = _make_bot()

        await bot._on_message(
            {
                "user": "U123",
                "channel": "C123",
                "channel_type": "im",
                "ts": "1710000000.456",
                "text": "model gpt-5.4",
            }
        )

        bot._handle_command.assert_awaited_once()
        assert bot._handle_command.await_args.args[0] == "/model gpt-5.4"

    async def test_non_command_text_with_extra_words_stays_a_message(self) -> None:
        bot = _make_bot()

        await bot._on_message(
            {
                "user": "U123",
                "channel": "C123",
                "channel_type": "im",
                "ts": "1710000000.456",
                "text": "help me debug this",
            }
        )

        bot._handle_command.assert_not_awaited()
        bot._dispatch_with_lock.assert_awaited_once()

    async def test_run_streaming_updates_single_slack_message(self) -> None:
        bot = _make_bot()
        bot._config.streaming.edit_interval_seconds = 0.0

        async def _fake_stream(
            key: object,
            text: str,
            *,
            on_text_delta: object = None,
            on_thinking_delta: object = None,
            on_tool_activity: object = None,
            on_system_status: object = None,
        ) -> OrchestratorResult:
            assert key is not None
            assert text == "hello"
            assert on_thinking_delta is not None
            assert on_text_delta is not None
            await on_thinking_delta("step 1")
            await on_tool_activity("bash")
            await on_text_delta("final")
            await on_system_status(None)
            return OrchestratorResult(text="final")

        bot._orchestrator.handle_message_streaming = _fake_stream
        bot._app.client.chat_postMessage.return_value = {"ts": "2.0"}

        await bot._run_streaming(MagicMock(), "hello", "C123", "1710000000.123")

        bot._app.client.chat_postMessage.assert_awaited_once()
        assert "💭 *Thinking*" in bot._app.client.chat_postMessage.await_args.kwargs["text"]
        bot._app.client.chat_update.assert_awaited()
