"""Runs inside the fresh environment of ``fresh_install_smoke.py``; imports only the installed wheel and the stdlib.

[INPUT]
- argv: the phase (``extra-missing`` or ``stub-turn``) and the path of the stub ACP agent

[OUTPUT]
- main(): exit 0 when the phase behaves as a user of the installed framework would see it; one line saying what
  differed otherwise

[POS]
The program a user of the framework would write to delegate one turn to an ACP agent: register a backend, run a
turn, read the events.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from myrm_agent_harness.toolkits.acp import RuntimeConfig, RuntimePool
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType

_PROMPT = "ping"
_EXTRA_HINT = "myrm-agent-harness[acp]"


async def _turn(stub_agent: Path) -> list[RuntimeEvent]:
    pool = RuntimePool(max_concurrent=1)
    pool.register(
        "stub",
        RuntimeConfig(
            backend_type="acp", command=sys.executable, args=[str(stub_agent)], cwd=str(Path.cwd()), timeout_seconds=60
        ),
    )
    try:
        return [event async for event in pool.run_turn("stub", _PROMPT, session_id="fresh-install-smoke")]
    finally:
        await pool.close_all()


def _problem(phase: str, events: list[RuntimeEvent]) -> str | None:
    """What differs from what ``phase`` promises, or None."""
    kinds = [event.type for event in events]
    if phase == "extra-missing":
        errors = [str(event.data.get("error")) for event in events if event.type == RuntimeEventType.ERROR]
        if not errors:
            return f"no error event without the acp extra: {kinds}"
        return None if _EXTRA_HINT in errors[0] else f"the error does not name {_EXTRA_HINT}: {errors[0]}"
    text = "".join(str(event.data.get("content")) for event in events if event.type == RuntimeEventType.TEXT_DELTA)
    if f"[echo] {_PROMPT}" not in text:
        return f"the stub agent's answer did not stream: {text!r} from {kinds}"
    return None if kinds[-1] == RuntimeEventType.DONE else f"the turn did not end with done: {kinds}"


def main(argv: list[str]) -> int:
    phase, stub_agent = argv[0], Path(argv[1])
    problem = _problem(phase, asyncio.run(_turn(stub_agent)))
    if problem is not None:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
