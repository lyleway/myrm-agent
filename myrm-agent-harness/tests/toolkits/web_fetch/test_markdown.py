"""MarkdownGenerator 测试"""

import pytest

from myrm_agent_harness.toolkits.web_fetch.processing.content_pruning import ContentPruningFilter
from myrm_agent_harness.toolkits.web_fetch.processing.markdown_generator import MarkdownGenerator, MarkdownResult


def test_markdown_generator_initialization():
    """测试 Markdown 生成器初始化"""
    content_filter = ContentPruningFilter()
    generator = MarkdownGenerator(content_filter=content_filter)

    assert generator.content_filter is not None


def test_markdown_result_dataclass():
    """测试 MarkdownResult 数据类"""
    result = MarkdownResult(raw_markdown="# Title", fit_markdown="## Fit")

    assert result.raw_markdown == "# Title"
    assert result.fit_markdown == "## Fit"


def test_generate_markdown_keeps_links_inline_and_adds_no_reference_section():
    """转换结果保留行内链接，且不再追加引用章节"""
    html = '<html><body><p>See <a href="https://example.com/doc">the docs</a>.</p></body></html>'

    result = MarkdownGenerator().generate_markdown(html, base_url="https://example.com")

    assert "[the docs](https://example.com/doc)" in result.raw_markdown
    assert "References" not in result.raw_markdown
    assert result.fit_markdown == ""


def test_generate_markdown_with_filter_fills_fit_variant():
    """配置内容过滤器后产出 fit 变体"""
    html = "<html><body><article><p>" + ("lorem ipsum " * 60) + "</p></article></body></html>"

    result = MarkdownGenerator(content_filter=ContentPruningFilter()).generate_markdown(html)

    assert "lorem ipsum" in result.raw_markdown
    assert "lorem ipsum" in result.fit_markdown


def test_html_deep_nesting_depth_limit_protection():
    """测试极深嵌套 DOM (如 600 层 div) 时熔断保护，防止 RecursionError 栈溢出"""
    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown import HTML2Markdown

    depth = 600
    inner_text = "Core content deep inside 600 nested layers"
    html_content = ("<div>" * depth) + inner_text + ("</div>" * depth)

    converter = HTML2Markdown()
    # 转换必须在合理时间内顺利返回，且不得抛出 RecursionError
    result = converter.handle(html_content)

    assert inner_text in result
    assert converter._current_depth == 0


def test_html_standard_conversion_fidelity():
    """测试正常多级格式的转换保真度"""
    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown import HTML2Markdown

    html_content = """
    <html>
        <body>
            <h1>Section Title</h1>
            <p>This is a paragraph with <strong>bold</strong> and <em>italic</em>.</p>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
            </ul>
        </body>
    </html>
    """
    converter = HTML2Markdown()
    result = converter.handle(html_content)

    assert "# Section Title" in result
    assert "**bold**" in result
    assert "_italic_" in result
    assert "* Item 1" in result or "- Item 1" in result or "Item 1" in result


def test_generate_markdown_fails_closed_on_raw_conversion_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """raw 转换抛异常时返回空串并记 WARNING，绝不把报错文字当成页面内容"""
    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown import HTML2Markdown

    def _boom(_self: HTML2Markdown, _html: str) -> str:
        raise RuntimeError("x" * 200)

    monkeypatch.setattr(HTML2Markdown, "handle", _boom)
    with caplog.at_level("WARNING"):
        result = MarkdownGenerator().generate_markdown("<p>hello</p>", base_url="https://example.com")

    assert result.raw_markdown == ""
    assert result.fit_markdown == ""
    assert any("HTML-to-Markdown conversion failed" in r.message for r in caplog.records)


def test_generate_markdown_fails_closed_on_filter_error(caplog: pytest.LogCaptureFixture) -> None:
    """内容过滤器抛异常时 fit_markdown 返回空串并记 WARNING，不污染正文"""

    class _ExplodingFilter:
        def filter_content(self, _html: str) -> list[str]:
            raise RuntimeError("filter boom " + "y" * 200)

    with caplog.at_level("WARNING"):
        result = MarkdownGenerator(content_filter=_ExplodingFilter()).generate_markdown(
            "<p>hello world</p>", base_url="https://example.com"
        )

    assert "hello world" in result.raw_markdown
    assert result.fit_markdown == ""
    assert any("Fit Markdown generation failed" in r.message for r in caplog.records)


def test_generate_markdown_fails_closed_on_setup_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """外层初始化抛异常时返回空 MarkdownResult 并记 WARNING"""
    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown import HTML2Markdown

    def _bad_init(_self: HTML2Markdown, **_kwargs: object) -> None:
        raise RuntimeError("init boom")

    monkeypatch.setattr(HTML2Markdown, "__init__", _bad_init)
    with caplog.at_level("WARNING"):
        result = MarkdownGenerator().generate_markdown("<p>hello</p>", base_url="https://example.com")

    assert result == MarkdownResult(raw_markdown="", fit_markdown="")
    assert any("Markdown generation setup failed" in r.message for r in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
