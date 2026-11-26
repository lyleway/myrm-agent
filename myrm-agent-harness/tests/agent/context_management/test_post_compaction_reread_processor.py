"""The post-compaction reread: what triggers it, how much it may inject, and which file it reads.

The pipeline is real (``ContextPipeline`` with the real reread processor); the compaction processors are stand-ins that
only carry the real processor names, because what decides the trigger is the name the engine credits their savings to.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from myrm_agent_harness.agent.context_management.pipeline.base import BaseProcessor, ProcessorContext
from myrm_agent_harness.agent.context_management.pipeline.engine import ContextPipeline, build_default_processors
from myrm_agent_harness.agent.context_management.pipeline.processors import (
    PostCompactionRereadProcessor,
    ThinkingBlockCleaner,
)
from myrm_agent_harness.agent.context_management.pipeline.processors.post_compaction_reread_processor import (
    _COMPACTION_PROCESSOR_NAMES,
    _compute_budget,
)
from myrm_agent_harness.agent.context_management.tracking.artifact_tracker import (
    clear_artifact_tracker,
    create_artifact_tracker,
)
from myrm_agent_harness.core.context_vars import workspace_root_var
from myrm_agent_harness.utils.text_utils import get_token_count
from tests.support.fake_secrets import hex_secret

# Every processor that credits ``context.tokens_saved`` besides the three compactions.
_OTHER_SAVERS = (
    "filter",
    "media_filter",
    "media_budget_governor",
    "active_tool_result_prune",
    "cache_ttl_prune",
    "selective_eviction",
    "ThinkingBlockCleaner",
)


class _Saver(BaseProcessor):
    """A processor that saves a fixed number of tokens under a chosen name."""

    def __init__(self, name: str, saved: int) -> None:
        self._name = name
        self._saved = saved

    @property
    def name(self) -> str:
        return self._name

    async def should_process(self, context: ProcessorContext) -> bool:
        return True

    async def process(self, context: ProcessorContext) -> ProcessorContext:
        context.tokens_saved += self._saved
        return context


class _SaveThenFail(_Saver):
    async def process(self, context: ProcessorContext) -> ProcessorContext:
        context.tokens_saved += self._saved
        raise RuntimeError("compaction broke halfway")


@pytest.fixture
def chat_id() -> Iterator[str]:
    cid = f"reread-{uuid.uuid4().hex[:8]}"
    yield cid
    clear_artifact_tracker(cid)


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "ws"
    root.mkdir()
    token = workspace_root_var.set(str(root))
    yield root
    workspace_root_var.reset(token)


def _context(chat_id: str | None, messages: list[BaseMessage] | None = None) -> ProcessorContext:
    return ProcessorContext(
        messages=messages or [HumanMessage(content="continue")], user_query="continue", chat_id=chat_id
    )


def _file(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _track(chat_id: str, *paths: str) -> None:
    tracker = create_artifact_tracker(chat_id)
    for path in paths:
        tracker.record_modification(path)


async def _reread(chat_id: str, window: int = 128_000) -> str:
    """The text the reread appends after a compaction, '' when it appended nothing."""
    context = await ContextPipeline([_Saver("summarize", 500), PostCompactionRereadProcessor(window)]).process(
        _context(chat_id)
    )
    return str(context.messages[-1].content) if len(context.messages) > 1 else ""


class TestTrigger:
    def test_compaction_names_are_the_default_chain_names_and_run_before_the_reread(self) -> None:
        order = [p.name for p in build_default_processors(session_notes_manager=MagicMock())]
        assert set(order) >= _COMPACTION_PROCESSOR_NAMES
        assert set(order) >= set(_OTHER_SAVERS)
        assert all(order.index(name) < order.index("post_compaction_reread") for name in _COMPACTION_PROCESSOR_NAMES)

    @pytest.mark.parametrize("compaction", sorted(_COMPACTION_PROCESSOR_NAMES))
    async def test_each_compaction_appends_one_human_message_at_the_tail(
        self, compaction: str, chat_id: str, workspace: Path
    ) -> None:
        _track(chat_id, str(_file(workspace, "a.py", "print('latest')\n")))
        pipeline = ContextPipeline([_Saver(compaction, 500), PostCompactionRereadProcessor()])

        result = await pipeline.process(_context(chat_id))

        tail = result.messages[-1]
        assert len(result.messages) == 2
        assert type(tail) is HumanMessage
        assert "print('latest')" in str(tail.content)
        assert not any(isinstance(m, SystemMessage) for m in result.messages)

    @pytest.mark.parametrize("saver", _OTHER_SAVERS)
    async def test_other_savers_do_not_reread(self, saver: str, chat_id: str, workspace: Path) -> None:
        _track(chat_id, str(_file(workspace, "a.py", "x = 1\n")))
        pipeline = ContextPipeline([_Saver(saver, 500), PostCompactionRereadProcessor()])

        result = await pipeline.process(_context(chat_id))

        assert len(result.messages) == 1
        assert result.tokens_saved == 500

    async def test_thinking_cleanup_alone_does_not_reread(self, chat_id: str, workspace: Path) -> None:
        _track(chat_id, str(_file(workspace, "a.py", "x = 1\n")))
        messages = [
            HumanMessage(content="first"),
            AIMessage(content="done", additional_kwargs={"reasoning_content": "t" * 8000}),
            HumanMessage(content="next"),
        ]

        result = await ContextPipeline([ThinkingBlockCleaner(), PostCompactionRereadProcessor()]).process(
            _context(chat_id, messages)
        )

        assert result.tokens_saved == 2000
        assert len(result.messages) == 3
        assert result.tokens_saved_by == {"ThinkingBlockCleaner": 2000}

    async def test_compaction_without_a_chat_does_not_reread(self, workspace: Path) -> None:
        result = await ContextPipeline([_Saver("compress", 500), PostCompactionRereadProcessor()]).process(
            _context(None)
        )
        assert len(result.messages) == 1


class TestSavingsAttribution:
    async def test_savings_are_credited_to_the_processor_that_made_them(self) -> None:
        pipeline = ContextPipeline([_Saver("a", 7), _Saver("idle", 0), _Saver("b", 3)])

        context = await pipeline.process(_context("chat"))
        assert (context.tokens_saved, context.tokens_saved_by) == (10, {"a": 7, "b": 3})

        context = await pipeline.process(context)
        assert (context.tokens_saved, context.tokens_saved_by) == (20, {"a": 14, "b": 6})

    async def test_two_instances_of_one_name_add_up(self) -> None:
        context = await ContextPipeline([_Saver("compress", 4), _Saver("compress", 6)]).process(_context("chat"))
        assert context.tokens_saved_by == {"compress": 10}

    async def test_a_processor_that_failed_is_credited_with_nothing(self) -> None:
        context = await ContextPipeline([_SaveThenFail("compress", 9), _Saver("summarize", 1)]).process(
            _context("chat")
        )
        assert context.tokens_saved_by == {"summarize": 1}


class TestBudget:
    @pytest.mark.parametrize(
        ("window", "lowest", "highest"),
        [(200_000, 49_900, 50_000), (32_000, 7_900, 8_000), (8_000, 5_000, 5_000)],
    )
    def test_budget_is_a_quarter_of_the_window_between_the_floor_and_the_cap(
        self, window: int, lowest: int, highest: int
    ) -> None:
        assert lowest <= _compute_budget(window, [HumanMessage(content="hi")]) <= highest

    async def test_the_injection_follows_the_window_the_processor_was_built_with(
        self, chat_id: str, workspace: Path
    ) -> None:
        many_lines = "".join(f"line {i} " + "word " * 20 + "\n" for i in range(4000))
        _track(chat_id, str(_file(workspace, "big.txt", many_lines)))

        wide = get_token_count(await _reread(chat_id, window=200_000))
        narrow = get_token_count(await _reread(chat_id, window=32_000))

        assert narrow < wide
        assert narrow <= 8_000

    async def test_a_file_of_few_very_long_lines_still_stays_within_the_budget(
        self, chat_id: str, workspace: Path
    ) -> None:
        _track(chat_id, str(_file(workspace, "bundle.min.js", "var a=[" + ",".join(["12345"] * 60_000) + "];")))

        assert get_token_count(await _reread(chat_id, window=32_000)) <= 8_000


class TestWhichFileIsRead:
    @pytest.mark.parametrize("spelling", ["/workspace/notes/plan.md", "notes/plan.md"])
    async def test_the_workspace_file_is_read_for_the_alias_and_the_relative_spelling(
        self, spelling: str, chat_id: str, workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _file(workspace, "notes/plan.md", "WORKSPACE-FILE\n")
        elsewhere = tmp_path / "cwd"
        _file(elsewhere, "notes/plan.md", "FILE-IN-THE-PROCESS-CWD\n")
        monkeypatch.chdir(elsewhere)
        _track(chat_id, spelling)

        injected = await _reread(chat_id)

        assert "WORKSPACE-FILE" in injected
        assert "FILE-IN-THE-PROCESS-CWD" not in injected
        assert f"--- {spelling} ---" in injected

    async def test_the_host_absolute_path_is_read(self, chat_id: str, workspace: Path) -> None:
        _track(chat_id, str(_file(workspace, "notes/plan.md", "HOST-FILE\n")))
        assert "HOST-FILE" in await _reread(chat_id)

    async def test_without_a_workspace_the_path_is_taken_as_written(self, chat_id: str, tmp_path: Path) -> None:
        _track(chat_id, str(_file(tmp_path, "plain.txt", "PLAIN-FILE\n")))
        assert "PLAIN-FILE" in await _reread(chat_id)

    async def test_secrets_are_masked_the_way_the_file_tool_masks_them(self, chat_id: str, workspace: Path) -> None:
        secret = hex_secret("reread-redaction")
        _track(chat_id, str(_file(workspace, "service.env", f"SERVICE_TOKEN={secret}\nREGION=eu\n")))

        injected = await _reread(chat_id)

        assert "REGION=eu" in injected
        assert secret not in injected
