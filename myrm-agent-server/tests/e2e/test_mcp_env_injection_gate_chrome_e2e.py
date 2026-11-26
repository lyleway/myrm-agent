"""Chrome E2E: every MCP settings surface refuses environment names that hijack the server process.

Each surface runs the backend security scan before it stores or enables a config. A config whose ``env`` names steer
git or the dynamic loader (``GIT_CONFIG_*``, ``LD_*``, ``DYLD_*``) is refused with the scan verdict and never reaches
the stored configs (Import JSON, one node for a single and a batched import; the configuration backup file of the
import/export section) or is never enabled (Add Service editor, enable switch); the same configs with ordinary names
are accepted and their env is stored as written.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

from tests.support.chrome_config_import_e2e import (
    CONFIG_IMPORT_ROUTE,
    IMPORT_BLOCKED,
    IMPORT_SUCCEEDED,
    import_config_backup,
)
from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    get_e2e_api_url,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.chrome_mcp_editor_e2e import (
    CLICK_SAVE_CONFIG_JS,
    EDITOR_BLOCKED_MESSAGE_JS,
    EDITOR_SCAN_FINDINGS_JS,
    confirm_reload_dialog_after_save,
    fill_editor_fields,
    open_stdio_add_service_editor,
)
from tests.support.chrome_mcp_settings_e2e import (
    MCP_IMPORT_ERROR_JS,
    MCP_PAGE_READY_JS,
    RELOAD_DIALOG_STATE_JS,
    confirm_reload_dialog,
    import_mcp_json,
    mcp_page_state_js,
    put_mcp_configs,
    reload_mcp_settings_page,
    seed_empty_mcp_configs,
    server_config_in_api,
    server_exists_in_api,
    toggle_server_switch_js,
    wait_for_server_present,
    wait_until,
)
from tests.support.chrome_page_text import page_text_matches_js

_ORDINARY_SERVER = "e2e-ordinary-env"
_SECOND_ORDINARY_SERVER = "e2e-ordinary-env-second"
_HIJACK_SERVER = "e2e-hijack-env"
_EDITOR_SERVER = "e2e-editor-env"
_TOGGLE_ORDINARY_SERVER = "e2e-toggle-ordinary-env"
_TOGGLE_HIJACK_SERVER = "e2e-toggle-hijack-env"
_BACKUP_ORDINARY_SERVER = "e2e-backup-ordinary-env"
_BACKUP_SECOND_SERVER = "e2e-backup-second-env"
_BACKUP_THIRD_SERVER = "e2e-backup-third-env"
_BACKUP_HIJACK_SERVER = "e2e-backup-hijack-env"
_ORDINARY_ENV = {"LOG_LEVEL": "debug", "GIT_AUTHOR_NAME": "synthetic"}
_HIJACK_ENV = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.sshCommand",
    "GIT_CONFIG_VALUE_0": "echo synthetic-injection",
}
# Only the GIT_CONFIG_ family rule refuses this name: without the rule the scan has no other finding, so a backup that is
# still refused could not be passing for another reason (the result line of the import names no finding).
_BACKUP_HIJACK_ENV = {"GIT_CONFIG_GLOBAL": "/tmp/synthetic-gitconfig"}
# Answers the editor's probes and ``verify``: a stub that stays up and lists one tool.
_E2E_STDIO_MCP_STUB = Path(__file__).resolve().parents[1] / "support" / "e2e_minimal_stdio_mcp_server.py"

# The verdict text is keyed by the finding's threat type, so it can only come from the environment-injection finding.
_ENV_FINDING = r"(Dangerous environment variable|危险环境变量)"
# Import JSON shows "<scan blocked message>: <description of the first finding>".
_IMPORT_VERDICT = re.compile(rf"(Security scan blocked|安全扫描已阻止).*{_ENV_FINDING}", re.DOTALL)
# The editor and the enable toast show "<description> (<field of the first finding>)".
_GATE_VERDICT = rf"{_ENV_FINDING}.*extra_params\.env\.GIT_CONFIG_\w+"
_ENABLE_REFUSED_TOAST = rf"(Security scan blocked|安全扫描已阻止).*{_GATE_VERDICT}"


def _stdio_servers(server_envs: dict[str, dict[str, str]]) -> dict[str, object]:
    """An ``mcpServers`` import document with one stdio server per entry, each carrying its ``env``."""
    servers = {name: {"command": sys.executable, "args": ["-c", "pass"], "env": env} for name, env in server_envs.items()}
    return {"mcpServers": servers}


def _stored_stdio_config(name: str, env: dict[str, str]) -> dict[str, object]:
    """A disabled stdio config exactly as the config store keeps it; the store itself never scans what it is given."""
    return {
        "name": name,
        "type": "stdio",
        "command": sys.executable,
        "args": [str(_E2E_STDIO_MCP_STUB)],
        "description": "E2E env gate probe",
        "enabled": False,
        "headers": {},
        "extra_params": {"env": env},
    }


def _config_backup(server_envs: dict[str, dict[str, str]]) -> dict[str, object]:
    """A configuration backup file (the format of the import/export section) with one stored stdio server per entry."""
    return {
        "version": "4.0.0",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "config": {"mcpConfigs": [_stored_stdio_config(name, env) for name, env in server_envs.items()]},
    }


def _assert_never_stored(*names: str, settle_sec: float = 6.0) -> None:
    """None of ``names`` reaches the stored configs: the store pushes to the backend a moment after it changes."""
    deadline = time.monotonic() + settle_sec
    while time.monotonic() < deadline:
        stored = [name for name in names if server_exists_in_api(name)]
        assert not stored, f"a refused backup stored {stored}"
        time.sleep(0.4)


def _no_persist_pending(client: ChromeMcpClient, page: McpPage) -> None:
    """A refused change must not have queued a save behind the reload-confirm dialog."""
    pending = client.evaluate(page, RELOAD_DIALOG_STATE_JS, timeout_sec=10.0)
    assert isinstance(pending, dict) and pending.get("titleMatch") is False, f"refused change queued a save: {pending}"


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
        import_mcp_json(client, page, _stdio_servers({_ORDINARY_SERVER: _ORDINARY_ENV}))
        confirm_reload_dialog(client, page, timeout_sec=90.0)
        wait_for_server_present(_ORDINARY_SERVER)
        stored = server_config_in_api(_ORDINARY_SERVER)
        assert stored is not None, "ordinary-env import missing from the stored configs"
        assert stored.get("extra_params") == {"env": _ORDINARY_ENV}, stored

        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        import_mcp_json(client, page, _stdio_servers({_HIJACK_SERVER: _HIJACK_ENV}))
        verdict = wait_for_state(client, page, MCP_IMPORT_ERROR_JS, timeout_sec=60.0)
        assert _IMPORT_VERDICT.search(str(verdict.get("text"))), json.dumps(verdict, ensure_ascii=False)
        _no_persist_pending(client, page)
        assert not server_exists_in_api(_HIJACK_SERVER), "refused import was stored"


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE", access_scope="GLOBAL_WRITE", workload="STANDARD", private_reason="global_write_non_namespace"
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_batch_import_is_refused_whole_when_one_server_carries_injection_env() -> None:
    """One session: a batch of ordinary servers imports; a batch with one GIT_CONFIG_* server stores none of it."""
    prepare_e2e_ui_session(get_e2e_api_url())
    warm_ui_route("/settings/mcp")
    with open_settings_subroute("/settings/mcp", timeout_ms=120_000) as (client, page):
        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        import_mcp_json(client, page, _stdio_servers({_ORDINARY_SERVER: _ORDINARY_ENV, _SECOND_ORDINARY_SERVER: _ORDINARY_ENV}))
        confirm_reload_dialog(client, page, timeout_sec=90.0)
        wait_for_server_present(_ORDINARY_SERVER)
        wait_for_server_present(_SECOND_ORDINARY_SERVER)

        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        import_mcp_json(client, page, _stdio_servers({_ORDINARY_SERVER: _ORDINARY_ENV, _HIJACK_SERVER: _HIJACK_ENV}))
        verdict = wait_for_state(client, page, MCP_IMPORT_ERROR_JS, timeout_sec=60.0)
        assert _IMPORT_VERDICT.search(str(verdict.get("text"))), json.dumps(verdict, ensure_ascii=False)
        _no_persist_pending(client, page)
        assert not server_exists_in_api(_HIJACK_SERVER), "refused batch stored the hijacking server"
        assert not server_exists_in_api(_ORDINARY_SERVER), "refused batch stored its ordinary server (import must be atomic)"


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE", access_scope="GLOBAL_WRITE", workload="STANDARD", private_reason="global_write_non_namespace"
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_add_service_editor_refuses_injection_env_until_it_is_removed() -> None:
    """One session in the Add Service editor: the live scan flags the env, Save is refused; with ordinary env it saves."""
    prepare_e2e_ui_session(get_e2e_api_url())
    warm_ui_route("/settings/mcp")
    with open_settings_subroute("/settings/mcp", timeout_ms=120_000) as (client, page):
        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page)
        ready = wait_for_state(client, page, MCP_PAGE_READY_JS, timeout_sec=90.0)
        assert ready.get("ready") is True, json.dumps(ready, ensure_ascii=False)

        open_stdio_add_service_editor(client, page, name=_EDITOR_SERVER)
        fill_editor_fields(
            client,
            page,
            (
                ("Command|命令", sys.executable),
                ("Arguments|参数", str(_E2E_STDIO_MCP_STUB)),
                ("Description|描述", "E2E editor env gate probe"),
                ("Extra Parameters|额外参数", json.dumps({"env": _HIJACK_ENV})),
            ),
        )
        findings = wait_for_state(client, page, EDITOR_SCAN_FINDINGS_JS, timeout_sec=30.0)
        assert re.search(_ENV_FINDING, str(findings.get("text"))), json.dumps(findings, ensure_ascii=False)
        saved = client.evaluate(page, CLICK_SAVE_CONFIG_JS, timeout_sec=15.0)
        assert isinstance(saved, dict) and saved.get("ok") is True, saved
        blocked = wait_for_state(client, page, EDITOR_BLOCKED_MESSAGE_JS, timeout_sec=60.0)
        assert re.search(_GATE_VERDICT, str(blocked.get("text")), re.DOTALL), json.dumps(blocked, ensure_ascii=False)
        _no_persist_pending(client, page)
        assert not server_exists_in_api(_EDITOR_SERVER), "refused save was stored"

        fill_editor_fields(client, page, (("Extra Parameters|额外参数", json.dumps({"env": _ORDINARY_ENV})),))
        saved_again = client.evaluate(page, CLICK_SAVE_CONFIG_JS, timeout_sec=15.0)
        assert isinstance(saved_again, dict) and saved_again.get("ok") is True, saved_again
        confirm_reload_dialog_after_save(client, page, timeout_sec=120.0)
        wait_for_server_present(_EDITOR_SERVER)
        stored = server_config_in_api(_EDITOR_SERVER)
        assert stored is not None, "ordinary-env save missing from the stored configs"
        assert stored.get("extra_params") == {"env": _ORDINARY_ENV}, stored


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE", access_scope="GLOBAL_WRITE", workload="STANDARD", private_reason="global_write_non_namespace"
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_enable_switch_refuses_a_stored_config_whose_env_hijacks_the_server() -> None:
    """One session: a stored GIT_CONFIG_* config cannot be switched on; a stored ordinary config can."""
    prepare_e2e_ui_session(get_e2e_api_url())
    warm_ui_route("/settings/mcp")
    with open_settings_subroute("/settings/mcp", timeout_ms=120_000) as (client, page):
        put_mcp_configs(
            [
                _stored_stdio_config(_TOGGLE_HIJACK_SERVER, _HIJACK_ENV),
                _stored_stdio_config(_TOGGLE_ORDINARY_SERVER, _ORDINARY_ENV),
            ]
        )
        wait_until(
            lambda: server_exists_in_api(_TOGGLE_HIJACK_SERVER) and server_exists_in_api(_TOGGLE_ORDINARY_SERVER),
            timeout_sec=20.0,
            failure="failed to seed the stored configs",
        )
        reload_mcp_settings_page(client, page)
        listed = wait_for_state(client, page, mcp_page_state_js(_TOGGLE_ORDINARY_SERVER), timeout_sec=90.0)
        assert listed.get("ready") is True, json.dumps(listed, ensure_ascii=False)

        toggled = client.evaluate(page, toggle_server_switch_js(_TOGGLE_HIJACK_SERVER), timeout_sec=15.0)
        assert isinstance(toggled, dict) and toggled.get("ok") is True, toggled
        toast = wait_for_state(client, page, page_text_matches_js(_ENABLE_REFUSED_TOAST), timeout_sec=60.0)
        assert toast.get("ready") is True, json.dumps(toast, ensure_ascii=False)
        _no_persist_pending(client, page)
        hijack = server_config_in_api(_TOGGLE_HIJACK_SERVER)
        assert hijack is not None and hijack.get("enabled") is False, f"refused enable was persisted: {hijack}"

        toggled_ordinary = client.evaluate(page, toggle_server_switch_js(_TOGGLE_ORDINARY_SERVER), timeout_sec=15.0)
        assert isinstance(toggled_ordinary, dict) and toggled_ordinary.get("ok") is True, toggled_ordinary
        confirm_reload_dialog(client, page, timeout_sec=90.0)
        wait_until(
            lambda: (server_config_in_api(_TOGGLE_ORDINARY_SERVER) or {}).get("enabled") is True,
            timeout_sec=45.0,
            failure="confirmed enable of the ordinary config was not persisted",
        )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE", access_scope="GLOBAL_WRITE", workload="STANDARD", private_reason="global_write_non_namespace"
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_config_backup_import_is_refused_whole_when_one_server_carries_injection_env() -> None:
    """One session in Import/Export: a backup of ordinary servers imports; one with a GIT_CONFIG_* server stores none."""
    prepare_e2e_ui_session(get_e2e_api_url())
    warm_ui_route("/settings/developer")
    with open_settings_subroute(CONFIG_IMPORT_ROUTE, timeout_ms=120_000) as (client, page):
        seed_empty_mcp_configs()
        reload_mcp_settings_page(client, page, CONFIG_IMPORT_ROUTE)
        import_config_backup(
            client, page, _config_backup({_BACKUP_ORDINARY_SERVER: _ORDINARY_ENV, _BACKUP_SECOND_SERVER: _ORDINARY_ENV})
        )
        imported = wait_for_state(client, page, page_text_matches_js(IMPORT_SUCCEEDED), timeout_sec=60.0)
        assert imported.get("ready") is True, json.dumps(imported, ensure_ascii=False)
        wait_for_server_present(_BACKUP_ORDINARY_SERVER)
        wait_for_server_present(_BACKUP_SECOND_SERVER)
        stored = server_config_in_api(_BACKUP_ORDINARY_SERVER)
        assert stored is not None and stored.get("extra_params") == {"env": _ORDINARY_ENV}, stored

        import_config_backup(
            client, page, _config_backup({_BACKUP_THIRD_SERVER: _ORDINARY_ENV, _BACKUP_HIJACK_SERVER: _BACKUP_HIJACK_ENV})
        )
        refused = wait_for_state(client, page, page_text_matches_js(IMPORT_BLOCKED), timeout_sec=60.0)
        assert refused.get("ready") is True, json.dumps(refused, ensure_ascii=False)
        _assert_never_stored(_BACKUP_HIJACK_SERVER, _BACKUP_THIRD_SERVER)
