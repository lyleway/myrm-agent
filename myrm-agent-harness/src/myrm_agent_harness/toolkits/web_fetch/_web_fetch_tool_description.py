"""web_fetch_tool LLM-visible description (prompt/cache SSOT).

Kept separate from ``web_fetch_agent_tools.py`` so static tests can import
without pulling fetch engine dependencies.

English and Chinese variants are both first-class LLM-facing strings.
Default locale is English; callers pass BCP-47 locale strings (e.g. ``zh-CN``).

[INPUT]
- utils.locale::is_chinese (POS: BCP-47 Chinese detection for description locale)

[OUTPUT]
- resolve_web_fetch_tool_description: Locale-aware description resolver
- DEFAULT_WEB_FETCH_TOOL_DESCRIPTION_LOCALE: Locale used when the caller passes none

[POS]
Prompt SSOT for web_fetch_tool. Imported by web_fetch_agent_tools.py and static tests.
"""

from __future__ import annotations

from myrm_agent_harness.utils.locale import is_chinese

DEFAULT_WEB_FETCH_TOOL_DESCRIPTION_LOCALE = "en"

_DESCRIPTION_EN = """Fetch the full content of known URLs as Markdown, including PDF, DOCX, XLSX and PPTX files.
When you need several pages, pass their URLs together in one call (up to 5).
Long pages come back shortened; if the full text was saved, the result says how to read the omitted parts.
WeChat Official Account articles (mp.weixin.qq.com/s/...) are supported; use the article URL exactly as the user provided it."""

_DESCRIPTION_ZH = """获取已知 URL 的完整内容（Markdown 格式），包括 PDF、DOCX、XLSX、PPTX 文件。需要读取多个页面时，请把 URL 合并到一次调用（最多 5 个）。
过长的页面会被截短返回；若完整内容已保存，结果中会说明如何读取被省略的部分。
支持微信公众号文章链接（mp.weixin.qq.com/s/...）；请原样使用用户提供的文章 URL。"""


def resolve_web_fetch_tool_description(locale: str | None = DEFAULT_WEB_FETCH_TOOL_DESCRIPTION_LOCALE) -> str:
    """Return the locale-aware tool description."""
    return _DESCRIPTION_ZH if is_chinese(locale) else _DESCRIPTION_EN


__all__ = [
    "DEFAULT_WEB_FETCH_TOOL_DESCRIPTION_LOCALE",
    "resolve_web_fetch_tool_description",
]
