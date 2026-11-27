"""Waiting for a media task: ``status`` with ``wait_seconds`` answers when the task has finished, never later than asked.

[INPUT]
- app.ai_agents.media_tools.task_results::media_task_status_result, started_tasks_result (POS: the status wait and the generate answer)
- app.ai_agents.media_tools.image_agent_tool / video_agent_tool (POS: the tools that expose ``wait_seconds``)

[OUTPUT]
Fails when a wait returns late or early, swallows a failure, outlives its call, or when a tool lets ``wait_seconds``
outside its range or its action through.

[POS]
tests/unit/ai_agents/media_tools/test_status_wait.py; the clock and the sleep are replaced, so no test waits for real.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool
from myrm_agent_harness.toolkits.llms.image.models import ImageGenerationConfig
from myrm_agent_harness.toolkits.llms.video import VideoGenerationConfig
from myrm_agent_harness.toolkits.llms.video.models import ProviderCapabilities
from myrm_agent_harness.toolkits.tasks import ErrorRecoverability, Task, TaskError, TaskStatus
from pydantic import ValidationError

from app.ai_agents.media_tools.image_agent_tool import create_image_generation_tool
from app.ai_agents.media_tools.task_results import MAX_WAIT_SECONDS, media_task_status_result, started_tasks_result
from app.ai_agents.media_tools.video_agent_tool import create_video_generation_tool
from app.ai_agents.media_tools.video_schema import build_video_input_schema

_RESULTS = "app.ai_agents.media_tools.task_results"
_TASK_ID = "task-1"


class _Clock:
    """Stands in for ``monotonic`` and ``sleep``: a wait takes no real time and every second of it can be counted."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> Iterator[_Clock]:
    fake = _Clock()
    with patch(f"{_RESULTS}.monotonic", fake.monotonic), patch(f"{_RESULTS}.sleep", fake.sleep):
        yield fake


def _task(status: TaskStatus, *, result: dict[str, object] | None = None, error: TaskError | None = None) -> Task:
    return Task(task_id=_TASK_ID, task_type="video_generate", user_id="u", status=status, payload={}, result=result, error=error)


def _store(*reads: Task | None | Exception) -> MagicMock:
    """A task store that answers each read in turn; the last answer repeats."""
    store = MagicMock()
    answers = list(reads)

    async def get_task(_task_id: str) -> Task | None:
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    store.get_task = AsyncMock(side_effect=get_task)
    return store


async def _status(store: MagicMock, wait_seconds: int | None = None) -> dict[str, object]:
    with patch(f"{_RESULTS}.media_task_store", return_value=store):
        return json.loads(await media_task_status_result(_TASK_ID, wait_seconds))


class TestWithoutWaiting:
    @pytest.mark.asyncio
    async def test_a_running_task_is_reported_at_once(self, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING))

        payload = await _status(store)

        assert payload["status"] == "running"
        assert store.get_task.await_count == 1
        assert clock.slept == []


class TestWaiting:
    @pytest.mark.asyncio
    async def test_the_answer_comes_as_soon_as_the_task_has_succeeded(self, clock: _Clock) -> None:
        result: dict[str, object] = {"video_urls": ["media://media_vid"]}
        store = _store(_task(TaskStatus.RUNNING), _task(TaskStatus.QUEUED), _task(TaskStatus.SUCCEEDED, result=result))

        payload = await _status(store, wait_seconds=60)

        assert payload["status"] == "succeeded"
        assert payload["result"] == result
        assert store.get_task.await_count == 3
        assert clock.slept == [1.0, 1.0]

    @pytest.mark.asyncio
    async def test_a_task_that_has_already_finished_is_not_waited_for(self, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.SUCCEEDED, result={"video_urls": ["media://media_vid"]}))

        payload = await _status(store, wait_seconds=60)

        assert payload["status"] == "succeeded"
        assert store.get_task.await_count == 1
        assert clock.slept == []

    @pytest.mark.asyncio
    async def test_a_failure_ends_the_wait_and_comes_with_what_to_do_next(self, clock: _Clock) -> None:
        error = TaskError(error_type="api_error", message="rejected", recoverable=ErrorRecoverability.PERMANENT)
        store = _store(_task(TaskStatus.RUNNING), _task(TaskStatus.FAILED, error=error))

        payload = await _status(store, wait_seconds=60)

        assert payload["status"] == "failed"
        assert payload["error"] == {"error_type": "api_error", "message": "rejected", "recoverable": "permanent"}
        assert "next_step" in payload
        assert clock.slept == [1.0]

    @pytest.mark.asyncio
    async def test_a_cancelled_task_ends_the_wait(self, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING), _task(TaskStatus.CANCELLED))

        payload = await _status(store, wait_seconds=60)

        assert payload["status"] == "cancelled"
        assert clock.slept == [1.0]

    @pytest.mark.asyncio
    async def test_the_wait_ends_at_the_limit_with_the_last_state(self, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING))

        payload = await _status(store, wait_seconds=3)

        assert payload["status"] == "running"
        assert sum(clock.slept) == 3.0
        assert store.get_task.await_count == 4

    @pytest.mark.asyncio
    async def test_the_last_sleep_is_cut_to_the_time_that_is_left(self, clock: _Clock) -> None:
        """Reading the store takes time too; the wait must not run past the limit because of it."""
        running = _task(TaskStatus.RUNNING)

        async def slow_read(_task_id: str) -> Task:
            clock.now += 0.4
            return running

        store = MagicMock()
        store.get_task = AsyncMock(side_effect=slow_read)

        await _status(store, wait_seconds=2)

        assert clock.slept == pytest.approx([1.0, 0.6])

    @pytest.mark.asyncio
    async def test_a_task_deleted_while_waiting_is_reported_as_not_found(self, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING), None)

        payload = await _status(store, wait_seconds=60)

        assert payload == {"error": "Task not found", "task_id": _TASK_ID}

    @pytest.mark.asyncio
    async def test_a_store_that_goes_away_while_waiting_is_an_explicit_error(self, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING), RuntimeError("worker stopped"))

        payload = await _status(store, wait_seconds=60)

        assert payload == {"error": "Task store unavailable", "task_id": _TASK_ID}

    @pytest.mark.asyncio
    async def test_cancelling_the_call_stops_the_wait(self) -> None:
        store = _store(_task(TaskStatus.RUNNING))
        asleep = asyncio.Event()

        async def sleep_until_cancelled(_seconds: float) -> None:
            asleep.set()
            await asyncio.Event().wait()

        with patch(f"{_RESULTS}.media_task_store", return_value=store), patch(f"{_RESULTS}.sleep", sleep_until_cancelled):
            waiting = asyncio.create_task(media_task_status_result(_TASK_ID, MAX_WAIT_SECONDS))
            await asleep.wait()
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting

        assert store.get_task.await_count == 1


def _image_tool() -> BaseTool:
    return create_image_generation_tool(ImageGenerationConfig(model="gpt-image-2.6-sunburst"))


def _video_tool() -> BaseTool:
    return create_video_generation_tool(VideoGenerationConfig(provider="volcengine", model="seedance-2-0"))


@pytest.fixture(params=[_image_tool, _video_tool], ids=["image_tool", "video_tool"])
def tool(request: pytest.FixtureRequest) -> BaseTool:
    return request.param()


class TestTheTools:
    @pytest.mark.asyncio
    async def test_status_waits_for_the_task_when_asked_to(self, tool: BaseTool, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING), _task(TaskStatus.SUCCEEDED, result={"count": 1}))

        with patch(f"{_RESULTS}.media_task_store", return_value=store):
            payload = json.loads(await tool.ainvoke({"action": "status", "task_id": _TASK_ID, "wait_seconds": 30}))

        assert payload["status"] == "succeeded"
        assert clock.slept == [1.0]

    @pytest.mark.asyncio
    async def test_status_without_wait_seconds_answers_at_once(self, tool: BaseTool, clock: _Clock) -> None:
        store = _store(_task(TaskStatus.RUNNING))

        with patch(f"{_RESULTS}.media_task_store", return_value=store):
            payload = json.loads(await tool.ainvoke({"action": "status", "task_id": _TASK_ID}))

        assert payload["status"] == "running"
        assert clock.slept == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("seconds", [0, -1, MAX_WAIT_SECONDS + 1])
    async def test_a_wait_outside_the_range_is_rejected_before_any_lookup(self, tool: BaseTool, seconds: int) -> None:
        store = _store(_task(TaskStatus.RUNNING))

        with patch(f"{_RESULTS}.media_task_store", return_value=store), pytest.raises(ValidationError):
            await tool.ainvoke({"action": "status", "task_id": _TASK_ID, "wait_seconds": seconds})

        store.get_task.assert_not_awaited()

    def test_the_schema_offers_the_wait_with_its_range(self, tool: BaseTool) -> None:
        wait = tool.args["wait_seconds"]

        assert wait["anyOf"][0] == {"type": "integer", "minimum": 1, "maximum": MAX_WAIT_SECONDS}
        assert "action='status'" in wait["description"]

    def test_the_description_says_status_can_wait(self, tool: BaseTool) -> None:
        assert "wait_seconds waits for a task that is still running" in tool.description

    @pytest.mark.asyncio
    async def test_the_image_tool_refuses_a_wait_on_another_action(self) -> None:
        payload = json.loads(await _image_tool().ainvoke({"action": "generate", "prompt": "a cat", "wait_seconds": 5}))

        assert "wait_seconds is a parameter of action=status, not of action=generate" in payload["error"]


@pytest.mark.parametrize(
    "capabilities",
    [
        None,
        ProviderCapabilities(supports_audio=False, supports_aspect_ratio=False, max_input_videos=0),
        ProviderCapabilities(supports_audio=True, supports_aspect_ratio=True, max_input_videos=2),
    ],
)
def test_every_video_provider_schema_offers_the_wait(capabilities: ProviderCapabilities | None) -> None:
    assert "wait_seconds" in build_video_input_schema(capabilities).model_json_schema()["properties"]


def test_the_generate_answer_tells_the_model_how_to_treat_a_running_task() -> None:
    answer = json.loads(started_tasks_result("image_generate", ["task_a", "task_b"], message="Generation started."))

    assert [task["task_id"] for task in answer["tasks"]] == ["task_a", "task_b"]
    assert answer["message"].startswith("Generation started. ")
    assert "Do not keep checking" in answer["message"]
    assert "status with wait_seconds only if your next step needs the result" in answer["message"]
