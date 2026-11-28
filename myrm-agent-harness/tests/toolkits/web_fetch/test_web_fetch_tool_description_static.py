"""Static guards for web_fetch_tool description (locale SSOT)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from myrm_agent_harness.agent._internals._agent_build import _weave_dynamic_schemas
from myrm_agent_harness.toolkits.browser.tools.navigate import create_navigate_tool
from myrm_agent_harness.toolkits.web_fetch._web_fetch_tool_description import (
    DEFAULT_WEB_FETCH_TOOL_DESCRIPTION_LOCALE,
    resolve_web_fetch_tool_description,
)
from myrm_agent_harness.toolkits.web_fetch.web_fetch_agent_tools import (
    create_web_fetch_tool,
)

_LOCALES = ("en", "zh-CN")


def test_default_locale_is_english() -> None:
    assert DEFAULT_WEB_FETCH_TOOL_DESCRIPTION_LOCALE == "en"
    assert resolve_web_fetch_tool_description() == resolve_web_fetch_tool_description(None)
    assert resolve_web_fetch_tool_description() == resolve_web_fetch_tool_description("en")


def test_description_keeps_the_wechat_guardrail_en() -> None:
    assert "mp.weixin.qq.com" in resolve_web_fetch_tool_description("en")
    assert "use the article URL exactly as the user provided it" in resolve_web_fetch_tool_description("en")


def test_description_keeps_the_wechat_guardrail_zh() -> None:
    assert "mp.weixin.qq.com" in resolve_web_fetch_tool_description("zh-CN")
    assert "请原样使用用户提供的文章 URL" in resolve_web_fetch_tool_description("zh-CN")


def test_description_explains_that_long_pages_are_shortened() -> None:
    """The full read cuts long pages; the model must be told how to read on."""
    assert "Long pages come back shortened" in resolve_web_fetch_tool_description("en")
    assert "过长的页面会被截短返回" in resolve_web_fetch_tool_description("zh-CN")


def test_description_never_points_at_browser_tools() -> None:
    """Routing between reading and browsing lives on the navigation tool, so this text never dangles without it."""
    for locale in (None, *_LOCALES):
        desc = resolve_web_fetch_tool_description(locale)
        assert "browser" not in desc.lower(), f"dangling browser reference ({locale=})"
        assert "浏览器" not in desc, f"dangling browser reference ({locale=})"


def test_web_fetch_tool_description_never_leaks_policy_details() -> None:
    """Blocklist/decontamination internals must never reach the LLM prompt."""
    for locale in (None, *_LOCALES):
        desc = resolve_web_fetch_tool_description(locale)
        for leaked in ("blocked", "decontam", "Hugging Face", "benchmark"):
            assert leaked not in desc, f"policy implementation detail leaked into prompt: {leaked!r}"


def test_description_describes_only_what_the_schema_accepts() -> None:
    """The tool has no ``operation`` or ``questions`` argument, so the description must not name one."""
    limit = create_web_fetch_tool().get_input_jsonschema()["properties"]["urls"]["maxItems"]
    for locale, limit_phrase in (("en", f"up to {limit}"), ("zh-CN", f"最多 {limit} 个")):
        desc = resolve_web_fetch_tool_description(locale)
        assert "fetch_and_extract" not in desc
        assert "fetch_full_content" not in desc
        assert "operation" not in desc.lower()
        assert "questions" not in desc.lower()
        assert limit_phrase in desc, "the per-call URL limit must be stated and match the schema"


def test_tool_schema_takes_urls_and_reason_only() -> None:
    schema = create_web_fetch_tool().get_input_jsonschema()

    assert set(schema["properties"]) == {"urls", "reason"}
    assert set(schema["required"]) == {"urls", "reason"}
    assert (schema["properties"]["urls"]["minItems"], schema["properties"]["urls"]["maxItems"]) == (1, 5)


@pytest.mark.asyncio
async def test_tool_refuses_more_urls_than_its_limit() -> None:
    tool = create_web_fetch_tool()
    urls = [f"https://example.com/{i}" for i in range(6)]

    with pytest.raises(ValidationError):
        await tool.ainvoke({"urls": urls, "reason": "too many"})


def test_urls_field_forbids_inventing_urls_and_states_the_limit() -> None:
    urls = create_web_fetch_tool().get_input_jsonschema()["properties"]["urls"]
    assert "never invent URLs" in urls["description"]
    assert f"up to {urls['maxItems']}" in urls["description"]


def test_create_web_fetch_tool_default_english_description() -> None:
    tool = create_web_fetch_tool()
    assert _weave_dynamic_schemas([tool])[0].description == resolve_web_fetch_tool_description("en")


def test_create_web_fetch_tool_chinese_locale() -> None:
    tool = create_web_fetch_tool(description_locale="zh-CN")
    woven = _weave_dynamic_schemas([tool])[0]
    assert woven.description == resolve_web_fetch_tool_description("zh-CN")
    assert woven.description.startswith("获取已知 URL 的完整内容")


def test_description_names_the_document_formats_it_reads() -> None:
    """Without this sentence the model detours through bash to read a document the tool handles natively."""
    assert "PDF, DOCX, XLSX and PPTX" in resolve_web_fetch_tool_description("en")
    assert "PDF、DOCX、XLSX、PPTX" in resolve_web_fetch_tool_description("zh-CN")


def test_navigation_tool_routes_read_only_reads_to_web_fetch_only_while_it_is_mounted() -> None:
    """The web_fetch description carries no routing text, so this dynamic hint is the only guard against browsing for plain reads."""
    navigate = create_navigate_tool(MagicMock())
    web_fetch = create_web_fetch_tool()

    alone = _weave_dynamic_schemas([navigate])[0].description
    with_fetch = _weave_dynamic_schemas([navigate, web_fetch])[0].description

    assert web_fetch.name not in alone
    assert f"prefer {web_fetch.name}" in with_fetch
    assert "no interaction needed" in with_fetch
