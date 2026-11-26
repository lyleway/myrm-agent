"""Real-wire integration tests for plugin-root env injection (``placeholders``).

These spin up a *real* stdio MCP server subprocess and drive it through the
production connection pool — no mocks on the transport. They verify, against a
real subprocess boundary, every runtime behavior the Agent Plugins 1.0.0 spec
mandates for bundled servers:

- configured plugin roots are injected as ``PLUGIN_ROOT`` / ``PLUGIN_DATA`` (§9.1);
- without configured roots nothing is injected (the vars stay unset);
- empty-string roots are treated as unconfigured (no bogus ``""`` injection);
- partial injection: each reserved var is injected independently;
- ``${PLUGIN_ROOT}`` / ``${PLUGIN_DATA}`` placeholders expand in ``cwd``, ``args``
  and ``env`` values at spawn time;
- a ``./``-relative ``command`` runs from the plugin root even without an
  explicit ``cwd`` (§7.2.1);
- injection variables in the server's ``env`` (a linker preload, a Python path, git's
  ``GIT_CONFIG_*`` group) are stripped at launch and never reach the child;
- the enterprise network compatibility setting reaches the child as
  ``NODE_USE_SYSTEM_CA=1`` (unset while strict verification is on, and a value
  the server config sets itself is kept).

This is the wire-level complement to the pure unit tests in
``test_placeholders.py``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.mcp.config import MCPConfig
from myrm_agent_harness.toolkits.mcp.connection_manager import MCPConnectionManager

# A real MCP server that reports the runtime environment it actually received:
# the reserved plugin vars, a custom env var, the names of all its variables,
# the working directory, and argv.
_ENV_PROBE_SERVER_SRC = """
import json
import os
import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("env-probe")


@server.tool()
def probe() -> str:
    return json.dumps({
        "PLUGIN_ROOT": os.environ.get("PLUGIN_ROOT", "UNSET"),
        "PLUGIN_DATA": os.environ.get("PLUGIN_DATA", "UNSET"),
        "CUSTOM_ENV": os.environ.get("CUSTOM_ENV", "UNSET"),
        "NODE_USE_SYSTEM_CA": os.environ.get("NODE_USE_SYSTEM_CA", "UNSET"),
        "ENV_NAMES": sorted(os.environ),
        "CWD": os.getcwd(),
        "ARGV": list(sys.argv),
    })


if __name__ == "__main__":
    server.run(transport="stdio")
"""


# What the probe server reports it received: strings, and ``ARGV`` and ``ENV_NAMES`` as lists.
ProbeReport = dict[str, str | list[str]]

# Names that make a later program run what the value names; none of them may reach a child.
_INJECTED_ENV = {
    "LD_PRELOAD": "/tmp/rootkit.so",
    "PYTHONPATH": "/tmp/evil_lib",
    "node_options": "--inspect",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.sshCommand",
    "GIT_CONFIG_VALUE_0": "/tmp/hook.sh",
    "GIT_CONFIG_GLOBAL": "/tmp/evil.cfg",
}


@pytest.fixture
def _reset_manager() -> Iterator[None]:
    MCPConnectionManager._instance = None
    yield
    MCPConnectionManager._instance = None


def _make_roots(tmp_path: Path) -> tuple[str, str]:
    plugin_root = tmp_path / "plugin_root"
    data_root = tmp_path / "plugin_data"
    plugin_root.mkdir()
    data_root.mkdir()
    return str(plugin_root), str(data_root)


def _write_probe_script(tmp_path: Path) -> str:
    script = tmp_path / "env_probe_server.py"
    script.write_text(_ENV_PROBE_SERVER_SRC, encoding="utf-8")
    return str(script)


def _extract_json(raw: str) -> ProbeReport:
    """The call result wraps the tool output in a security notice; the first
    line after the ``---`` separator is the raw tool JSON payload."""
    body = raw.split("---\n", 1)[1]
    report: ProbeReport = json.loads(body.splitlines()[0])
    return report


async def _probe(
    command: str,
    args: list[str],
    extra_params: dict[str, object] | None,
) -> ProbeReport:
    cfg = MCPConfig(
        name="envprobe",
        type="stdio",
        command=command,
        args=args,
        description="env probe",
        extra_params=extra_params,
        connect_timeout=30.0,
    )
    manager = await MCPConnectionManager.get_instance()
    try:
        conn = await manager.get_connection([cfg])
        return _extract_json(str(await conn.call("envprobe", "probe", {})))
    finally:
        await manager.stop()


@pytest.mark.integration
async def test_stdio_injects_plugin_roots_into_subprocess_env(tmp_path: Path, _reset_manager: None) -> None:
    """Configured plugin roots must reach the subprocess as reserved vars (§9.1)."""
    plugin_root, data_root = _make_roots(tmp_path)

    report = await _probe(
        sys.executable,
        [_write_probe_script(tmp_path)],
        {"plugin_root": plugin_root, "data_root": data_root},
    )
    assert report["PLUGIN_ROOT"] == plugin_root
    assert report["PLUGIN_DATA"] == data_root


@pytest.mark.integration
async def test_stdio_skips_injection_without_configured_roots(tmp_path: Path, _reset_manager: None) -> None:
    """Without configured roots the reserved vars must stay unset in the subprocess."""
    report = await _probe(sys.executable, [_write_probe_script(tmp_path)], None)
    assert report["PLUGIN_ROOT"] == "UNSET"
    assert report["PLUGIN_DATA"] == "UNSET"


@pytest.mark.integration
async def test_stdio_treats_empty_string_root_as_unconfigured(tmp_path: Path, _reset_manager: None) -> None:
    """Empty-string roots must not inject bogus ``""`` values (regression guard)."""
    _plugin_root, data_root = _make_roots(tmp_path)

    report = await _probe(
        sys.executable,
        [_write_probe_script(tmp_path)],
        {"plugin_root": "", "data_root": data_root},
    )
    assert report["PLUGIN_ROOT"] == "UNSET"
    assert report["PLUGIN_DATA"] == data_root


@pytest.mark.integration
async def test_stdio_injects_each_reserved_var_independently(tmp_path: Path, _reset_manager: None) -> None:
    """Partial configuration: only the configured root is injected (§9.1 per-var)."""
    plugin_root, _data_root = _make_roots(tmp_path)

    report = await _probe(
        sys.executable,
        [_write_probe_script(tmp_path)],
        {"plugin_root": plugin_root},
    )
    assert report["PLUGIN_ROOT"] == plugin_root
    assert report["PLUGIN_DATA"] == "UNSET"


@pytest.mark.integration
async def test_stdio_expands_placeholders_in_env_args_and_cwd(tmp_path: Path, _reset_manager: None) -> None:
    """``${PLUGIN_ROOT}`` / ``${PLUGIN_DATA}`` must expand in env/args/cwd (§9)."""
    plugin_root, data_root = _make_roots(tmp_path)
    script = _write_probe_script(tmp_path)

    report = await _probe(
        sys.executable,
        [script, "${PLUGIN_ROOT}/args-res", "${PLUGIN_DATA}/data-res"],
        {
            "plugin_root": plugin_root,
            "data_root": data_root,
            "env": {"CUSTOM_ENV": "${PLUGIN_ROOT}/env-res"},
            "cwd": "${PLUGIN_ROOT}",
        },
    )
    assert report["PLUGIN_ROOT"] == plugin_root
    assert report["PLUGIN_DATA"] == data_root
    assert report["CUSTOM_ENV"] == f"{plugin_root}/env-res"
    assert report["CWD"] == plugin_root
    assert report["ARGV"][-2:] == [f"{plugin_root}/args-res", f"{data_root}/data-res"]


@pytest.mark.integration
async def test_stdio_dot_slash_command_runs_from_plugin_root(tmp_path: Path, _reset_manager: None) -> None:
    """A ``./``-relative command implies the plugin root as cwd (§7.2.1)."""
    plugin_root, data_root = _make_roots(tmp_path)
    bin_dir = tmp_path / "plugin_root" / "bin"
    bin_dir.mkdir()
    probe_bin = bin_dir / "probe.py"
    probe_bin.write_text(f"#!{sys.executable}\n" + _ENV_PROBE_SERVER_SRC, encoding="utf-8")
    probe_bin.chmod(0o755)

    report = await _probe("./bin/probe.py", [], {"plugin_root": plugin_root, "data_root": data_root})
    assert report["PLUGIN_ROOT"] == plugin_root
    assert report["CWD"] == plugin_root
    assert report["ARGV"][0].endswith("bin/probe.py")


@pytest.mark.integration
async def test_stdio_strips_dangerous_env_variables_at_runtime(tmp_path: Path, _reset_manager: None) -> None:
    """Injection variables of the server's ``env`` are stripped at launch: the child never receives them."""
    plugin_root, data_root = _make_roots(tmp_path)
    script = _write_probe_script(tmp_path)

    report = await _probe(
        sys.executable,
        [script],
        {
            "plugin_root": plugin_root,
            "data_root": data_root,
            "env": {"CUSTOM_ENV": "legitimate_value", **_INJECTED_ENV},
        },
    )
    assert report["PLUGIN_ROOT"] == plugin_root
    assert report["PLUGIN_DATA"] == data_root
    assert report["CUSTOM_ENV"] == "legitimate_value"
    received = report["ENV_NAMES"]
    assert isinstance(received, list)
    leaked = {name.upper() for name in received} & {name.upper() for name in _INJECTED_ENV}
    assert not leaked, f"injected names reached the stdio child: {sorted(leaked)}"


@pytest.mark.integration
async def test_stdio_child_trusts_the_system_store_when_enterprise_network_is_on(
    tmp_path: Path, _reset_manager: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child inherits none of the host's environment, so the setting reaches it as ``NODE_USE_SYSTEM_CA``."""
    monkeypatch.setenv("MYRM_TLS_STRICT", "0")

    report = await _probe(sys.executable, [_write_probe_script(tmp_path)], None)

    assert report["NODE_USE_SYSTEM_CA"] == "1"


@pytest.mark.integration
async def test_stdio_child_gets_no_system_store_variable_while_strict(tmp_path: Path, _reset_manager: None) -> None:
    report = await _probe(sys.executable, [_write_probe_script(tmp_path)], None)

    assert report["NODE_USE_SYSTEM_CA"] == "UNSET"


@pytest.mark.integration
async def test_stdio_child_keeps_the_system_store_value_the_server_sets(
    tmp_path: Path, _reset_manager: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MYRM_TLS_STRICT", "0")

    report = await _probe(sys.executable, [_write_probe_script(tmp_path)], {"env": {"NODE_USE_SYSTEM_CA": "0"}})

    assert report["NODE_USE_SYSTEM_CA"] == "0"
