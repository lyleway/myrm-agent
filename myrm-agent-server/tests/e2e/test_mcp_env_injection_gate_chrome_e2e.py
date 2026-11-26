"""Chrome E2E: MCP Import JSON refuses environment names that hijack the server process.

The importer runs the backend security scan before it stores anything. A config whose ``env`` names steer git or the
dynamic loader (``GIT_CONFIG_*``, ``LD_*``, ``DYLD_*``) is refused with the scan verdict and never reaches the stored
configs; the same config with ordinary names is accepted and its env is stored as written.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    get_e2e_api_url,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.chrome_mcp_settings_e2e import (
    CLICK_IMPORT_JSON_BUTTON_JS,
    CLICK_IMPORT_SUBMIT_JS,
    MCP_IMPORT_ERROR_JS,
    MCP_IMPORT_MODAL_READY_JS,
    MCP_PAGE_READY_JS,
    RELOAD_DIALOG_STATE_JS,
    confirm_reload_dialog,
    reload_mcp_settings_page,
    seed_empty_mcp_configs,
    server_config_in_api,
    server_exists_in_api,
    set_import_textarea_js,
    wait_for_server_present,
)

_ORDINARY_SERVER = "e2e-ordinary-env"
_HIJACK_SERVER = "e2e-hijack-env"
_ORDINARY_ENV = {"LOG_LEVEL": "debug", "GIT_AUTHOR_NAME": "synthetic"}
_HIJACK_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.sshCommand",
    "GIT_CONFIG_VALUE_0": "echo synthetic-injection",
}
# The importer shows "<scan blocked message>: <description of the first finding>"; the description is keyed by the
# finding's threat type, so this verdict can only come from the environment-injection finding.
_ENV_INJECTION_VERDICT = re.compile(
    r"(Security scan blocked|安全扫描已阻止).*(Dangerous environment variable|危险环境变量)", re.DOTALL
)


def _import_json(client: ChromeMcpClient, page: McpPage, server_name: str, env: dict[str, str]) -> None:
    """Open the Import JSON modal on a ready MCP page, paste one stdio server with ``env`` and submit."""
    ready = wait_for_state(client, page, MCP_PAGE_READY_JS, timeout_sec=90.0)
    assert ready.get("ready") is True, json.dumps(ready, ensure_ascii=False)
    opened = client.evaluate(page, CLICK_IMPORT_JSON_BUTTON_JS, timeout_sec=15.0)
    assert isinstance(opened, dict) and opened.get("ok") is True, opened
    modal = wait_for_state(client, page, MCP_IMPORT_MODAL_READY_JS, timeout_sec=30.0)
    assert modal.get("ready") is True, json.dumps(modal, ensure_ascii=False)
    payload = {"mcpServers": {server_name: {"command": sys.executable, "args": ["-c", "pass"], "env": env}}}
    filled = client.evaluate(page, set_import_textarea_js(json.dumps(payload)), timeout_sec=15.0)
    assert isinstance(filled, dict) and filled.get("ok") is True, filled
    submitted = client.evaluate(page, CLICK_IMPORT_SUBMIT_JS, timeout_sec=15.0)
    assert isinstance(submitted, dict) and submitted.get("ok") is True, submitted


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE", access_scope="GLOBAL_WRITE", workload="STANDARD", private_reason="global_write_non_namespace"
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_import_refuses_code_injection_env_and_stores_ordinary_env() -> None:
    """One session: ordinary env names import and persist; GIT_CONFIG_* names are refused and nothing is stored."""
    prepare_e2e_ui_session(get_e2e_api_url())
    warm_ui_route("/settings/mcp")
    with open_settings_subroute("/settings/mcp", timeout_ms=120_000) as (client, page):
        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        _import_json(client, page, _ORDINARY_SERVER, _ORDINARY_ENV)
        confirm_reload_dialog(client, page, timeout_sec=90.0)
        wait_for_server_present(_ORDINARY_SERVER)
        stored = server_config_in_api(_ORDINARY_SERVER)
        assert stored is not None, "ordinary-env import missing from the stored configs"
        assert stored.get("extra_params") == {"env": _ORDINARY_ENV}, stored

        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        _import_json(client, page, _HIJACK_SERVER, _HIJACK_ENV)
        verdict = wait_for_state(client, page, MCP_IMPORT_ERROR_JS, timeout_sec=60.0)
        assert _ENV_INJECTION_VERDICT.search(str(verdict.get("text"))), json.dumps(verdict, ensure_ascii=False)
        pending = client.evaluate(page, RELOAD_DIALOG_STATE_JS, timeout_sec=10.0)
        assert isinstance(pending, dict) and pending.get("titleMatch") is False, f"refused import queued a save: {pending}"
        assert not server_exists_in_api(_HIJACK_SERVER), "refused import was stored"
