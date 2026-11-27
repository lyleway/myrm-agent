"""Chrome E2E (LIVE lane): a real agent builds a page that needs live data, and the user installs and allows it.

Real business flow with a real model; nothing on the path is mocked:

    the user turns the life workbench skill on, lets it use files, and chats with an agent that carries it
    -> the user asks for a life workbench with live weather and a reminder; the agent picks the skill
    -> the agent writes an HTML page with the `myrm-app` declaration and calls `window.myrm` (the skill teaches it how)
    -> the answer's card offers "Install as app"; the app's permission dialog lists what the page declared
    -> the user saves, opens the app again, and the page's own calls run: the weather request is answered by the
       server's proxy and the reminder is shown, both visible in the activity log.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_installed_app_live_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    dismiss_blocking_modals,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    prepare_e2e_ui_session,
    wait_e2e_provider_ready,
    wait_for_react_e2e_bridge,
    wait_for_state,
    warm_ui_route,
)
from tests.support.chrome_skill_hooks_approval import click_approve
from tests.support.e2e_provider_seed import seed_live_e2e_providers
from tests.support.e2e_runtime_guard import E2EResourceLedger
from tests.support.hitl_live_e2e import pin_private_hitl_mode
from tests.support.installed_app_e2e import (
    APPS_PATH,
    ATTACH_CHAT_JS,
    DIALOG_STATE_JS,
    DISMISS_MIGRATION_JS,
    INSTALL_FROM_CARD_JS,
    NO_DIALOG_JS,
    SAVE,
    activity_counts,
    app_detail,
    click_dialog_button,
    page_wait_sec,
)

_SKILL_ID = "personal-life-workbench"
# What its `allowed-tools` (file_write_tool, file_read_tool, file_edit_tool) need.
_SKILL_PERMISSIONS = ["file_read", "file_write"]
_WEATHER_HOST = "api.open-meteo.com"
_PROMPT = (
    "帮我做一个个人生活工作台，保存成工作区里的 workbench.html：里面放一个待办清单，再放一个柏林实时天气小组件，"
    f"天气数据用免密钥的公开接口 {_WEATHER_HOST}"
    f"（https://{_WEATHER_HOST}/v1/forecast?latitude=52.52&longitude=13.41&current=temperature_2m）。"
    "页面一打开就自动取一次当前气温并显示出来，取到之后给我发一条提醒通知。请直接执行，不要询问确认。"
)

_SEND_TURN_JS = """(async () => {
  const bridge = window.__MYRM_E2E_CHAT__;
  if (!bridge?.sendChatMessage) return { ok: false, err: 'no-sendChatMessage' };
  bridge.setActionMode?.('agent');
  const usersBefore = bridge.turnSnapshot?.().userCount ?? 0;
  const result = await bridge.sendChatMessage(__PROMPT__, { baselineUserCount: usersBefore, preserveActionMode: true });
  return { ...result, usersBefore };
})()"""

# One probe per poll: whether the run is over, whether a tool approval waits for the user, whether the app card is there.
_TURN_STATE_JS = """(() => {
  const store = window.__myrmChatStore?.getState?.();
  const messages = Array.isArray(store?.messages) ? store.messages : [];
  const assistant = [...messages].reverse().find((message) => message?.role === 'assistant');
  const install = /^(Install as app|安装为应用)$/;
  const snapshot = window.__MYRM_E2E_CHAT__?.toolApprovalSnapshot?.() ?? {};
  const approvalWaiting = Number(snapshot.queueLen ?? 0) > 0
    && Array.from(document.querySelectorAll('button')).some((button) => /Approve|批准/.test((button.textContent || '').trim()));
  return {
    loading: Boolean(store?.loading),
    approvalWaiting,
    hasCard: Array.from(document.querySelectorAll('button')).some(
      (button) => install.test((button.getAttribute('aria-label') || '').trim()),
    ),
    reply: assistant?.content ? String(assistant.content).slice(0, 300) : null,
  };
})()"""

_HOST_READY_JS = "(() => ({ ready: Boolean(document.querySelector('[data-testid=\"app-host\"] iframe')) }))()"

_AGENT_BOUND_JS = """((agentId) => {
  const bound = window.__myrmChatStore?.getState?.()?.agentConfig?.agentId ?? null;
  return { ready: bound === agentId, bound };
})"""


def _open_workbench_chat(api_url: str, ledger: E2EResourceLedger) -> tuple[str, str]:
    """What a user sets up first: the workbench skill on and allowed to touch files, an agent that carries it, and a chat.

    Prebuilt skills are opt-in, an agent only sees the skills bound to it, and a loaded skill may use a tool
    only after the user granted the matching permission (Settings → Skills → Permissions).
    """
    enabled = http_json("POST", f"{api_url}/api/v1/skills/{_SKILL_ID}/enable")
    assert isinstance(enabled, dict) and enabled.get("enabled") is True, enabled
    granted = http_json(
        "POST", f"{api_url}/api/v1/skills/{_SKILL_ID}/permissions/grant", body={"permissions": _SKILL_PERMISSIONS}
    )
    assert isinstance(granted, dict) and granted.get("success") is True, granted

    suffix = uuid.uuid4().hex[:8]
    created = http_json(
        "POST",
        f"{api_url}/api/v1/user-agents",
        body={
            "name": f"Installed app live E2E {suffix}",
            "description": "Builds personal pages with the life workbench skill",
            "system_prompt": "You are a helpful personal assistant.",
            "skill_ids": [_SKILL_ID],
            "mcp_ids": [],
            "enabled_builtin_tools": [],
        },
    )
    created_data = created.get("data") if isinstance(created, dict) else None
    agent_id = created_data.get("id") if isinstance(created_data, dict) else None
    assert isinstance(agent_id, str) and agent_id, created
    ledger.register("agent", agent_id)

    chat_id = f"e2eapp{suffix}"
    created_chat = http_json(
        "POST",
        f"{api_url}/api/v1/chats/",
        body={
            "chat_id": chat_id,
            "title": "Installed app live E2E",
            "agent_id": agent_id,
            "action_mode": "agent",
            "messages": [],
        },
    )
    assert isinstance(created_chat, dict) and created_chat.get("success") is True, created_chat
    ledger.register("chat", chat_id)
    return agent_id, chat_id


def _wait_for_app_card(client: ChromeMcpClient, page: McpPage, *, timeout_sec: float = 480.0) -> None:
    """Approve what the run parks behind, until the run is over and its answer carries the app's card."""
    deadline = time.monotonic() + timeout_sec
    state: object = None
    while time.monotonic() < deadline:
        state = client.evaluate(page, _TURN_STATE_JS, timeout_sec=15.0)
        if isinstance(state, dict):
            if state.get("loading") is False and state.get("hasCard") is True:
                return
            if state.get("approvalWaiting") is True:
                click_approve(client, page)
        time.sleep(3.0)
    pytest.fail(
        f"the agent never delivered an HTML app within {timeout_sec}s; last state: {json.dumps(state, ensure_ascii=False)}"
    )


def _wait_for_activity(
    api_url: str, artifact_id: str, expected: set[tuple[str, str, str | None]], *, timeout_sec: float = 90.0
) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if expected <= set(activity_counts(api_url, artifact_id)):
            return
        time.sleep(3.0)
    pytest.fail(f"activity never held {sorted(expected, key=str)}; got {dict(activity_counts(api_url, artifact_id))}")


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="NAMESPACE_WRITE",
    workload="LIVE",
    private_reason="live_shpoib",
)
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_real_agent_builds_an_app_the_user_installs_and_allows_live_chrome_e2e(e2e_resource_ledger: E2EResourceLedger) -> None:
    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url().rstrip("/")
    prepare_e2e_ui_session(api_url)
    seed_live_e2e_providers(api_url)
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready — run ./myrm-dev ready --chrome")
    pin_private_hitl_mode(api_url)

    agent_id, chat_id = _open_workbench_chat(api_url, e2e_resource_ledger)
    chat_url = f"{ui_url}/{chat_id}"
    warm_ui_route("/")
    warm_ui_route(f"/{chat_id}")
    warm_ui_route("/apps/warmup")
    page_wait = page_wait_sec()

    artifact_id: str | None = None
    try:
        with open_mcp_page(chat_url, timeout_ms=120_000) as (client, page):
            dismiss_blocking_modals(client, page)
            client.evaluate(page, DISMISS_MIGRATION_JS, timeout_sec=15.0)
            wait_for_react_e2e_bridge(client, page, timeout_sec=page_wait, page_url=chat_url)
            attached = client.evaluate(page, ATTACH_CHAT_JS.replace("__CHAT_ID__", json.dumps(chat_id)), timeout_sec=page_wait)
            assert isinstance(attached, dict) and attached.get("ok") is True, attached

            bound = wait_for_state(
                client, page, f"({_AGENT_BOUND_JS})({json.dumps(agent_id)})", timeout_sec=90.0, page_url=chat_url
            )
            assert bound.get("ready") is True, bound

            sent = client.evaluate(page, _SEND_TURN_JS.replace("__PROMPT__", json.dumps(_PROMPT)), timeout_sec=60.0)
            assert isinstance(sent, dict) and sent.get("ok") is not False, sent
            _wait_for_app_card(client, page)

            installed = client.evaluate(page, INSTALL_FROM_CARD_JS, timeout_sec=120.0)
            assert isinstance(installed, dict) and installed.get("ok") is True, installed
            artifact_id = str(installed["artifactId"])
            app_url = f"{ui_url}/apps/{artifact_id}"

            # What the page declared reaches the user's question: the weather host and the reminder.
            dialog = wait_for_state(client, page, DIALOG_STATE_JS, timeout_sec=page_wait, page_url=app_url)
            assert dialog["hosts"] == [_WEATHER_HOST], dialog
            assert dialog["checked"] == ["true", "true"], dialog
            click_dialog_button(client, page, SAVE)
            wait_for_state(client, page, NO_DIALOG_JS, timeout_sec=30.0, page_url=app_url)
            grants = app_detail(api_url, artifact_id)["grants"]
            assert isinstance(grants, list)
            assert {str(grant["capability"]): grant["decision"] for grant in grants} == {"notify": "allow", "fetch": "allow"}, (
                grants
            )

            # Open the app again: the page's own startup calls now run with what the user allowed.
            client.navigate(page, app_url, timeout_ms=90_000)
            wait_for_state(client, page, _HOST_READY_JS, timeout_sec=page_wait, page_url=app_url)
            _wait_for_activity(api_url, artifact_id, {("fetch", "ok", _WEATHER_HOST), ("notify", "ok", None)})
    finally:
        if artifact_id is not None:
            http_json("DELETE", f"{api_url}{APPS_PATH}/{artifact_id}")
