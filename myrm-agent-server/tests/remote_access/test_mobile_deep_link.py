"""Mobile status deep link builder tests."""

from __future__ import annotations

import pytest

from app.channels.i18n import channel_t
from app.channels.types import ButtonStyle
from app.config.settings import settings
from app.remote_access.mobile_deep_link import (
    build_mobile_status_deep_link,
    resolve_mobile_status_action_components,
    resolve_mobile_status_deep_link,
)
from app.remote_access.pairing import parse_pairing_token

_BASE = "https://abc.trycloudflare.com"


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.database, "state_dir", str(tmp_path))


def _serve_from(monkeypatch: pytest.MonkeyPatch, base: str) -> None:
    """Make ``base`` the public address links are built on (an empty string: the instance has none)."""

    async def _base(fallback: str = "") -> str:
        return base or fallback

    monkeypatch.setattr("app.core.infra.ingress.resolve_public_link_base", _base)


def test_build_mobile_status_deep_link_includes_scoped_pair() -> None:
    url = build_mobile_status_deep_link(chat_id="chat-9", base_url=_BASE)
    assert url is not None
    assert url.startswith(f"{_BASE}/mobile/status/chat-9?pair=")
    token = url.split("pair=", 1)[1]
    parsed = parse_pairing_token(token)
    assert parsed is not None
    assert parsed["chat_id"] == "chat-9"


def test_build_mobile_status_deep_link_returns_none_without_base() -> None:
    assert build_mobile_status_deep_link(chat_id="chat-1", base_url="") is None


async def test_the_status_link_is_built_on_the_public_address_of_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_from(monkeypatch, _BASE)

    link = await resolve_mobile_status_deep_link("chat-9")

    assert link is not None
    assert link.startswith(f"{_BASE}/mobile/status/chat-9?pair=")


async def test_the_status_action_row_holds_one_primary_button_to_the_scoped_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_from(monkeypatch, _BASE)

    rows = await resolve_mobile_status_action_components("chat-9")

    assert [len(row) for row in rows] == [1]
    button = rows[0][0]
    assert button.action_id == "mobile:open_status"
    assert button.style is ButtonStyle.PRIMARY
    assert button.label == channel_t("en", "mobile_hitl_open")
    assert button.url.startswith(f"{_BASE}/mobile/status/chat-9?pair=")


async def test_the_status_action_label_follows_the_locale_and_the_key_the_caller_names(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_from(monkeypatch, _BASE)

    chinese = (await resolve_mobile_status_action_components("chat-9", locale="zh-CN"))[0][0]
    renamed = (await resolve_mobile_status_action_components("chat-9", label_key="web_continue_chat"))[0][0]

    assert chinese.label == channel_t("zh-CN", "mobile_hitl_open")
    assert chinese.label != channel_t("en", "mobile_hitl_open")
    assert renamed.label == channel_t("en", "web_continue_chat")


async def test_the_status_action_row_is_empty_when_the_instance_has_no_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_from(monkeypatch, "")

    assert await resolve_mobile_status_action_components("chat-9") == ()
