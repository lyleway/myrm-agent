"""Interactive subscription-login session for CLI agent backends.

Drives ``<cli> <login args>`` as a child process and turns its console output into
a structured :class:`AuthEvent` stream the business layer can relay over SSE: login
URLs and device codes surface as actionable PROMPT events, progress lines as STATUS,
and the terminal outcome as SUCCESS or ERROR. A login succeeded when the CLI exited
cleanly and says it is logged in (its own report, the credential file only for a CLI
that has no status command); one that failed ends with the CLI's own last words.
Backends whose login is not scriptable on this host yield a single PROMPT directing
the user to credential import.

[INPUT]
- toolkits.acp.auth._profiles::AuthProfile (POS: Authentication profile registry.)
- toolkits.acp.auth.credential_store::CredentialStore (POS: Credential persistence and detection.)
- toolkits.acp.auth.launch_env::cli_base_env (POS: The environment every launch of a CLI agent starts from)
- toolkits.acp.auth.login_state::resolve_login_state (POS: The login state a CLI reports about itself)
- utils.os_compat::get_process_group_kwargs, kill_process_group (POS: Cross-platform process group control.)

[OUTPUT]
- AuthEventType: Kinds of events emitted during a login session.
- AuthEvent: A single login progress/outcome event.
- CliLoginSession: Drives and observes an interactive CLI login.

[POS]
Interactive login session driver for the ACP auth subsystem.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from myrm_agent_harness.toolkits.acp.auth._profiles import AuthProfile
from myrm_agent_harness.toolkits.acp.auth.credential_store import CredentialStore
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.auth.login_state import resolve_login_state

logger = logging.getLogger(__name__)

# A CLI colours its output even when it writes to a pipe (Codex ignores NO_COLOR), and the escape sequences would end up
# inside the URL and the code it prints.
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
# The address of the CLI's own callback server ("Starting local login server on http://localhost:1455") is where the
# browser is sent back to, not a page for the user to open.
_LOOPBACK_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:[/?#]|$)", re.IGNORECASE
)
# Device codes are two groups of four or five characters (``ABCD-1234``, ``58T3-YFL83``).
_CODE_RE = re.compile(r"\b([A-Z0-9]{4,5}-[A-Z0-9]{4,5})\b")
_TERMINATE_GRACE_SECONDS = 5.0
_MAX_FAILURE_CHARS = 300


class AuthEventType(StrEnum):
    """Kinds of events emitted during a login session."""

    STATUS = "status"  # progress / informational line
    PROMPT = "prompt"  # actionable: carries a login URL and/or device code for the user
    SUCCESS = "success"  # login completed and the CLI reports a login
    ERROR = "error"  # login failed, or finished without the CLI reporting a login


@dataclass(frozen=True, slots=True)
class AuthEvent:
    """A single login progress or outcome event."""

    type: AuthEventType
    message: str
    url: str | None = None
    code: str | None = None


class CliLoginSession:
    """Drives an interactive CLI login and observes it to completion.

    Lifecycle: iterate :meth:`run` to receive the event stream; call :meth:`feed`
    to forward a user-supplied code to the CLI's stdin (for ``paste_code`` flows);
    call :meth:`cancel` to abort. A session instance drives a single login attempt.

    ``remote``: the browser that signs in is on another machine than the CLI, so the CLI is asked for the login that
    works from there (see ``AuthProfile.remote_login_args``).
    """

    def __init__(
        self,
        command: str,
        profile: AuthProfile,
        *,
        base_env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        timeout_seconds: int = 300,
        remote: bool = False,
    ) -> None:
        self._command = command
        self._profile = profile
        self._base_env: dict[str, str] = dict(base_env if base_env is not None else os.environ)
        self._cwd = cwd
        self._timeout = timeout_seconds
        self._remote = remote
        self._process: asyncio.subprocess.Process | None = None
        self._store = CredentialStore(self._base_env)

    async def run(self) -> AsyncIterator[AuthEvent]:
        """Spawn the login command and stream login events to completion."""
        if not self._profile.scriptable_login:
            yield AuthEvent(
                AuthEventType.PROMPT,
                message=(
                    f"{self._profile.backend} login is not scriptable here. Sign in on a machine "
                    "with a browser, then import the credential file to finish."
                ),
            )
            return

        from myrm_agent_harness.utils.os_compat import get_process_group_kwargs

        args = [self._command, *self._profile.login_command(remote=self._remote)]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._base_env,
                cwd=self._cwd,
                **get_process_group_kwargs(),
            )
        except (OSError, ValueError) as exc:
            yield AuthEvent(AuthEventType.ERROR, message=f"Failed to start login: {exc}")
            return

        yield AuthEvent(AuthEventType.STATUS, message=f"Starting {self._profile.backend} login…")

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        readers = [
            asyncio.create_task(_pump(self._process.stdout, queue)),
            asyncio.create_task(_pump(self._process.stderr, queue)),
        ]
        return_code: int | None = None
        last_words = ""
        try:
            async with asyncio.timeout(self._timeout):
                pending = len(readers)
                while pending > 0:
                    line = await queue.get()
                    if line is None:
                        pending -= 1
                        continue
                    event = _classify(line)
                    if event is not None:
                        if not self._is_code_prompt(event.message):
                            last_words = event.message
                        yield event
                return_code = await self._process.wait()
        except TimeoutError:
            await self._terminate()
            yield AuthEvent(AuthEventType.ERROR, message=f"Login timed out after {self._timeout}s")
            return
        finally:
            for task in readers:
                task.cancel()

        if return_code != 0:
            # What the CLI said last is the reason: a rejected code, a refused connection, a workspace that forbids it.
            yield AuthEvent(
                AuthEventType.ERROR,
                message=last_words[:_MAX_FAILURE_CHARS] or f"Login exited with code {return_code}",
            )
        elif await self._logged_in():
            yield AuthEvent(AuthEventType.SUCCESS, message=f"{self._profile.backend} login complete")
        else:
            yield AuthEvent(
                AuthEventType.ERROR,
                message=f"Login finished but {self._profile.backend} reports no login; try credential import.",
            )

    async def feed(self, text: str) -> None:
        """Forward a user-supplied line (e.g. a pasted auth code) to the CLI's stdin."""
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            return
        try:
            proc.stdin.write((text.rstrip("\n") + "\n").encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("login_feed_failed backend=%s", self._profile.backend, exc_info=True)

    async def cancel(self) -> None:
        """Abort an in-progress login."""
        await self._terminate()

    def _is_code_prompt(self, message: str) -> bool:
        """Whether ``message`` is the CLI asking for the pasted code, which it also does after refusing one."""
        return bool(self._profile.code_prompt) and self._profile.code_prompt in message

    async def _logged_in(self) -> bool:
        """Whether the CLI says it is logged in now, asked the way a delegated turn would see it."""
        state = await resolve_login_state(
            self._store,
            self._profile.backend,
            executable=self._command,
            env=cli_base_env(self._command, self._base_env),
        )
        return state.authenticated

    async def _terminate(self) -> None:
        """Terminate the login process group, escalating SIGTERM → SIGKILL."""
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        from myrm_agent_harness.utils.os_compat import SIGKILL, kill_process_group

        pid = proc.pid
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if pid is not None:
                kill_process_group(pid, signal.SIGTERM)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if pid is not None:
                    kill_process_group(pid, SIGKILL)


def _classify(line: str) -> AuthEvent | None:
    """Map a console line to an AuthEvent, surfacing login URLs and device codes."""
    stripped = _ANSI_RE.sub("", line).strip()
    if not stripped:
        return None
    url = _login_link(stripped)
    # The URL's query string holds upper-case runs of its own, so a code is looked for outside it.
    code_match = _CODE_RE.search(_URL_RE.sub("", stripped))
    if url or code_match:
        return AuthEvent(
            AuthEventType.PROMPT,
            message=stripped,
            url=url,
            code=code_match.group(1) if code_match else None,
        )
    return AuthEvent(AuthEventType.STATUS, message=stripped)


def _login_link(text: str) -> str | None:
    """The first URL in ``text`` that the user is meant to open."""
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,);]")
        if not _LOOPBACK_URL_RE.match(url):
            return url
    return None


async def _pump(stream: asyncio.StreamReader | None, queue: asyncio.Queue[str | None]) -> None:
    """Forward decoded lines from a stream into ``queue``; enqueue ``None`` at EOF."""
    if stream is None:
        await queue.put(None)
        return
    try:
        async for raw in stream:
            await queue.put(raw.decode("utf-8", errors="replace").rstrip("\n"))
    finally:
        await queue.put(None)
