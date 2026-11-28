"""Runs inside the fresh environment of ``fresh_install_smoke.py``; imports only the installed wheel and the stdlib.

[INPUT]
- argv: the phase (``extra-missing`` or ``stub-turn``) and the path of the stub ACP agent

[OUTPUT]
- main(): exit 0 when the phase behaves as a user of the installed framework would see it; one line saying what
  differed otherwise

[POS]
The program a host of the framework would write to delegate turns to an agent. Every name it uses comes from the
public surface ``myrm_agent_harness.api.delegation``, so a name a host needs and the surface lacks ends this run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from myrm_agent_harness.api.delegation import (
    ClientIdentity,
    PermissionRequest,
    PermissionVerdict,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    RuntimePool,
)

_EXTRA_HINT = "myrm-agent-harness[acp]"
_HOST = ClientIdentity(name="fresh-install-smoke", title="Fresh Install Smoke", version="9.9.9")
# The smallest CLI a host can delegate to: one event line carrying the prompt it was given as its last argument.
_FAKE_CLI = (
    'import json, sys\nprint(json.dumps({"type": "assistant", "content": "[cli] " + sys.argv[-1]}), flush=True)\n'
)

type _Check = Callable[[Path, Path], Awaitable[str | None]]


class _Broker:
    """A host's own permission policy: it records what the agent asks for and answers as it was told to."""

    def __init__(self, *, allow: bool) -> None:
        self.allow = allow
        self.requests: list[PermissionRequest] = []

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.requests.append(request)
        return PermissionVerdict(allowed=self.allow)


async def _turn(
    config: RuntimeConfig, prompt: str, workspace: Path, broker: _Broker | None = None
) -> tuple[str, list[RuntimeEvent]]:
    """The text a turn streamed and its events; the pool is released whatever the turn did."""
    pool = RuntimePool(max_concurrent=1)
    pool.register("stub", config)
    try:
        turn = pool.run_turn(
            "stub", prompt, session_id="fresh-install-smoke", workspace=str(workspace), permissions=broker
        )
        events = [event async for event in turn]
    finally:
        await pool.close_all()
    text = "".join(str(event.data.get("content")) for event in events if event.type == RuntimeEventType.TEXT_DELTA)
    return text, events


def _acp(stub_agent: Path) -> RuntimeConfig:
    return RuntimeConfig(
        backend_type="acp", command=sys.executable, args=[str(stub_agent)], timeout_seconds=60, client=_HOST
    )


def _ended(events: list[RuntimeEvent]) -> str | None:
    kinds = [event.type for event in events]
    return None if kinds[-1:] == [RuntimeEventType.DONE] else f"the turn did not end with done: {kinds}"


async def _acp_names_its_extra(stub_agent: Path, workspace: Path) -> str | None:
    _, events = await _turn(_acp(stub_agent), "ping", workspace)
    errors = [str(event.data.get("error")) for event in events if event.type == RuntimeEventType.ERROR]
    if not errors:
        return f"no error event without the acp extra: {[event.type for event in events]}"
    return None if _EXTRA_HINT in errors[0] else f"the error does not name {_EXTRA_HINT}: {errors[0]}"


async def _cli_turn_needs_no_extra(_stub_agent: Path, workspace: Path) -> str | None:
    script = workspace / "fake_cli.py"
    script.write_text(_FAKE_CLI, encoding="utf-8")
    config = RuntimeConfig(backend_type="cli", command=sys.executable, args=[str(script)], timeout_seconds=60)
    text, events = await _turn(config, "ping", workspace)
    return _ended(events) or (None if text == "[cli] ping" else f"the CLI's answer did not stream: {text!r}")


async def _acp_answer_streams(stub_agent: Path, workspace: Path) -> str | None:
    text, events = await _turn(_acp(stub_agent), "ping", workspace)
    return _ended(events) or (None if "[echo] ping" in text else f"the stub agent's answer did not stream: {text!r}")


async def _agent_is_told_who_the_host_is(stub_agent: Path, workspace: Path) -> str | None:
    text, _ = await _turn(_acp(stub_agent), "/client", workspace)
    told = json.loads(text.removeprefix("[client] ")) if text.startswith("[client] ") else text
    expected = {"name": _HOST.name, "title": _HOST.title, "version": _HOST.version}
    return None if told == expected else f"the agent was told {told!r}, not {expected!r}"


async def _hosts_verdict_decides_the_edit(stub_agent: Path, workspace: Path) -> str | None:
    problems: list[str] = []
    for allow in (True, False):
        broker = _Broker(allow=allow)
        target = workspace / f"edit-{'allowed' if allow else 'denied'}.txt"
        prompt = "/edit " + json.dumps({"path": target.name, "content": "edited"})
        await _turn(_acp(stub_agent), prompt, workspace, broker)
        asked = {request.kind for request in broker.requests}
        if asked != {"edit"}:
            problems.append(f"the broker was asked about {sorted(map(str, asked))}, not an edit (allow={allow})")
        if target.exists() != allow:
            problems.append(
                f"the file {'is missing' if allow else 'was written'} after the host answered allow={allow}"
            )
    return "; ".join(problems) or None


_PHASES: dict[str, tuple[_Check, ...]] = {
    "extra-missing": (_acp_names_its_extra, _cli_turn_needs_no_extra),
    "stub-turn": (_acp_answer_streams, _agent_is_told_who_the_host_is, _hosts_verdict_decides_the_edit),
}


async def _first_problem(phase: str, stub_agent: Path) -> str | None:
    for check in _PHASES[phase]:
        with tempfile.TemporaryDirectory(prefix="myrm-smoke-workspace-") as workspace:
            problem = await check(stub_agent, Path(workspace).resolve())
        if problem is not None:
            return f"{check.__name__.strip('_')}: {problem}"
    return None


def main(argv: list[str]) -> int:
    phase, stub_agent = argv[0], Path(argv[1])
    problem = asyncio.run(_first_problem(phase, stub_agent))
    if problem is not None:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
