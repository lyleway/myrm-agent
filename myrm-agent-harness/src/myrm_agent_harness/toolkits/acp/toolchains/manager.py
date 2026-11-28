"""Isolated Toolchain Manager for external CLI agents.

Downloads a portable Node.js and installs CLI agents with it into a private directory, so a delegated CLI never
touches the host's global npm. An install either leaves a CLI that starts or raises ``ToolchainError``: nothing here
reports success it did not observe, waits without a bound, or leaves a child process behind.

[INPUT]
- core.security.bounded_communicate::communicate_bounded (POS: communicate() with a cap on each pipe, stopping the process through the caller on a deadline or a cancel)
- core.security.process_env::EnvInheritPolicy, sanitize_env (POS: Child process environment isolation SSOT)
- infra.tls_compat::with_node_system_ca (POS: Enterprise TLS compatibility: the system trust store for a Node child)
- toolkits.acp.drivers::command_driver, known_backends (POS: The contract of every known agent: its login, install, launch, transport and permission facts)
- utils.os_compat::SIGKILL, get_process_group_kwargs, kill_spawned_group (POS: Unified abstractions for POSIX/Windows differences (file locks, process groups))

[OUTPUT]
- TOOLCHAIN_DIR_ENV: The environment variable a host sets to move the toolchain directory.
- toolchain_base_dir: Where the isolated toolchain lives, read on every call.
- ToolchainError: An install step failed; the message says what and why.
- toolchain_node_bin: The managed Node a CLI installed by this manager needs on PATH.
- IsolatedToolchainManager: Downloads portable environments and installs CLI tools.

[POS]
Isolated toolchain manager for external CLI agents — keeps tool installations
out of the global OS environment. Which package installs an agent is a fact of its ``DriverSpec``, not of this module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path

from myrm_agent_harness.core.security.bounded_communicate import communicate_bounded
from myrm_agent_harness.core.security.process_env import EnvInheritPolicy, sanitize_env
from myrm_agent_harness.infra.tls_compat import with_node_system_ca
from myrm_agent_harness.toolkits.acp.drivers import command_driver, known_backends
from myrm_agent_harness.utils.os_compat import SIGKILL, get_process_group_kwargs, kill_spawned_group

logger = logging.getLogger(__name__)

TOOLCHAIN_DIR_ENV = "MYRM_TOOLCHAIN_DIR"
# Node 24.6 and later read NODE_USE_SYSTEM_CA, which npm needs behind a TLS-inspection proxy.
NODE_VERSION = "v24.22.2"

# How long a second install waits for the one in progress before it says so and stops.
LOCK_WAIT_SECONDS = 600.0
# A download that delivers nothing for this long is dead; one that keeps delivering still ends at the total.
DOWNLOAD_IDLE_SECONDS = 60.0
DOWNLOAD_TOTAL_SECONDS = 900.0
NPM_INSTALL_SECONDS = 900.0
VERSION_CHECK_SECONDS = 30.0
# What a CLI's ``--version`` may print before the rest is dropped; the first line is all that is used.
_VERSION_OUTPUT_BYTES = 64 * 1024
_LOCK_NOTICE_SECONDS = 30.0
_OUTPUT_TAIL_LINES = 8
_TERMINATE_GRACE_SECONDS = 5.0


def toolchain_base_dir() -> Path:
    """Where CLI agents are installed: ``$MYRM_TOOLCHAIN_DIR`` when a host names one, else ``~/.myrm-agent/toolchains``.

    Read on every call, never at import: a host that sets it after the harness is imported still moves the
    installer, the detector and the launch environment to the same directory. A relative name is taken from the
    working directory of the call, so every consumer compares absolute paths.
    """
    named = os.environ.get(TOOLCHAIN_DIR_ENV, "").strip()
    if named:
        return Path(named).expanduser().absolute()
    return Path.home() / ".myrm-agent" / "toolchains"


def toolchain_node_bin(command: str) -> Path | None:
    """The managed Node's ``bin`` directory when ``command`` is a CLI this manager installed.

    Such a CLI starts with ``#!/usr/bin/env node``; on a host that has no Node of its own it runs only with this one
    on ``PATH``. Any other command (a system install, a bare name) needs nothing added, so it gets ``None``.
    """
    base_dir = toolchain_base_dir()
    if not os.path.isabs(command) or not Path(command).is_relative_to(base_dir):
        return None
    node_bin = base_dir / f"node-{NODE_VERSION}" / "bin"
    return node_bin if node_bin.is_dir() else None


class ToolchainError(RuntimeError):
    """An install step failed. The message says what happened and why, ready to show to the user."""


class _InstallLock:
    """One install at a time per toolchain directory.

    An advisory ``flock`` on a lock file: the kernel drops it with the process that holds it, so a crashed or killed
    installer can never leave a lock behind that blocks every later install.
    """

    def __init__(self, path: Path, *, wait_seconds: float) -> None:
        self._path = path
        self._wait_seconds = wait_seconds
        self._fd: int | None = None

    async def acquire(self) -> AsyncGenerator[str]:
        """Yield a notice while another install holds the lock; return once this one holds it."""
        import fcntl

        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        started = time.monotonic()
        noticed = float("-inf")
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    return
                except BlockingIOError:
                    waited = time.monotonic() - started
                    if waited >= self._wait_seconds:
                        msg = (
                            f"Another installation has been running for over {int(self._wait_seconds // 60)} minutes. "
                            "Wait for it to finish, then try again."
                        )
                        raise ToolchainError(msg) from None
                    if waited - noticed >= _LOCK_NOTICE_SECONDS:
                        noticed = waited
                        yield "Waiting for another installation to finish..."
                    await asyncio.sleep(0.5)
        except BaseException:
            os.close(fd)
            raise

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor releases the flock
            self._fd = None


class IsolatedToolchainManager:
    """Manages isolated toolchains for external CLI agents."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or toolchain_base_dir()
        self.node_dir = self.base_dir / f"node-{NODE_VERSION}"
        self.bin_dir = self.base_dir / "bin"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir.mkdir(parents=True, exist_ok=True)

    def _get_node_download_url(self) -> str:
        """Determine the correct Node.js binary URL for the current OS/Arch."""
        system = platform.system().lower()
        machine = platform.machine().lower()

        if system == "darwin":
            os_name = "darwin"
            arch = "arm64" if machine == "arm64" else "x64"
        elif system == "linux":
            os_name = "linux"
            arch = "arm64" if "aarch64" in machine or "arm64" in machine else "x64"
        else:
            msg = f"Unsupported OS for isolated toolchain: {system}. Install the CLI yourself; Myrm detects it on PATH."
            raise ToolchainError(msg)

        return f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-{os_name}-{arch}.tar.gz"

    async def install_backend(self, backend_name: str) -> AsyncGenerator[str]:
        """Install a backend CLI, yielding progress lines.

        Returns normally only when the CLI is on disk and answers ``--version``; every other outcome raises
        ``ToolchainError``.
        """
        spec = command_driver(backend_name)
        # The name is also the file name under ``bin``, so only the exact backend name is accepted, never a path or an
        # extension that names the same agent.
        if spec is None or spec.backend != backend_name:
            msg = f"Unknown backend {backend_name!r}. Known: {', '.join(sorted(known_backends()))}."
            raise ToolchainError(msg)
        self._get_node_download_url()  # an unsupported OS ends here, before the lock is touched

        lock = _InstallLock(self.base_dir / ".install.lock", wait_seconds=LOCK_WAIT_SECONDS)
        # ``aclosing`` ends each step the moment this stream stops, so a client that went away stops npm with it.
        async with aclosing(lock.acquire()) as notices:
            async for notice in notices:
                yield notice
        try:
            async with aclosing(self._ensure_node()) as node_steps:
                async for msg in node_steps:
                    yield msg
            async with aclosing(self._npm_install(spec.npm_package)) as npm_lines:
                async for line in npm_lines:
                    yield line
            yield await self._verify_installed(backend_name)
        finally:
            lock.release()

    async def _ensure_node(self) -> AsyncGenerator[str]:
        """Download and extract the portable Node.js unless it is already there."""
        if (self.node_dir / "bin" / "node").exists():
            yield "Node.js environment already exists."
            return

        url = self._get_node_download_url()
        tar_path = self.base_dir / "node.tar.gz"
        yield f"Downloading Node.js {NODE_VERSION}..."
        cancelled = threading.Event()
        try:
            try:
                await asyncio.to_thread(_download, url, tar_path, cancelled)
            except asyncio.CancelledError:
                cancelled.set()  # the thread stops at its next chunk instead of finishing a download nobody awaits
                raise
            yield "Extracting Node.js..."
            await asyncio.to_thread(self._extract_node, tar_path)
            yield "Node.js environment setup complete."
        finally:
            tar_path.unlink(missing_ok=True)

    def _extract_node(self, tar_path: Path) -> None:
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                names = tar.getnames()
                if not names:
                    raise tarfile.ReadError("the archive is empty")
                tar.extractall(path=self.base_dir, filter="data")
                extracted_path = self.base_dir / names[0].split("/")[0]
        except (tarfile.TarError, EOFError, OSError) as exc:
            msg = f"The downloaded Node.js archive is damaged ({exc}). Try again."
            raise ToolchainError(msg) from exc
        if extracted_path != self.node_dir:
            if self.node_dir.exists():
                shutil.rmtree(self.node_dir)
            extracted_path.rename(self.node_dir)

    def _npm_env(self) -> dict[str, str]:
        """The environment of npm: the core variables, the user's own npm settings, and the isolated Node first.

        npm reads its registry and proxy from ``~/.npmrc`` and ``npm_config_*`` variables, so an install follows the
        user's network setup (a corporate or regional mirror included) without this module knowing about any.
        """
        env = sanitize_env(dict(os.environ), EnvInheritPolicy.CORE)
        env.update({key: value for key, value in os.environ.items() if key.lower().startswith("npm_config_")})
        env = with_node_system_ca(env)
        env["PATH"] = f"{self.node_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
        return env

    async def _npm_install(self, package_name: str) -> AsyncGenerator[str]:
        npm_path = self.node_dir / "bin" / "npm"
        if not npm_path.exists():
            msg = f"The downloaded Node.js is incomplete (no npm). Remove {self.node_dir} and try again."
            raise ToolchainError(msg)

        yield f"Installing {package_name} via npm..."
        cmd = [str(npm_path), "install", "-g", package_name, "--prefix", str(self.base_dir)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._npm_env(),
                **get_process_group_kwargs(),
            )
        except OSError as exc:
            msg = f"Could not start npm: {exc}"
            raise ToolchainError(msg) from exc

        tail: deque[str] = deque(maxlen=_OUTPUT_TAIL_LINES)
        try:
            async with asyncio.timeout(NPM_INSTALL_SECONDS):
                assert proc.stdout is not None  # PIPE above
                while line := await proc.stdout.readline():
                    text = line.decode("utf-8", errors="replace").strip()
                    tail.append(text)
                    yield text
                returncode = await proc.wait()
        except TimeoutError:
            msg = f"npm did not finish within {int(NPM_INSTALL_SECONDS // 60)} minutes and was stopped."
            raise ToolchainError(msg) from None
        finally:
            await _stop_group(proc)

        if returncode != 0:
            details = "\n".join(line for line in tail if line)
            msg = f"npm exited with code {returncode}."
            raise ToolchainError(f"{msg}\n{details}" if details else msg)

    async def _verify_installed(self, backend_name: str) -> str:
        """The success line, once the installed CLI has been seen to start."""
        binary = self.bin_dir / backend_name
        if not binary.is_file() or not os.access(binary, os.X_OK):
            msg = f"npm finished, but {backend_name} is not at {binary}."
            raise ToolchainError(msg)
        try:
            proc = await asyncio.create_subprocess_exec(
                str(binary),
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._npm_env(),
                **get_process_group_kwargs(),
            )
        except OSError as exc:
            msg = f"{backend_name} was installed but cannot start: {exc}"
            raise ToolchainError(msg) from exc
        try:
            output = await communicate_bounded(
                proc,
                timeout=VERSION_CHECK_SECONDS,
                on_abort=lambda: _stop_group(proc),
                max_bytes=_VERSION_OUTPUT_BYTES,
            )
        except TimeoutError:
            msg = f"{backend_name} was installed but did not answer --version within {int(VERSION_CHECK_SECONDS)}s."
            raise ToolchainError(msg) from None
        finally:
            await _stop_group(proc)
        if proc.returncode != 0:
            detail = output.stderr.decode("utf-8", errors="replace").strip().splitlines()[-_OUTPUT_TAIL_LINES:]
            msg = f"{backend_name} was installed but cannot start (exit code {proc.returncode})."
            raise ToolchainError("\n".join([msg, *detail]))
        version = output.stdout.decode("utf-8", errors="replace").strip().splitlines()
        return f"Successfully installed {backend_name} {version[0] if version else ''}".strip()


def _download(url: str, destination: Path, cancelled: threading.Event) -> None:
    """Fetch ``url`` to ``destination``, giving up on a stalled connection or an unreasonably slow one."""
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        msg = f"Refusing to download Node.js from {url}: only http and https are allowed."
        raise ToolchainError(msg)
    deadline = time.monotonic() + DOWNLOAD_TOTAL_SECONDS
    request = urllib.request.Request(url, headers={"User-Agent": "myrm-agent-harness"})  # noqa: S310 - scheme checked above
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_IDLE_SECONDS) as response,  # noqa: S310
            destination.open("wb") as out,
        ):
            while chunk := response.read(1 << 20):
                if cancelled.is_set():
                    return
                if time.monotonic() > deadline:
                    msg = f"Downloading Node.js took longer than {int(DOWNLOAD_TOTAL_SECONDS // 60)} minutes."
                    raise ToolchainError(msg)
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        msg = f"Could not download Node.js: {url} answered HTTP {exc.code}."
        raise ToolchainError(msg) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        msg = f"Could not download Node.js from {url}: {exc}"
        raise ToolchainError(msg) from exc


async def _stop_group(proc: asyncio.subprocess.Process) -> None:
    """End ``proc`` and everything it started, whatever state the caller left it in."""
    if proc.returncode is not None:
        return
    kill_spawned_group(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        kill_spawned_group(proc.pid, SIGKILL)
        await proc.wait()
