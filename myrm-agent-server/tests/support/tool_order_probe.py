"""What a live turn of conflicting tool calls leaves behind, and what that shows about the order they ran in.

[INPUT]
- (standard library only)

[OUTPUT]
- TAGS / STEP_LOG_NAME / LEDGER_NAME / BASH_STEP_KEY: the step names a turn asks for and the files it leaves in the chat's workspace
- Step / read_steps(): the steps the turn's shared log records as finished, in the order they started
- started_tags(): the tags the shared log records as started, finished or not, in the order they started
- step_script(): the program each step runs; it appends its start and its end to the shared log, and holds the steps named in ``pauses`` for as long as asked
- tool_round_ends_ns() / ran_in_one_message(): from the chat's usage ledger, whether two of the steps came from one model message
- bash_tags_in(): the step tags among the bash steps a chat stored
- step_keys_in(): the tool key of every step a chat stored, in the order they are listed
- BATCH_SYSTEM_PROMPT / batch_prompt() / WAITING_FILES / BATCH_SHELL_TAG / WRITE_STEP_KEY: the batch a Stop is pressed on (one shell step, then a write of each waiting file) and the words that ask for it
- stored_stop_reason(): the reason the chat stored as the end of its latest assistant turn

[POS]
Shared by tests/e2e/test_parallel_tool_order_live_chrome_e2e.py, tests/e2e/test_stop_exclusive_batch_chrome_e2e.py and
tests/api/agent/test_stop_exclusive_batch_live_e2e.py; the pure parsing is covered by tests/support/test_tool_order_probe.py.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BASH_STEP_KEY = "bash_code_execute_tool"
TAGS = ("ALPHA", "BRAVO", "CHARLIE", "DELTA")
STEP_LOG_NAME = "order_e2e.log"
LEDGER_NAME = "usage_ledger.jsonl"

# The batch a Stop is pressed on: one shell step, then a write of each waiting file; all exclusive calls, so the writes wait behind the step.
WRITE_STEP_KEY = "file_write_tool"
BATCH_SHELL_TAG = TAGS[0]
WAITING_FILES = ("waiting_one.txt", "waiting_two.txt")
BATCH_SYSTEM_PROMPT = (
    f"You work with {BASH_STEP_KEY} and {WRITE_STEP_KEY}. The tools run the calls of one response one after another, in the "
    "order you list them. When the user lists several calls, make one call per item, all in the same response and in "
    "the order listed; never wait for one result before the next call and never combine items into one call."
)

# Finish reasons of a model call that ended by asking for tools (OpenAI-style and Anthropic-style wire values).
_TOOL_ROUND_FINISH_REASONS = frozenset({"tool_calls", "tool_use"})
_STEP_TAG_PATTERN = re.compile(r"step\.py\s+([A-Z]+)")


@dataclass(frozen=True)
class Step:
    tag: str
    started_ns: int
    ended_ns: int


def step_script(pauses: Mapping[str, float] | None = None) -> str:
    """The program each step runs: record start and end in the shared log, with a pause between so overlap shows.

    A step named in ``pauses`` waits that many seconds instead of the default half second, so a test can act on a turn while
    its first step is still running.
    """
    return "\n".join(
        (
            "import sys",
            "import time",
            "from pathlib import Path",
            "",
            f"log = Path(__file__).with_name({STEP_LOG_NAME!r})",
            f"pauses = {dict(pauses or {})!r}",
            "",
            "",
            "def stamp(phase: str) -> None:",
            '    with log.open("a", encoding="utf-8") as handle:',
            '        handle.write(f"{sys.argv[1]} {phase} {time.time_ns()}\\n")',
            "",
            "",
            'stamp("start")',
            "time.sleep(pauses.get(sys.argv[1], 0.5))",
            'stamp("end")',
            "",
        )
    )


def _log_rows(log: Path) -> Iterator[tuple[str, str, int]]:
    """The complete rows of the shared log as (tag, phase, time in ns); a row another step is still writing is skipped."""
    if not log.exists():
        return
    for line in log.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3:
            yield parts[0], parts[1], int(parts[2])


def read_steps(log: Path) -> list[Step]:
    """The steps that ended, in the order they started."""
    starts: list[tuple[str, int]] = []
    ends: dict[str, int] = {}
    for tag, phase, stamp in _log_rows(log):
        if phase == "start":
            starts.append((tag, stamp))
        else:
            ends[tag] = stamp
    return [Step(tag, started, ends[tag]) for tag, started in starts if tag in ends]


def started_tags(log: Path) -> list[str]:
    """The tags that started, in the order they started; a step that has not ended (or never will) counts."""
    return [tag for tag, phase, _ in _log_rows(log) if phase == "start"]


def tool_round_ends_ns(ledger: Path) -> list[int]:
    """When each model call that ended by asking for tools finished, from the chat's usage ledger."""
    if not ledger.exists():
        return []
    ends: list[int] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a row still being appended
        if row.get("finish_reason") in _TOOL_ROUND_FINISH_REASONS:
            ends.append(int(datetime.fromisoformat(row["ts"]).timestamp() * 1_000_000_000))
    return ends


def ran_in_one_message(steps: list[Step], round_ends_ns: list[int]) -> bool:
    """Whether two consecutive steps came from one model message.

    A step that comes from a later message starts after that message's model call ended, so such a call ended between the
    two starts; when none did, the later step was already part of the message the earlier one came from.
    """
    return any(
        not any(earlier.started_ns < end <= later.started_ns for end in round_ends_ns)
        for earlier, later in zip(steps, steps[1:], strict=False)
    )


def _progress_steps(messages: Iterable[object]) -> Iterator[dict[str, object]]:
    """The progress steps the stored messages carry, in the order they are listed."""
    for message in messages:
        if not isinstance(message, dict):
            continue
        metadata = message.get("metadata")
        raw_steps = message.get("progressSteps") or (metadata.get("progressSteps") if isinstance(metadata, dict) else None)
        for step in raw_steps or []:
            if isinstance(step, dict):
                yield step


def _step_key(step: Mapping[str, object]) -> str:
    return str(step.get("step_key") or step.get("tool_name") or "")


def step_keys_in(messages: Iterable[object]) -> list[str]:
    """The tool key of every progress step among the stored messages, in the order they are listed."""
    return [_step_key(step) for step in _progress_steps(messages)]


def bash_tags_in(messages: Iterable[object]) -> list[str]:
    """The tag of every bash step among the stored messages' progress steps, in the order they are listed."""
    tags: list[str] = []
    for step in _progress_steps(messages):
        if not _step_key(step).startswith(BASH_STEP_KEY):
            continue
        found = _STEP_TAG_PATTERN.search(json.dumps(step, ensure_ascii=False))
        if found:
            tags.append(found.group(1))
    return tags


def batch_prompt(script: Path, workspace: Path) -> str:
    """The message that asks, in one response, for the shell step ``script`` and behind it a write of each waiting file."""
    writes = "\n".join(
        f"{number}. {WRITE_STEP_KEY}: path {workspace / name}, content {name}"
        for number, name in enumerate(WAITING_FILES, start=2)
    )
    return (
        "Make these calls in your next response, in this order (the tools run them one after another in the order you list them):\n"
        f"1. {BASH_STEP_KEY}: python3 {script} {BATCH_SHELL_TAG}\n{writes}"
    )


def stored_stop_reason(messages: Sequence[object]) -> str | None:
    """The reason the chat stored on its latest assistant message as the end of the turn, if it stored one."""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        metadata = message.get("metadata")
        stop = metadata.get("stopReason") if isinstance(metadata, dict) else None
        detail = stop.get("detail") if isinstance(stop, dict) else None
        reason = detail.get("reason") if isinstance(detail, dict) else None
        return reason if isinstance(reason, str) else None
    return None
