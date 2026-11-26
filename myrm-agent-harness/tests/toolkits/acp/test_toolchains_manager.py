"""IsolatedToolchainManager: an install ends in a CLI that starts or in a ToolchainError — never in a false success.

Real processes, a real local HTTP server and the real kernel lock stand in for the network, npm and a second
installer, so what is asserted is what a user would observe.
"""

from __future__ import annotations

import asyncio
import http.server
import io
import os
import platform
import subprocess
import sys
import tarfile
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.toolchains import manager as manager_module
from myrm_agent_harness.toolkits.acp.toolchains.manager import (
    BACKEND_NPM_MAP,
    NODE_VERSION,
    IsolatedToolchainManager,
    ToolchainError,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="isolated toolchains are POSIX only")


async def _run(manager: IsolatedToolchainManager, backend: str = "claude") -> list[str]:
    return [line async for line in manager.install_backend(backend)]


def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    path.chmod(0o755)


@pytest.fixture
def manager(tmp_path: Path) -> IsolatedToolchainManager:
    """A manager whose Node.js is already in place, so only npm and what follows it is under test."""
    toolchain = IsolatedToolchainManager(tmp_path / "toolchain")
    (toolchain.node_dir / "bin").mkdir(parents=True)
    (toolchain.node_dir / "bin" / "node").touch()
    return toolchain


def _fake_npm(manager: IsolatedToolchainManager, body: str) -> None:
    _write_script(manager.node_dir / "bin" / "npm", body)


# A fake `npm install -g <pkg> --prefix <dir>`: puts a working CLI at <dir>/bin/<name of the package>.
_INSTALLS_CLI = """
    prefix=""; name=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --prefix) prefix="$2"; shift ;;
        -g|install) ;;
        *) name="${1##*/}"; name="${name%%-code}"; name="${name%%-cli}" ;;
      esac
      shift
    done
    mkdir -p "$prefix/bin"
    printf '#!/bin/sh\\necho "1.2.3 (fake)"\\n' > "$prefix/bin/$name"
    chmod +x "$prefix/bin/$name"
    echo "added 1 package"
"""


class TestCatalog:
    def test_known_backends_use_official_packages(self) -> None:
        assert BACKEND_NPM_MAP["claude"] == "@anthropic-ai/claude-code"
        assert BACKEND_NPM_MAP["codex"] == "@openai/codex"
        assert BACKEND_NPM_MAP["gemini"] == "@google/gemini-cli"
        assert BACKEND_NPM_MAP["qwen"] == "@qwen-code/qwen-code"

    def test_the_managed_node_honours_the_system_trust_store(self) -> None:
        # Node reads NODE_USE_SYSTEM_CA from 22.19 and, on the 24 line the managed Node is on, from 24.6.
        major, minor = (int(part) for part in NODE_VERSION.removeprefix("v").split(".")[:2])
        assert (major, minor) >= (24, 6)

    @pytest.mark.parametrize(
        ("system", "machine", "expected"),
        [
            ("Darwin", "arm64", "darwin-arm64"),
            ("Darwin", "x86_64", "darwin-x64"),
            ("Linux", "x86_64", "linux-x64"),
            ("Linux", "aarch64", "linux-arm64"),
        ],
    )
    def test_download_url_per_platform(
        self,
        monkeypatch: pytest.MonkeyPatch,
        manager: IsolatedToolchainManager,
        system: str,
        machine: str,
        expected: str,
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: system)
        monkeypatch.setattr(platform, "machine", lambda: machine)
        assert manager._get_node_download_url() == (
            f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-{expected}.tar.gz"
        )

    def test_an_unsupported_os_is_a_toolchain_error(
        self, monkeypatch: pytest.MonkeyPatch, manager: IsolatedToolchainManager
    ) -> None:
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        with pytest.raises(ToolchainError, match="Unsupported OS"):
            manager._get_node_download_url()


class TestInstallLock:
    async def test_a_second_install_waits_for_the_first_and_then_proceeds(self, tmp_path: Path) -> None:
        path = tmp_path / ".install.lock"
        first = manager_module._InstallLock(path, wait_seconds=30)
        second = manager_module._InstallLock(path, wait_seconds=30)
        assert [n async for n in first.acquire()] == []

        notices: list[str] = []

        async def waiter() -> None:
            async for notice in second.acquire():
                notices.append(notice)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.3)
        assert not task.done()
        assert notices == ["Waiting for another installation to finish..."]

        first.release()
        await asyncio.wait_for(task, timeout=5)
        second.release()

    async def test_the_wait_is_bounded(self, tmp_path: Path) -> None:
        path = tmp_path / ".install.lock"
        holder = manager_module._InstallLock(path, wait_seconds=30)
        _ = [n async for n in holder.acquire()]
        waiter = manager_module._InstallLock(path, wait_seconds=0.4)
        with pytest.raises(ToolchainError, match="Another installation has been running"):
            _ = [n async for n in waiter.acquire()]
        holder.release()

    async def test_a_killed_installer_leaves_no_lock_behind(self, tmp_path: Path) -> None:
        path = tmp_path / ".install.lock"
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import fcntl, os, sys, time\n"
                f"fd = os.open({str(path)!r}, os.O_CREAT | os.O_RDWR)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "print('locked', flush=True)\n"
                "time.sleep(60)\n",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "locked"
            blocked = manager_module._InstallLock(path, wait_seconds=0.3)
            with pytest.raises(ToolchainError):
                _ = [n async for n in blocked.acquire()]

            holder.kill()
            holder.wait(timeout=10)

            free = manager_module._InstallLock(path, wait_seconds=5)
            assert [n async for n in free.acquire()] == []
            free.release()
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)


class _NodeArchiveHandler(http.server.BaseHTTPRequestHandler):
    """Serves what the test sets on the class: an archive, an error status, or silence."""

    mode = "archive"
    payload = b""

    def do_GET(self) -> None:
        if self.mode == "missing":
            self.send_error(404)
        elif self.mode == "stall":
            threading.Event().wait(5)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)

    def log_message(self, *_: object) -> None:
        return


@pytest.fixture
def node_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_NodeArchiveHandler]]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _NodeArchiveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/node.tar.gz"
    monkeypatch.setattr(IsolatedToolchainManager, "_get_node_download_url", lambda self: url)
    _NodeArchiveHandler.mode = "archive"
    _NodeArchiveHandler.payload = b""
    yield _NodeArchiveHandler
    server.shutdown()
    server.server_close()


def _node_archive(folder: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in (("bin/node", b"#!/bin/sh\n"), ("bin/npm", b"#!/bin/sh\n")):
            info = tarfile.TarInfo(name=f"{folder}/{name}")
            info.size = len(content)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class TestNodeDownload:
    async def test_skips_when_node_is_already_present(self, manager: IsolatedToolchainManager) -> None:
        assert [m async for m in manager._ensure_node()] == ["Node.js environment already exists."]

    async def test_downloads_and_extracts_into_the_node_dir(
        self, tmp_path: Path, node_server: type[_NodeArchiveHandler]
    ) -> None:
        toolchain = IsolatedToolchainManager(tmp_path / "fresh")
        node_server.payload = _node_archive("node-vX-os-arch")

        messages = [m async for m in toolchain._ensure_node()]

        assert messages[-1] == "Node.js environment setup complete."
        assert (toolchain.node_dir / "bin" / "node").is_file()
        assert not (toolchain.base_dir / "node.tar.gz").exists()

    async def test_an_http_error_names_the_url_and_the_status(
        self, tmp_path: Path, node_server: type[_NodeArchiveHandler]
    ) -> None:
        toolchain = IsolatedToolchainManager(tmp_path / "fresh")
        node_server.mode = "missing"
        with pytest.raises(ToolchainError, match=r"answered HTTP 404"):
            _ = [m async for m in toolchain._ensure_node()]

    async def test_a_stalled_connection_gives_up(
        self, tmp_path: Path, node_server: type[_NodeArchiveHandler], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        toolchain = IsolatedToolchainManager(tmp_path / "fresh")
        node_server.mode = "stall"
        monkeypatch.setattr(manager_module, "DOWNLOAD_IDLE_SECONDS", 0.4)
        with pytest.raises(ToolchainError, match=r"Could not download Node\.js"):
            _ = [m async for m in toolchain._ensure_node()]
        assert not (toolchain.base_dir / "node.tar.gz").exists()

    async def test_a_damaged_archive_is_reported_and_cleaned_up(
        self, tmp_path: Path, node_server: type[_NodeArchiveHandler]
    ) -> None:
        toolchain = IsolatedToolchainManager(tmp_path / "fresh")
        node_server.payload = b"this is not a tarball"
        with pytest.raises(ToolchainError, match="damaged"):
            _ = [m async for m in toolchain._ensure_node()]
        assert not (toolchain.base_dir / "node.tar.gz").exists()

    async def test_only_http_and_https_urls_are_fetched(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toolchain = IsolatedToolchainManager(tmp_path / "fresh")
        monkeypatch.setattr(IsolatedToolchainManager, "_get_node_download_url", lambda self: "file:///etc/passwd")
        with pytest.raises(ToolchainError, match="only http and https"):
            _ = [m async for m in toolchain._ensure_node()]


class TestNpmInstall:
    async def test_a_cli_that_starts_is_a_success_that_names_its_version(
        self, manager: IsolatedToolchainManager
    ) -> None:
        _fake_npm(manager, _INSTALLS_CLI)

        messages = await _run(manager)

        assert messages[-1] == "Successfully installed claude 1.2.3 (fake)"
        assert "added 1 package" in messages

    async def test_unknown_backend_is_an_error_not_a_success(self, manager: IsolatedToolchainManager) -> None:
        with pytest.raises(ToolchainError, match="Unknown backend 'not-a-backend'"):
            await _run(manager, "not-a-backend")

    async def test_npm_failure_carries_the_exit_code_and_what_npm_said(self, manager: IsolatedToolchainManager) -> None:
        _fake_npm(manager, 'echo "npm ERR! code ENOTFOUND"; echo "npm ERR! network request failed"; exit 3\n')

        with pytest.raises(ToolchainError) as raised:
            await _run(manager)

        assert "exit" in str(raised.value)
        assert "3" in str(raised.value)
        assert "npm ERR! network request failed" in str(raised.value)

    async def test_npm_that_exits_zero_without_installing_is_not_a_success(
        self, manager: IsolatedToolchainManager
    ) -> None:
        _fake_npm(manager, "echo done\n")
        with pytest.raises(ToolchainError, match="claude is not at"):
            await _run(manager)

    async def test_an_installed_cli_that_cannot_start_is_not_a_success(self, manager: IsolatedToolchainManager) -> None:
        _fake_npm(
            manager,
            """
            prefix=""
            while [ $# -gt 0 ]; do [ "$1" = "--prefix" ] && prefix="$2"; shift; done
            mkdir -p "$prefix/bin"
            printf '#!/bin/sh\\necho "missing platform binary" >&2\\nexit 7\\n' > "$prefix/bin/claude"
            chmod +x "$prefix/bin/claude"
            """,
        )
        with pytest.raises(ToolchainError) as raised:
            await _run(manager)
        assert "cannot start" in str(raised.value)
        assert "missing platform binary" in str(raised.value)

    async def test_an_installed_cli_that_never_answers_version_is_not_a_success_and_is_stopped(
        self, manager: IsolatedToolchainManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        pid_file = tmp_path / "version-pid.txt"
        monkeypatch.setattr(manager_module, "VERSION_CHECK_SECONDS", 0.5)
        _fake_npm(
            manager,
            f"""
            prefix=""
            while [ $# -gt 0 ]; do [ "$1" = "--prefix" ] && prefix="$2"; shift; done
            mkdir -p "$prefix/bin"
            printf '#!/bin/sh\\necho $$ > {pid_file}\\nexec sleep 30\\n' > "$prefix/bin/claude"
            chmod +x "$prefix/bin/claude"
            """,
        )

        with pytest.raises(ToolchainError, match="did not answer --version"):
            await _run(manager)

        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    async def test_a_cli_that_floods_its_version_output_still_names_its_first_line(
        self, manager: IsolatedToolchainManager
    ) -> None:
        _fake_npm(
            manager,
            """
            prefix=""
            while [ $# -gt 0 ]; do [ "$1" = "--prefix" ] && prefix="$2"; shift; done
            mkdir -p "$prefix/bin"
            printf '#!/bin/sh\\necho "1.2.3 (loud)"\\nyes x | head -c 3000000\\n' > "$prefix/bin/claude"
            chmod +x "$prefix/bin/claude"
            """,
        )

        messages = await _run(manager)

        assert messages[-1] == "Successfully installed claude 1.2.3 (loud)"

    async def test_missing_npm_is_reported(self, manager: IsolatedToolchainManager) -> None:
        with pytest.raises(ToolchainError, match="no npm"):
            await _run(manager)

    async def test_npm_follows_the_users_registry_and_inherits_no_secrets(
        self, manager: IsolatedToolchainManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen = tmp_path / "seen.txt"
        monkeypatch.setenv("npm_config_registry", "https://registry.example.test/")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-reach-npm")
        monkeypatch.setenv("MYRM_TLS_STRICT", "0")
        monkeypatch.delenv("NODE_USE_SYSTEM_CA", raising=False)
        _fake_npm(
            manager,
            f"""
            {{ echo "ARGV $*"; env; }} > {seen}
            prefix=""
            while [ $# -gt 0 ]; do [ "$1" = "--prefix" ] && prefix="$2"; shift; done
            mkdir -p "$prefix/bin"
            printf '#!/bin/sh\\necho 9.9.9\\n' > "$prefix/bin/claude"
            chmod +x "$prefix/bin/claude"
            """,
        )

        await _run(manager)

        report = seen.read_text()
        assert "--registry" not in report
        assert "registry.npmmirror.com" not in report
        assert "npm_config_registry=https://registry.example.test/" in report
        assert "sk-must-not-reach-npm" not in report
        assert "NODE_USE_SYSTEM_CA=1" in report
        path_line = next(line for line in report.splitlines() if line.startswith("PATH="))
        assert path_line.startswith(f"PATH={manager.node_dir / 'bin'}{os.pathsep}")

    async def test_a_hung_npm_is_stopped_with_everything_it_started(
        self, manager: IsolatedToolchainManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        pidfile = tmp_path / "child.pid"
        monkeypatch.setattr(manager_module, "NPM_INSTALL_SECONDS", 4)
        _fake_npm(manager, f"sleep 300 &\necho $! > {pidfile}\necho started\nwait\n")

        with pytest.raises(ToolchainError, match="did not finish"):
            await _run(manager)

        assert not _alive(int(pidfile.read_text()))

    async def test_closing_the_stream_stops_npm(self, manager: IsolatedToolchainManager, tmp_path: Path) -> None:
        pidfile = tmp_path / "npm.pid"
        _fake_npm(manager, f"echo $$ > {pidfile}\necho started\nsleep 300\n")

        stream = manager.install_backend("claude")
        async for line in stream:
            if line == "started":
                break
        await stream.aclose()

        assert not _alive(int(pidfile.read_text()))

    async def test_the_install_lock_is_released_after_a_failure(self, manager: IsolatedToolchainManager) -> None:
        _fake_npm(manager, "exit 1\n")
        with pytest.raises(ToolchainError):
            await _run(manager)

        free = manager_module._InstallLock(manager.base_dir / ".install.lock", wait_seconds=1)
        assert [n async for n in free.acquire()] == []
        free.release()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
