"""The environment-probe MCP server as live-model tests use it: its configuration, the request and the report a guarded run gives.

[INPUT]
- tests.support.e2e_env_probe_stdio_mcp_server (POS: the stdio server this configuration launches)

[OUTPUT]
- INJECTION_ENV: the ``GIT_CONFIG_*`` names, with synthetic values, that must never reach the server process
- ENV_PROBE_ENV: the environment configured on the server: ``INJECTION_ENV`` plus one ordinary variable
- ENV_PROBE_SERVER: the server entry as a user, the Control Plane or the WebUI hands it over
- ENV_PROBE_PROMPT: the request that makes a model call the server's ``env_presence`` tool and tell what it returned
- EXPECTED_ENV_REPORT: what a guarded run reports: no ``GIT_CONFIG_*`` variable received, the ordinary one received
- reported_env_states(): the ``git_config_vars=<n>`` and ``control_vars=<n>`` counts found in some texts, the first report of each winning

[POS]
Shared by tests/api/agent/test_org_mcp_agent_stream_e2e.py (in-process agent stream) and
tests/e2e/test_org_mcp_agent_usage_chrome_e2e.py (WebUI chat), so both read one definition of what the probe is told and
what it answers.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Synthetic values: git would run ``echo`` for ssh transports if these names ever reached a process that invokes git; the
# probe server never does.
INJECTION_ENV: dict[str, str] = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.sshCommand",
    "GIT_CONFIG_VALUE_0": "echo synthetic-injection",
}

# The probe server reads the same names from its own process environment.
_CONTROL_ENV_NAME = "E2E_PROBE_CONTROL"

ENV_PROBE_ENV: dict[str, str] = {**INJECTION_ENV, _CONTROL_ENV_NAME: "kept"}

ENV_PROBE_SERVER: dict[str, object] = {
    "name": "env-probe",
    "type": "stdio",
    "command": sys.executable,
    "args": [str(Path(__file__).resolve().parent / "e2e_env_probe_stdio_mcp_server.py")],
    "description": "MCP server that reports which probe environment variables it received",
    "extra_params": {"env": ENV_PROBE_ENV},
}

ENV_PROBE_PROMPT = (
    "请调用 env-probe MCP server 提供的 env_presence 工具。这是一个真实存在的 MCP 工具，"
    "必须真正调用它，禁止自己编造或用代码模拟。然后把工具返回的原始结果一字不差地告诉我，"
    "最后用一句话说明该工具进程收到了几个 GIT_CONFIG_ 开头的环境变量。"
)

# The control count is what makes a pass mean something: a server that was handed no environment at all reports it as 0.
EXPECTED_ENV_REPORT: dict[str, str] = {"git_config_vars": "0", "control_vars": "1"}

_ENV_REPORT_TOKEN = re.compile(rf"\b({'|'.join(EXPECTED_ENV_REPORT)})=(\d+)\b")


def reported_env_states(texts: list[str]) -> dict[str, str]:
    """Counts found in ``texts``; the first report of each label wins."""
    states: dict[str, str] = {}
    for text in texts:
        for label, count in _ENV_REPORT_TOKEN.findall(text):
            states.setdefault(label, count)
    return states
