"""The lazy exports of ``agent.acp``: each name is what its module defines, and the protocol modules stay light."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

import myrm_agent_harness.agent.acp as acp_pkg

_DEFINED_IN = {
    "AgentBridge": "bridge",
    "AgentFactory": "bridge",
    "DefaultAgentFactory": "default_factory",
    "MyrmAcpServer": "server",
    "SkillAgentFactory": "skill_factory",
    "convert_acp_mcp_servers": "mcp_converter",
    "run_server": "server",
}


def test_the_package_exports_the_inbound_surface_and_nothing_else() -> None:
    assert sorted(acp_pkg.__all__) == sorted(_DEFINED_IN)


@pytest.mark.parametrize("name", sorted(_DEFINED_IN))
def test_an_export_is_the_object_its_module_defines(name: str) -> None:
    defining = importlib.import_module(f"myrm_agent_harness.agent.acp.{_DEFINED_IN[name]}")

    assert getattr(acp_pkg, name) is getattr(defining, name)


def test_dir_lists_every_export() -> None:
    assert set(acp_pkg.__all__) <= set(dir(acp_pkg))


def test_an_unknown_name_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = acp_pkg.NonExistentThing  # type: ignore[attr-defined]


def test_importing_the_protocol_modules_does_not_load_the_agent_stack() -> None:
    """The factories bring in the agent and its model stack; a protocol-only host must not pay for them."""
    probe = (
        "import sys\n"
        "import myrm_agent_harness.agent.acp.server\n"
        "loaded = [m for m in ('myrm_agent_harness.agent.base_agent', 'myrm_agent_harness.agent.acp.default_factory',"
        " 'myrm_agent_harness.agent.acp.skill_factory') if m in sys.modules]\n"
        "print(','.join(loaded))\n"
    )

    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=120)

    assert result.stdout.strip() == ""
