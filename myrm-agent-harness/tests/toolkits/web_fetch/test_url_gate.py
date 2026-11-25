"""Pre-fetch URL gate: the URLs of a web fetch are judged, and then fetched, in one canonical spelling.

The blocklist, the SSRF guard and every fetch tier read the URL that ``validate_urls`` returns, so a spelling a
client folds (``2130706433``, ``0x7f.1``, full-width letters, ``Straße``) can never be judged under a different host
than the one the request reaches.
"""

from __future__ import annotations

import socket

import pytest

from myrm_agent_harness.toolkits.web_fetch._url_gate import validate_urls
from myrm_agent_harness.utils.errors import ToolError

_PUBLIC_ADDRESS = "93.184.216.34"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every name resolves to one public address, so the gate never reaches the network."""

    def answer(host: str, *args: object, **kwargs: object) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC_ADDRESS, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", answer)


async def _gate(urls: list[str], *, blocked: tuple[str, ...] | None = None, private: bool = False) -> list[str]:
    return await validate_urls(urls, blocked_hostnames=blocked, allow_private_networks=private)


@pytest.mark.parametrize("private", [False, True])
async def test_the_urls_come_back_in_canonical_spelling(private: bool) -> None:
    urls = [
        "HTTPS://Example.COM./a?b=1",
        "http://münchen.de:0443/x",
        "https://ＥＸＡＭＰＬＥ。com/",
        "  https://a.test/ ",
    ]

    assert await _gate(urls, private=private) == [
        "https://example.com/a?b=1",
        "http://xn--mnchen-3ya.de:443/x",
        "https://example.com/",
        "https://a.test/",
    ]


@pytest.mark.parametrize("url", ["http://2130706433/", "http://0x7f.1:8080/", "http://0177.0.0.1/", "http://[::1]/"])
async def test_a_private_address_in_any_spelling_is_refused_unless_private_networks_are_allowed(url: str) -> None:
    with pytest.raises(ToolError, match="SSRF protection"):
        await _gate([url])

    assert len(await _gate([url], private=True)) == 1


async def test_a_private_address_is_fetched_as_the_address_it_names() -> None:
    assert await _gate(["http://2130706433:8080/x"], private=True) == ["http://127.0.0.1:8080/x"]


@pytest.mark.parametrize(
    ("entry", "url"),
    [
        ("93.184.216.34", "http://1572395042/"),
        ("xn--mnchen-3ya.de", "https://MÜNCHEN.de/x"),
        ("münchen.de", "https://xn--mnchen-3ya.de/"),
        ("*.example.org", "https://a.B.EXAMPLE.org./"),
        ("σασ.gr", "https://ΣΑΣ.gr/"),
        ("aσ", "https://aΣ/x"),
    ],
)
async def test_the_blocklist_reads_the_host_a_client_dials(entry: str, url: str) -> None:
    with pytest.raises(ToolError) as blocked:
        await _gate([url], blocked=(entry,))

    assert blocked.value.error_code == "BENCHMARK_BLOCKED_HOST"


async def test_a_final_sigma_host_is_not_the_host_its_capitals_name() -> None:
    assert await _gate(["https://ΣΑΣ.gr/"], blocked=("σας.gr",)) == ["https://xn--mxa9ab.gr/"]


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://a.test\\@b.test/", "parser-confusing"),
        ("http://127%2e0%2e0%2e1/", "percent-encoding"),
        ("http://a..b/", "Invalid hostname"),
        ("http://a.test:99999/", "Invalid port"),
        ("http://a@b@c.test/", "Malformed URL"),
        ("file:///etc/passwd", "Blocked URL scheme: file"),
        ("example.com/x", "Blocked URL scheme"),
        ("localhost:3000", "Blocked URL scheme"),
    ],
)
async def test_a_url_with_no_canonical_form_is_refused_before_any_list_reads_it(
    url: str, fragment: str, private: bool
) -> None:
    with pytest.raises(ToolError) as refused:
        await _gate([url], blocked=("example.org",), private=private)

    assert fragment in str(refused.value)
    assert refused.value.user_hint
    assert refused.value.error_code != "BENCHMARK_BLOCKED_HOST"


async def test_one_refused_url_refuses_the_whole_call() -> None:
    with pytest.raises(ToolError):
        await _gate(["https://example.com", "http://a..b/"])


async def test_a_secret_in_the_url_is_refused_unless_private_networks_are_allowed() -> None:
    url = "https://example.com/cb?api_key=sk-1234567890abcdef1234567890abcdef"

    with pytest.raises(ToolError, match="data exfiltration"):
        await _gate([url])

    assert await _gate([url], private=True) == [url]


@pytest.mark.parametrize("url", ["ws://example.com/socket", "wss://example.com/stream"])
async def test_websocket_schemes_are_refused(url: str) -> None:
    with pytest.raises(ToolError, match="Blocked URL scheme"):
        await _gate([url], private=True)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<https://example.com/doc>", "https://example.com/doc"),
        ('"https://example.com/doc"', "https://example.com/doc"),
        ("'https://example.com/doc'", "https://example.com/doc"),
        ("`https://example.com/doc`", "https://example.com/doc"),
        ("[Docs](https://example.com/doc)", "https://example.com/doc"),
        ('[Docs](<https://example.com/doc> "Title")', "https://example.com/doc"),
        ("https://example.com/doc),", "https://example.com/doc"),
        ("https://example.com/doc;", "https://example.com/doc"),
        (
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
        ),
        (
            "https://en.wikipedia.org/wiki/Python_(programming_language))",
            "https://en.wikipedia.org/wiki/Python_(programming_language)",
        ),
    ],
)
async def test_model_wrapped_http_urls_are_safely_unwrapped(raw: str, expected: str) -> None:
    assert await _gate([raw]) == [expected]


@pytest.mark.parametrize("raw", ["<file:///etc/passwd>", "[bad](javascript:alert(1))", "<example.com/path>"])
async def test_non_http_wrappers_are_still_refused(raw: str) -> None:
    with pytest.raises(ToolError):
        await _gate([raw], private=True)


@pytest.mark.parametrize(
    ("url", "ext"),
    [
        ("https://example.com/clip.mp4", "mp4"),
        ("https://example.com/release.ZIP", "zip"),
        ("https://example.com/app.dmg", "dmg"),
        ("https://example.com/font.woff2", "woff2"),
        ("https://example.com/audio.mp3?download=1", "mp3"),
    ],
)
async def test_non_document_resource_extensions_are_rejected_preflight(url: str, ext: str) -> None:
    with pytest.raises(ToolError, match=rf"\.{ext}") as exc_info:
        await _gate([url])

    assert exc_info.value.user_hint


async def test_security_gates_run_before_non_document_extension_check() -> None:
    with pytest.raises(ToolError) as blocked:
        await _gate(["https://blocked.example/video.mp4"], blocked=("blocked.example",))

    assert blocked.value.error_code == "BENCHMARK_BLOCKED_HOST"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/report.pdf",
        "https://example.com/spec.docx",
        "https://example.com/data.xlsx",
        "https://example.com/deck.pptx",
        "https://example.com/diagram.png",
        "https://example.com/photo.jpg",
        "https://example.com/download?file=archive.zip",
    ],
)
async def test_supported_document_and_query_urls_pass_preflight(url: str) -> None:
    assert await _gate([url]) == [url]
