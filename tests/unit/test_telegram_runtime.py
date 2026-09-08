"""Telegram adapters use the canonical runtime without network or credentials."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hyperclaw import telegram_bot as telegram_module
from hyperclaw.memory_manager import MemoryManager
from hyperclaw.orchestrator import Orchestrator
from tests.unit.test_state_persistence import temporary_postgres  # noqa: F401


class RuntimeAdapter(Orchestrator):
    """Real session operations with a deterministic model stream boundary."""

    def __init__(self, memory):
        super().__init__()
        self._memory = memory
        self._initialized = True
        self.turns = []
        self.resets = []

    async def stream_events(self, message, session_id="default", channel="api", attachments=None):
        self.turns.append((message, session_id, channel, attachments))
        await self._memory.load_conversation(session_id)
        self._memory.add_message(session_id, "user", message)
        yield "thinking", "synthetic thought"
        yield "text", "synthetic response"
        self._memory.add_message(session_id, "assistant", "synthetic response")
        await self._memory.save_conversation(session_id)

    async def reset_session(self, session_id):
        self.resets.append(session_id)
        await super().reset_session(session_id)


@pytest.fixture
def telegram_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRETS_MOUNT", str(tmp_path / "no-secrets"))
    monkeypatch.setattr(telegram_module, "ALLOWED_CHAT_IDS", {42, -43})
    monkeypatch.setattr(telegram_module, "TENANT_ID", "synthetic-owner")
    memory = MemoryManager(root=tmp_path / "canonical")
    asyncio.run(memory.initialize())
    runtime = RuntimeAdapter(memory)
    resolve = AsyncMock(return_value=runtime)
    monkeypatch.setattr(telegram_module, "get_orchestrator", resolve, raising=False)
    monkeypatch.setattr(telegram_module, "get_solomon", lambda: None, raising=False)
    legacy = AsyncMock(return_value=[])
    monkeypatch.setattr(telegram_module, "_load_history", legacy)
    # The old per-message writer is forbidden; persistence belongs to the runtime.
    monkeypatch.setattr(
        telegram_module, "_save_history", AsyncMock(side_effect=AssertionError("legacy write")),
        raising=False,
    )
    return runtime, resolve, legacy


def update_for(chat_id=42, text="hello"):
    placeholder = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(
        text=text, reply_text=AsyncMock(return_value=placeholder),
        photo=None, document=None, caption=None,
    )
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=message)
    context = SimpleNamespace(bot=SimpleNamespace(send_chat_action=AsyncMock()))
    return update, context, placeholder


def test_allowed_telegram_chat_streams_with_canonical_session_and_attachments(telegram_runtime):
    runtime, _, _ = telegram_runtime

    async def run():
        bot = telegram_module.TelegramBot()
        attachment = {"type": "image", "source": {"type": "base64", "data": "synthetic"}}
        bot.pending_attachments[42].append(attachment)
        update, context, placeholder = update_for()
        await bot.handle_message(update, context)
        assert runtime.turns == [("hello", "telegram:synthetic-owner:42", "telegram", [attachment])]
        assert [m["content"] for m in runtime._memory.get_conversation_history("telegram:synthetic-owner:42")] == [
            "hello", "synthetic response"
        ]
        assert placeholder.edit_text.call_args.args == ("synthetic response",)
        assert not bot.pending_attachments.get(42)

    asyncio.run(run())


def test_unauthorized_telegram_chat_never_resolves_runtime_or_downloads(telegram_runtime):
    runtime, resolve, legacy = telegram_runtime

    async def run():
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for(chat_id=999)
        update.message.document = SimpleNamespace(get_file=AsyncMock())
        await bot.handle_message(update, context)
        await bot.handle_file(update, context)
        await bot.clear_command(update, context)
        assert runtime.turns == []
        assert runtime.resets == []
        resolve.assert_not_awaited()
        legacy.assert_not_awaited()
        update.message.document.get_file.assert_not_awaited()
        update.message.reply_text.assert_not_awaited()

    asyncio.run(run())


def test_missing_chat_is_ignored_without_runtime_access(telegram_runtime):
    _, resolve, _ = telegram_runtime

    async def run():
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for()
        update.effective_chat = None
        await bot.handle_message(update, context)
        await bot.handle_file(update, context)
        resolve.assert_not_awaited()

    asyncio.run(run())


def test_telegram_chats_keep_separate_canonical_histories(telegram_runtime):
    runtime, _, _ = telegram_runtime

    async def run():
        bot = telegram_module.TelegramBot()
        first, first_context, _ = update_for(text="first chat")
        second, second_context, _ = update_for(chat_id=-43, text="second chat")
        await bot.handle_message(first, first_context)
        await bot.handle_message(second, second_context)
        assert [(turn[0], turn[1]) for turn in runtime.turns] == [
            ("first chat", "telegram:synthetic-owner:42"),
            ("second chat", "telegram:synthetic-owner:-43"),
        ]
        history = await runtime._memory.load_conversation("telegram:synthetic-owner:42")
        assert [m["content"] for m in history] == ["first chat", "synthetic response"]

    asyncio.run(run())


def test_legacy_history_imports_once_and_never_returns_after_reset(telegram_runtime):
    runtime, _, legacy = telegram_runtime
    legacy.return_value = [{"role": "user", "content": "legacy hello"}]

    async def run():
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for()
        await bot.handle_message(update, context)
        history = await runtime._memory.load_conversation("telegram:synthetic-owner:42")
        assert [m["content"] for m in history] == ["legacy hello", "hello", "synthetic response"]
        bot.pending_attachments[42].append({"type": "text", "text": "discard on reset"})
        await bot.clear_command(update, context)
        assert runtime.resets == ["telegram:synthetic-owner:42"]
        assert not bot.pending_attachments.get(42)
        assert await runtime._memory.load_conversation("telegram:synthetic-owner:42") == []
        # Both a fresh adapter and manager retain the durable migration decision.
        fresh_memory = MemoryManager(root=runtime._memory.root)
        await fresh_memory.initialize()
        runtime._memory = fresh_memory
        restarted_bot = telegram_module.TelegramBot()
        await restarted_bot.handle_message(update, context)
        history = await fresh_memory.load_conversation("telegram:synthetic-owner:42")
        assert [m["content"] for m in history] == ["hello", "synthetic response"]
        assert legacy.await_count == 1

    asyncio.run(run())


def test_existing_canonical_session_skips_legacy_database(telegram_runtime):
    runtime, _, legacy = telegram_runtime

    async def run():
        runtime._memory.add_message("telegram:synthetic-owner:42", "user", "canonical history")
        await runtime._memory.save_conversation("telegram:synthetic-owner:42")
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for()
        await bot.handle_message(update, context)
        legacy.assert_not_awaited()
        history = await runtime._memory.load_conversation("telegram:synthetic-owner:42")
        assert history[0]["content"] == "canonical history"

    asyncio.run(run())


def test_reset_failure_is_not_reported_as_cleared(telegram_runtime):
    runtime, _, _ = telegram_runtime

    async def fail_reset(session_id):
        raise OSError("synthetic disk failure")

    runtime.reset_session = fail_reset

    async def run():
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for()
        bot.pending_attachments[42].append({"type": "text", "text": "keep pending"})
        with pytest.raises(OSError, match="synthetic disk failure"):
            await bot.clear_command(update, context)
        update.message.reply_text.assert_not_awaited()
        assert bot.pending_attachments[42]

    asyncio.run(run())


def test_captioned_file_reaches_runtime_with_attachment(telegram_runtime):
    runtime, _, _ = telegram_runtime

    async def run():
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for(text=None)
        update.message.caption = "summarize this"
        file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=b"synthetic file"))
        update.message.document = SimpleNamespace(
            file_size=14, file_name="note.txt", mime_type="text/plain", get_file=AsyncMock(return_value=file)
        )
        await bot.handle_file(update, context)
        assert runtime.turns == [(
            "summarize this", "telegram:synthetic-owner:42", "telegram",
            [{"type": "text", "text": "[Contents of note.txt]\n\nsynthetic file"}],
        )]

    asyncio.run(run())


def test_failed_legacy_import_retries_without_marking_session_complete(telegram_runtime):
    runtime, _, legacy = telegram_runtime
    legacy.side_effect = [
        ConnectionError("synthetic legacy failure"),
        [{"role": "user", "content": "legacy history"}],
    ]

    async def run():
        bot = telegram_module.TelegramBot()
        update, context, _ = update_for()
        await bot.handle_message(update, context)
        assert not await runtime._memory.conversation_exists("telegram:synthetic-owner:42")
        assert runtime.turns == []
        await bot.handle_message(update, context)
        history = await runtime._memory.load_conversation("telegram:synthetic-owner:42")
        assert [m["content"] for m in history] == ["legacy history", "hello", "synthetic response"]

    asyncio.run(run())


def test_legacy_read_error_is_not_treated_as_empty_history(monkeypatch):
    class UnavailablePool:
        def acquire(self):
            raise ConnectionError("synthetic legacy failure")

    monkeypatch.setattr(telegram_module, "_get_db_pool", AsyncMock(return_value=UnavailablePool()))

    async def run():
        with pytest.raises(ConnectionError, match="synthetic legacy failure"):
            await telegram_module._load_history("synthetic-owner", 42)

    asyncio.run(run())


def test_legacy_connection_error_is_not_treated_as_unconfigured(tmp_path, monkeypatch):
    import asyncpg

    monkeypatch.setattr(telegram_module, "_db_pool", None)
    for key, value in {
        "SECRETS_MOUNT": str(tmp_path / "no-secrets"),
        "POSTGRES_HOST": "synthetic.invalid", "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "synthetic", "POSTGRES_PASSWORD": "synthetic", "POSTGRES_DB": "synthetic",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(asyncpg, "create_pool", AsyncMock(side_effect=ConnectionError("synthetic offline")))

    async def run():
        with pytest.raises(ConnectionError, match="synthetic offline"):
            await telegram_module._get_db_pool()

    asyncio.run(run())


def test_close_legacy_history_releases_database_pool(temporary_postgres, monkeypatch):
    import asyncpg

    async def run():
        pool = await asyncpg.create_pool(temporary_postgres, min_size=1, max_size=1)
        monkeypatch.setattr(telegram_module, "_db_pool", pool)
        try:
            await telegram_module.close_legacy_history()
            with pytest.raises(asyncpg.InterfaceError, match="closed"):
                await pool.acquire()
            await telegram_module.close_legacy_history()
        finally:
            await pool.close()

    asyncio.run(run())


def test_canonical_reset_waits_for_legacy_import_and_keeps_reset_durable(telegram_runtime):
    runtime, _, legacy = telegram_runtime

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_legacy(tenant_id, chat_id):
            entered.set()
            await release.wait()
            return [{"role": "user", "content": "synthetic legacy message"}]

        legacy.side_effect = slow_legacy
        bot = telegram_module.TelegramBot()
        importing = asyncio.create_task(bot._prepare_session(42))
        await entered.wait()
        resetting = asyncio.create_task(runtime.reset_session("telegram:synthetic-owner:42"))
        try:
            await asyncio.sleep(0)
            assert not resetting.done(), "Reset must wait for the in-flight canonical import"
        finally:
            release.set()
            await asyncio.gather(importing, resetting)
        fresh = MemoryManager(root=runtime._memory.root)
        await fresh.initialize()
        assert await fresh.conversation_exists("telegram:synthetic-owner:42")
        assert await fresh.load_conversation("telegram:synthetic-owner:42") == []
        runtime._memory = fresh
        await telegram_module.TelegramBot()._prepare_session(42)
        assert legacy.await_count == 1

    asyncio.run(run())


def test_competing_adapters_import_legacy_history_once(telegram_runtime):
    runtime, _, legacy = telegram_runtime

    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_legacy(tenant_id, chat_id):
            entered.set()
            await release.wait()
            return [{"role": "user", "content": "single legacy message"}]

        legacy.side_effect = slow_legacy
        first = asyncio.create_task(telegram_module.TelegramBot()._prepare_session(42))
        await entered.wait()
        second = asyncio.create_task(telegram_module.TelegramBot()._prepare_session(42))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        history = await runtime._memory.load_conversation("telegram:synthetic-owner:42")
        assert [message["content"] for message in history] == ["single legacy message"]
        assert legacy.await_count == 1

    asyncio.run(run())
