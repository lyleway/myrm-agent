"""What the context-compaction Chrome E2Es share: a long dormant conversation, the model window and the page language.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: CDP page helpers, http_json)
- tests.support.e2e_provider_seed (POS: seeded LIVE model endpoints, strip_provider_prefix)
- cdp_chat.support (POS: provider config read and write on the runtime under test)

[OUTPUT]
- PROJECT_CODENAME / MIGRATION_WINDOW: the facts a compaction summary must keep
- create_dormant_chat(): a chat with a history (short or long) that carries those facts, last written ``quiet_for`` ago (hours by default)
- SHORT_CHAT_ROUNDS / LONG_CHAT_ROUNDS: the two history sizes a test can pick
- DORMANT_FOR / RECENT_FOR: how long ago the last message was written, for a user back after hours and for one still at it
- prepare_live_model(): the runtime under test answers with the real model, whose context window is pinned
- declare_context_window(): pins the model's context window on the runtime under test
- read_compaction_state(): what the server stored for a chat after a compaction
- locale_text() / locale_fragments(): the shipped wording of a key path, whole or cut at its placeholders
- chat_page() / open_chat() / open_new_chat(): the chat (or a new, unsent one) opened in the shared browser, bridge ready and chat (with its agent) loaded
- ATTACH_CHAT_JS: the page script that loads a chat into the store of the app through its E2E bridge
- read_locale_cookie() / set_locale_cookie() / ensure_locale() / switch_locale(): the page's language, handed back as found after a test

[POS]
The conversation is sent through the real chat-create API with back-dated timestamps, so the backend sees what it sees
for a user returning to an old chat. The window is pinned because the idle gate compares the history against a share of
the model's window, and the catalog's window for the E2E model is far larger than the history a test can afford to seed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cdp_chat.support import fetch_config_value, put_config_value, wait_e2e_provider_ready

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    attach_chat_and_wait_agent_binding,
    dismiss_blocking_modals,
    http_json,
    open_mcp_page,
    prepare_e2e_ui_session,
    reload_mcp_page,
    wait_for_react_e2e_bridge,
    wait_for_state,
    warm_ui_route,
)
from tests.support.e2e_provider_seed import ResolvedE2ELlmEndpoints, seed_live_e2e_providers, strip_provider_prefix

PROJECT_CODENAME = "AMBER-HARBOR-47"
MIGRATION_WINDOW = "2026-12-03 02:00"
CONTEXT_WINDOW_TOKENS = 128_000
# Agent setting the idle gate reads: a chat quiet for this long is compacted before the next reply.
IDLE_COMPACT_AFTER_SECONDS = 1800
# How long ago a chat's last message was written: a user back after hours, and one who is still at it.
DORMANT_FOR = timedelta(hours=3)
RECENT_FOR = timedelta(minutes=2)

_LOCALES_DIR = Path(__file__).resolve().parents[3] / "myrm-agent-frontend" / "locales"
_LOCALE_COOKIE = "NEXT_LOCALE"

_AREAS = ("支付网关", "订单中心", "库存服务", "用户画像", "消息推送", "搜索索引", "风控引擎", "报表仓库")
_RISKS = (
    "双写期间的数据不一致",
    "回滚脚本缺少幂等保护",
    "连接池在切换瞬间被打满",
    "下游超时重试放大了流量",
    "缓存预热不足带来的冷启动抖动",
    "监控告警阈值沿用了旧系统",
)
_OWNERS = ("Mara", "Idris", "Noor", "Tomas", "Wen")
_STATES = ("待评审", "整改中", "待复测", "已通过")
# A short history (about 12k tokens) compacts within seconds. A long one (about 38k) is what a user who presses compress
# really has, and the real model needs far longer to summarise it.
SHORT_CHAT_ROUNDS = 6
LONG_CHAT_ROUNDS = 18
_ITEMS_PER_ROUND = 45


def _review_log(round_no: int) -> str:
    """One round of review findings; every line differs so the history reads like real notes, not filler."""
    lines = [
        f"第{round_no}轮第{item + 1}项：{_AREAS[(round_no + item) % len(_AREAS)]}存在{_RISKS[(round_no * 3 + item) % len(_RISKS)]}，"
        f"建议由{_OWNERS[(round_no + 2 * item) % len(_OWNERS)]}在迁移窗口前完成整改并留存验证记录，当前状态为{_STATES[(round_no + item) % len(_STATES)]}。"
        for item in range(_ITEMS_PER_ROUND)
    ]
    return "\n".join(lines)


def _history(chat_id: str, *, rounds: int, context_usage_percent: float | None, quiet_for: timedelta) -> list[dict[str, object]]:
    """Alternating user / assistant messages ending on an assistant reply written ``quiet_for`` ago, a minute apart."""
    message_count = rounds * 2
    start = datetime.now(UTC).replace(tzinfo=None) - quiet_for - timedelta(minutes=message_count - 1)
    messages: list[dict[str, object]] = []
    for index in range(message_count):
        round_no = index // 2 + 1
        is_user = index % 2 == 0
        if is_user:
            content = (
                f"数据中心迁移项目代号 {PROJECT_CODENAME}，迁移窗口定在 {MIGRATION_WINDOW}，现场负责人 Mara。请记住这些，下面开始逐轮评审。"
                if round_no == 1
                else f"继续第{round_no}轮评审，沿用项目代号 {PROJECT_CODENAME}，把新发现的风险逐项列出来。"
            )
        else:
            content = f"已记录：项目 {PROJECT_CODENAME}，窗口 {MIGRATION_WINDOW}。\n{_review_log(round_no)}"
        message: dict[str, object] = {
            "messageId": f"{chat_id}-m{index}",
            "chatId": chat_id,
            "role": "user" if is_user else "assistant",
            "content": content,
            "createdAt": (start + timedelta(minutes=index)).isoformat(),
        }
        if not is_user and index == message_count - 1 and context_usage_percent is not None:
            # The ring and the strategy panel read the last reply's budget; compress is offered from 30 %.
            message["metadata"] = {
                "contextBudget": {
                    "current_tokens": int(CONTEXT_WINDOW_TOKENS * context_usage_percent / 100),
                    "max_context_tokens": CONTEXT_WINDOW_TOKENS,
                    "usage_percent": context_usage_percent,
                    "health_status": "healthy",
                }
            }
        messages.append(message)
    return messages


def create_dormant_chat(
    api_url: str,
    chat_id: str,
    *,
    agent_id: str | None = None,
    context_usage_percent: float | None = None,
    rounds: int = SHORT_CHAT_ROUNDS,
    quiet_for: timedelta = DORMANT_FOR,
) -> None:
    """Create a chat whose last message is ``quiet_for`` old; ``context_usage_percent`` sets the last reply's recorded usage."""
    body = http_json(
        "POST",
        f"{api_url}/api/v1/chats/",
        body={
            "chat_id": chat_id,
            "title": "Datacenter migration review",
            "agent_id": agent_id,
            "action_mode": "agent",
            "messages": _history(chat_id, rounds=rounds, context_usage_percent=context_usage_percent, quiet_for=quiet_for),
        },
    )
    assert isinstance(body, dict) and body.get("success") is True, f"create dormant chat failed: {body}"


def prepare_live_model(api_url: str) -> None:
    """Make the runtime under test answer with the real E2E model, whose window is pinned (see ``declare_context_window``)."""
    prepare_e2e_ui_session(api_url)
    declare_context_window(api_url, seed_live_e2e_providers(api_url))
    if not wait_e2e_provider_ready(timeout_sec=90.0):
        pytest.fail("Provider not ready; verify the .env.test LLM credentials")


def declare_context_window(api_url: str, endpoints: ResolvedE2ELlmEndpoints, tokens: int = CONTEXT_WINDOW_TOKENS) -> None:
    """Pin the context window of the seeded E2E models (user ``customModelInfo`` outranks the model catalog).

    The window is looked up under the model's id with or without its provider prefix, so both spellings are declared.
    """
    config = fetch_config_value("providers", api_url=api_url)
    existing = config.get("customModelInfo")
    declared: dict[str, object] = dict(existing) if isinstance(existing, dict) else {}
    for raw_model in (endpoints.basic_model, endpoints.lite_model):
        for key in {raw_model, strip_provider_prefix(raw_model)}:
            entry = declared.get(key)
            declared[key] = {**(entry if isinstance(entry, dict) else {}), "max_input_tokens": tokens}
    put_config_value("providers", {**config, "customModelInfo": declared}, api_url=api_url)


def read_compaction_state(api_url: str, chat_id: str) -> dict[str, object]:
    """What the server holds for the chat: the stored summary and the tokens its compactions saved."""
    chat = http_json("GET", f"{api_url}/api/v1/chats/{chat_id}")
    analytics = http_json("GET", f"{api_url}/api/v1/statistics/session/{chat_id}")
    assert isinstance(chat, dict) and isinstance(analytics, dict), (chat, analytics)
    chat_envelope, analytics_data = chat.get("data"), analytics.get("data")
    assert isinstance(chat_envelope, dict) and isinstance(analytics_data, dict), (chat, analytics)
    chat_data = chat_envelope.get("chat")
    assert isinstance(chat_data, dict), f"no chat in {json.dumps(chat_envelope)[:400]}"
    health = analytics_data.get("context_health")
    compaction = health.get("compaction") if isinstance(health, dict) else None
    assert isinstance(compaction, dict), f"no context_health.compaction in {json.dumps(analytics_data)[:400]}"
    return {
        "summary": chat_data.get("compacted_summary") or "",
        "summary_persisted": compaction.get("summary_persisted"),
        "tokens_saved": compaction.get("tokens_saved"),
    }


def locale_text(locale: str, *path: str) -> str:
    """The wording a locale file ships for a key path, e.g. ``("chat", "compact", "skip", "nothing")``."""
    node: object = json.loads((_LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    for part in path:
        assert isinstance(node, dict) and part in node, f"{locale}.json has no {'.'.join(path)}"
        node = node[part]
    assert isinstance(node, str), f"{locale}.json {'.'.join(path)} is not a sentence"
    return node


def locale_fragments(locale: str, *path: str) -> list[str]:
    """The wording of a key path cut at its ``{placeholders}``: the text a toast must carry, in order, around the values."""
    fragments = re.split(r"\{\w+\}", locale_text(locale, *path))
    assert fragments[-1], f"{locale}.json {'.'.join(path)} ends in a placeholder, so its value has no end to be read up to"
    return fragments


def read_locale_cookie(client: ChromeMcpClient, page: McpPage) -> str | None:
    """The language cookie the shared browser profile carries, so a test can hand it back untouched."""
    state = client.evaluate(
        page,
        f"""(() => {{
  const hit = document.cookie.split('; ').find((c) => c.startsWith('{_LOCALE_COOKIE}='));
  return {{ value: hit ? hit.slice({len(_LOCALE_COOKIE) + 1}) : null }};
}})()""",
        timeout_sec=15.0,
    )
    assert isinstance(state, dict), state
    value = state.get("value")
    return value if isinstance(value, str) else None


ATTACH_CHAT_JS = """(async (chatId) => {
  const bridge = window.__MYRM_E2E_CHAT__;
  if (!bridge?.attachToChat) return { ok: false, err: 'no-bridge' };
  await bridge.attachToChat(chatId);
  return { ok: true };
})"""


@contextmanager
def chat_page(ui_url: str, chat_id: str | None = None) -> Iterator[tuple[ChromeMcpClient, McpPage, str]]:
    """The chat (a new, unsent one without ``chat_id``) opened in the shared E2E browser.

    The language cookie the browser carried is handed back on exit.
    """
    route = f"/{chat_id}" if chat_id else "/"
    page_url = f"{ui_url}{route}"
    warm_ui_route(route)
    with open_mcp_page(page_url, timeout_ms=120_000) as (client, page):
        previous_locale = read_locale_cookie(client, page)
        try:
            yield client, page, page_url
        finally:
            set_locale_cookie(client, page, previous_locale)


def open_chat(client: ChromeMcpClient, page: McpPage, chat_id: str, page_url: str, *, agent_id: str | None = None) -> None:
    """Wait for the chat page and its E2E bridge, then load the chat (and its agent, if it has one) into the store."""
    dismiss_blocking_modals(client, page)
    wait_for_react_e2e_bridge(client, page, timeout_sec=90.0, page_url=page_url)
    if agent_id is not None:
        attach_chat_and_wait_agent_binding(client, page, chat_id, expected_preset="hitl", agent_id=agent_id, timeout_sec=90.0)
        return
    attached = client.evaluate(page, f"({ATTACH_CHAT_JS})({json.dumps(chat_id)})", timeout_sec=60.0)
    assert isinstance(attached, dict) and attached.get("ok") is True, f"attachToChat failed: {attached!r}"


# A new chat gets its id in the browser, before anything is sent: the server has never heard of it.
_NEW_CHAT_READY_JS = """(() => {
  const state = window.__myrmChatStore?.getState?.();
  const chatId = state?.chatId;
  const messages = state?.messages?.length ?? null;
  return { ready: typeof chatId === 'string' && chatId.startsWith('c-') && messages === 0, chatId: chatId ?? null, messages };
})()"""


def open_new_chat(client: ChromeMcpClient, page: McpPage, page_url: str) -> str:
    """Wait for the new-chat page and its E2E bridge; returns the id the page gave the chat it has not sent anything in."""
    dismiss_blocking_modals(client, page)
    wait_for_react_e2e_bridge(client, page, timeout_sec=90.0, page_url=page_url)
    ready = wait_for_state(client, page, _NEW_CHAT_READY_JS, timeout_sec=60.0, page_url=page_url)
    chat_id = ready.get("chatId")
    assert isinstance(chat_id, str), ready
    return chat_id


def set_locale_cookie(client: ChromeMcpClient, page: McpPage, locale: str | None) -> None:
    """Set the language cookie as the picker does; ``None`` removes it."""
    cookie = f"{_LOCALE_COOKIE}={locale}; path=/; max-age=31536000" if locale else f"{_LOCALE_COOKIE}=; path=/; max-age=0"
    client.evaluate(page, f"(() => {{ document.cookie = {json.dumps(cookie)}; return {{ ok: true }}; }})()", timeout_sec=15.0)


def ensure_locale(client: ChromeMcpClient, page: McpPage, locale: str, *, page_url: str) -> None:
    """Show the page in ``locale``, reloading only when it is not already in it."""
    shown = client.evaluate(page, "document.documentElement.lang", timeout_sec=15.0)
    if not (isinstance(shown, str) and shown.startswith(locale)):
        switch_locale(client, page, locale, page_url=page_url)


def switch_locale(client: ChromeMcpClient, page: McpPage, locale: str, *, page_url: str) -> None:
    """Show the page in ``locale``: set the cookie, then reload through the harness.

    The harness reload waits for the load and re-binds the private backend to the page; a bare ``location.reload()``
    returns at once and leaves the next probe racing the navigation.
    """
    set_locale_cookie(client, page, locale)
    reload_mcp_page(client, page, target_url=page_url, timeout_ms=90_000)
