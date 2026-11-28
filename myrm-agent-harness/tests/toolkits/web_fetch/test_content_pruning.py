"""ContentPruningFilter 测试"""

import pytest

from myrm_agent_harness.toolkits.web_fetch.processing.content_pruning import ContentPruningFilter


def test_filter_initialization():
    """测试过滤器初始化"""
    filter = ContentPruningFilter()

    assert filter.threshold > 0
    assert "text_density" in filter.metric_weights


def test_filter_simple_content():
    """测试简单内容过滤"""
    filter = ContentPruningFilter()

    assert filter.threshold > 0
    assert len(filter.excluded_tags) > 0


def test_filter_with_threshold():
    """测试自定义阈值"""
    filter_strict = ContentPruningFilter(threshold=0.7)
    filter_loose = ContentPruningFilter(threshold=0.3)

    assert filter_strict.threshold > filter_loose.threshold


def test_filter_content_keeps_the_article_and_drops_navigation_and_footer():
    """正文保留，导航与页脚被剪掉"""
    html_content = """
    <html>
        <body>
            <article>This is an important article. It contains a lot of very useful information.</article>
            <nav>Some links</nav>
            <footer>Copyright 2026</footer>
        </body>
    </html>
    """

    joined = "".join(ContentPruningFilter().filter_content(html_content))

    assert "This is an important article" in joined
    assert "Some links" not in joined
    assert "Copyright 2026" not in joined


def _pruned(extra_blocks: str) -> str:
    """一篇有正文的文章，加上待判定的块，返回剪枝后的 HTML"""
    html = (
        "<html><body><article><p>This is an important article. It contains a lot of very useful information.</p>"
        f"{extra_blocks}</article></body></html>"
    )
    return "".join(ContentPruningFilter().filter_content(html))


@pytest.mark.parametrize(
    "classes",
    [
        "hidden",
        "flex hidden",
        "hidden-xs",
        "is-hidden-mobile",
        "sm:hidden",
        "md:hidden",
        "lg:hidden",
        "xl:hidden",
        "2xl:hidden",
    ],
)
def test_content_hidden_for_every_viewer_or_by_breakpoint_is_pruned(classes: str) -> None:
    """恒隐藏的块与只在小屏出现的副本不是正文，剪掉"""
    kept = _pruned(f'<div class="{classes}"><p>Mobile menu copy of the page links.</p></div>')

    assert "important article" in kept
    assert "Mobile menu copy" not in kept


@pytest.mark.parametrize(
    "classes",
    [
        "dark:hidden",
        "group-hover:hidden",
        "peer-checked:hidden",
        "md:dark:hidden",
        "data-[state=closed]:hidden",
        "print:hidden",
    ],
)
def test_content_hidden_only_by_a_state_variant_is_kept(classes: str) -> None:
    """主题、指针与状态变体的 hidden 只是当下不显示，里面是真实内容，不能剪"""
    kept = _pruned(f'<div class="{classes}"><p>Install it with pip install example-package today.</p></div>')

    assert "pip install example-package" in kept


def test_a_dual_theme_code_block_keeps_the_light_copy_only() -> None:
    """文档站为两种主题各渲染一份代码：恒隐藏的那份剪掉，另一份保留，代码不会整块消失"""
    kept = _pruned(
        '<pre class="dark:hidden"><code>print("light theme copy")</code></pre>'
        '<pre class="hidden dark:block"><code>print("dark theme copy")</code></pre>'
    )

    assert "light theme copy" in kept
    assert "dark theme copy" not in kept


def test_bottom_up_metrics_match_bs4_oracle_on_4500_random_pages() -> None:
    """等价预言机：自底向上非递归度量在 4,500 个随机 HTML 页（含引号/Unicode/布尔属性）上与逐节点的 BeautifulSoup 度量完全一致。"""
    import random

    from bs4 import BeautifulSoup, Tag

    from myrm_agent_harness.toolkits.web_fetch.processing.pruning_metrics import compute_subtree_metrics

    rng = random.Random(20261123)
    container_tags = ["div", "p", "span", "a", "section", "article", "ul", "li", "strong"]
    void_tags = ["br", "hr", "img"]
    text_pieces = ["hello", " world ", "a & b < c > d", "  \t\n  ", "中文测试", "foo\"bar'baz"]
    attr_choices = [
        "",
        ' class="c1 c2" id="i1"',
        " title='say \"hi\"'",
        ' title="both &quot; and &apos;"',
        ' data-u="中文 © €" disabled',
        ' data-empty=""',
    ]

    def _gen(depth: int) -> str:
        if depth >= 5 or rng.random() < 0.3:
            if rng.random() < 0.2:
                vt = rng.choice(void_tags)
                if vt == "img":
                    return '<img alt="a &amp; b" src="https://example.com/x.png"/>'
                return f"<{vt}/>"
            return rng.choice(text_pieces)
        tag = rng.choice(container_tags)
        attr = rng.choice(attr_choices)
        if tag == "a":
            attr += ' href="https://example.com/?a=1&amp;b=2"'
        inner = "".join(_gen(depth + 1) for _ in range(rng.randint(0, 3)))
        return f"<{tag}{attr}>{inner}</{tag}>"

    for _ in range(4500):
        html = f"<html><body>{_gen(0)}</body></html>"
        soup = BeautifulSoup(html, "lxml")
        body = soup.body
        assert isinstance(body, Tag)
        text_lens, tag_lens = compute_subtree_metrics(body)
        for node in [body, *body.find_all(True)]:
            nid = id(node)
            assert text_lens[nid] == len(node.get_text(strip=True))
            assert tag_lens[nid] == len(node.encode_contents().decode("utf-8"))


def test_pruning_survives_depth_1050_without_recursion_error() -> None:
    """深度超过 1,000 层的页面不会因递归撞栈而整页丢失。"""
    depth = 1050
    article_text = "Important paragraph deep inside a nested document. " * 200
    html = (
        "<html><body><article>"
        + ("<div>" * depth)
        + f"<p>{article_text}</p>"
        + ("</div>" * depth)
        + "</article></body></html>"
    )

    kept = "".join(ContentPruningFilter().filter_content(html))

    assert "Important paragraph deep inside" in kept


@pytest.mark.parametrize(
    "hidden_markup",
    [
        '<div style="display:none">HIDDEN_TOKEN</div>',
        '<div style="color: red; display: none !important; margin: 0">HIDDEN_TOKEN</div>',
        '<span style="visibility: hidden">HIDDEN_TOKEN</span>',
        '<span style="visibility: collapse !important">HIDDEN_TOKEN</span>',
        "<p hidden>HIDDEN_TOKEN</p>",
        '<p hidden="hidden">HIDDEN_TOKEN</p>',
        '<div aria-hidden="true">HIDDEN_TOKEN</div>',
        "<template><p>HIDDEN_TOKEN</p></template>",
        "<iframe>HIDDEN_TOKEN</iframe>",
        "<noscript>HIDDEN_TOKEN</noscript>",
        "<object>HIDDEN_TOKEN</object>",
    ],
)
def test_non_visible_dom_elements_are_stripped_in_both_pruning_and_raw_markdown(hidden_markup: str) -> None:
    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown import HTML2Markdown

    html = (
        "<html><body><article>"
        "<p>Visible authoritative documentation paragraph with enough prose to survive pruning.</p>"
        f"{hidden_markup}"
        "</article></body></html>"
    )

    pruned = "".join(ContentPruningFilter().filter_content(html))
    raw_md = HTML2Markdown().handle(html)

    assert "Visible authoritative documentation paragraph" in pruned
    assert "HIDDEN_TOKEN" not in pruned
    assert "Visible authoritative documentation paragraph" in raw_md
    assert "HIDDEN_TOKEN" not in raw_md


@pytest.mark.parametrize(
    ("void_markup", "tag_name"),
    [
        ('<input type="hidden" value="SECRET_VALUE">', "input"),
        ('<embed src="https://evil.test/payload.swf">', "embed"),
    ],
)
def test_hidden_void_elements_are_decomposed_from_dom(void_markup: str, tag_name: str) -> None:
    from bs4 import BeautifulSoup, Tag

    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown_helpers import (
        is_hidden_dom_element,
        strip_non_visible_elements,
    )

    soup = BeautifulSoup(f"<html><body><article><p>Visible prose.</p>{void_markup}</article></body></html>", "lxml")
    assert isinstance(soup.body, Tag)
    target = soup.body.find(tag_name)
    assert isinstance(target, Tag)
    assert is_hidden_dom_element(target) is True

    strip_non_visible_elements(soup.body)
    assert soup.body.find(tag_name) is None
    assert "Visible prose." in soup.body.get_text()


def test_visible_styles_and_until_found_hidden_remain_intact() -> None:
    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown import HTML2Markdown

    html = (
        "<html><body><article>"
        '<p style="display: block; visibility: visible">VISIBLE_BLOCK_TEXT</p>'
        '<div hidden="until-found"><p>EXPANDABLE_SECTION_TEXT</p></div>'
        '<div aria-hidden="false"><p>ACCESSIBLE_TEXT</p></div>'
        '<input type="text" value="ok">'
        "</article></body></html>"
    )

    raw_md = HTML2Markdown().handle(html)

    assert "VISIBLE_BLOCK_TEXT" in raw_md
    assert "EXPANDABLE_SECTION_TEXT" in raw_md
    assert "ACCESSIBLE_TEXT" in raw_md


def test_hidden_dom_stripping_survives_deep_nesting_without_recursion_error() -> None:
    from bs4 import BeautifulSoup, Tag

    from myrm_agent_harness.toolkits.web_fetch.processing.html_to_markdown_helpers import strip_non_visible_elements

    depth = 1150
    html = (
        "<html><body>"
        + ("<div>" * depth)
        + '<span style="display:none">DEEP_HIDDEN</span><p>DEEP_VISIBLE</p>'
        + ("</div>" * depth)
        + "</body></html>"
    )
    soup = BeautifulSoup(html, "lxml")
    assert isinstance(soup.body, Tag)
    strip_non_visible_elements(soup.body)
    text = soup.body.get_text()
    assert "DEEP_VISIBLE" in text
    assert "DEEP_HIDDEN" not in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
