"""Ask a CLI a short question: bounded in time and in output, its whole process tree stopped at the deadline.

[INPUT]
- core.security.bounded_communicate::communicate_bounded (POS: communicate() with a cap on each pipe, stopping the process through the caller on a deadline or a cancel)
- utils.os_compat::SIGKILL, get_process_group_kwargs, kill_spawned_group (POS: Unified abstractions for POSIX/Windows differences (file locks, process groups))

[OUTPUT]
- ProbeResult: What a probe printed and how it ended.
- run_probe: Run ``argv`` once and return its result, or ``None`` when it could not be run to completion.

[POS]
Process primitive of the detection and login-state checks (``--version``, ``auth status``): a probe that hangs
or floods its output costs a bounded wait and leaves no process behind.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from myrm_agent_harness.core.security.bounded_communicate import communicate_bounded
from myrm_agent_harness.utils.os_compat import SIGKILL, get_process_group_kwargs, kill_spawned_group

logger = logging.getLogger(__name__)

_PROBE_MAX_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What a probe printed and how it ended."""

    returncode: int
    stdout: str
    stderr: str


async def run_probe(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout_seconds: float = 15.0,
) -> ProbeResult | None:
    """Run ``argv`` once with exactly ``env``; ``None`` when it could not start or did not finish in time.

    stdin is closed, so a CLI that would ask a question gets none; at the deadline the process and everything it
    started are killed before this returns.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
            **get_process_group_kwargs(),
        )
    except OSError:
        logger.debug("probe_not_started argv0=%s", argv[0] if argv else "", exc_info=True)
        return None

    async def stop_tree() -> None:
        kill_spawned_group(proc.pid, SIGKILL)

    try:
        output = await communicate_bounded(
            proc, timeout=timeout_seconds, on_abort=stop_tree, max_bytes=_PROBE_MAX_BYTES
        )
    except TimeoutError:
        logger.debug("probe_timed_out argv0=%s timeout=%ss", argv[0], timeout_seconds)
        return None
    return ProbeResult(
        returncode=proc.returncode or 0,
        stdout=output.stdout.decode("utf-8", errors="replace").strip(),
        stderr=output.stderr.decode("utf-8", errors="replace").strip(),
    )
