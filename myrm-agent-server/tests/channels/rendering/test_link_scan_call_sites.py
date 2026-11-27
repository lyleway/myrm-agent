"""Each converter writes Markdown links as the pattern it used to call did, and takes linear time on hostile brackets.

The reference below is that pattern: ``\\[([^\\]]+)\\]\\(([^)]+)\\)`` (Telegram and Matrix: a target that holds NUL is no
link). A converter is compared with itself, run once with the shared scan and once with the reference put in its place,
so a call that passes the wrong shape or builds the wrong replacement shows as a different text. The pattern costs the
square of the text on a run of ``[`` with no ``]``; the converters must take time in proportion to it.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from types import ModuleType

import pytest

from app.channels.providers.matrix.html import md_to_matrix_html
from app.channels.providers.slack import format_converter as slack_converter
from app.channels.providers.telegram.html_converter import md_to_telegram_html
from app.channels.providers.whatsapp import format_converter as whatsapp_converter
from app.channels.rendering import text_utils
from app.channels.types import RenderStyle
from app.services.project import assessment_import_service

_PLAIN_STYLE = RenderStyle(format="plaintext", supports_links=False, max_text_length=4096)

# (module whose ``replace_markdown_links`` the converter calls, the converter)
_CONVERTERS: dict[str, tuple[ModuleType, Callable[[str], str]]] = {
    "plaintext": (text_utils, lambda text: text_utils.md_to_plaintext(text, _PLAIN_STYLE)),
    "telegram": (text_utils, md_to_telegram_html),
    "matrix": (text_utils, lambda text: md_to_matrix_html(text) or text),
    "whatsapp": (whatsapp_converter, whatsapp_converter.md_to_whatsapp),
    "slack": (slack_converter, slack_converter.md_to_slack_mrkdwn),
    "assessment-import": (assessment_import_service, assessment_import_service._clean_inline_markdown),
}

_PIECES = ("[", "]", "(", ")", "a", " ", "](", "[a](", "http://x.y", "**", "`", "\n", "\x00", "<", "&", "![", "~~", "# ", "_")


def _reference_replace(
    text: str,
    render: Callable[[str, str, str], str],
    *,
    image: bool | None = False,
    label_min: int = 1,
    target_min: int = 1,
    reject_in_target: str = "",
) -> str:
    assert (image, label_min, target_min) == (False, 1, 1)  # every converter reads the plain ``[label](target)``
    pattern = re.compile(rf"\[([^\]]+)\]\(([^){re.escape(reject_in_target)}]+)\)")
    return pattern.sub(lambda m: render(m.group(1), m.group(2), m.group(0)), text)


class TestSameTextAsThePattern:
    @pytest.mark.parametrize("name", sorted(_CONVERTERS))
    def test_random_markdown(self, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
        module, convert = _CONVERTERS[name]
        rng = random.Random(23)
        texts = ["".join(rng.choice(_PIECES) for _ in range(rng.randint(0, 18))) for _ in range(6_000)]

        actual = [convert(text) for text in texts]
        monkeypatch.setattr(module, "replace_markdown_links", _reference_replace)

        assert actual == [convert(text) for text in texts]

    @pytest.mark.parametrize(
        ("name", "text", "expected"),
        [
            ("plaintext", "see [docs](https://a.test) now", "see docs now"),
            ("whatsapp", "see [docs](https://a.test) now", "see docs (https://a.test) now"),
            ("slack", "see [docs](https://a.test) now", "see <https://a.test|docs> now"),
            ("telegram", "see [docs](https://a.test) now", 'see <a href="https://a.test">docs</a> now'),
            ("matrix", "see [docs](https://a.test) now", 'see <a href="https://a.test">docs</a> now'),
            ("assessment-import", "see [docs](https://a.test) now", "see docs now"),
        ],
    )
    def test_a_link_is_written_the_way_the_channel_reads_it(self, name: str, text: str, expected: str) -> None:
        assert _CONVERTERS[name][1](text) == expected

    @pytest.mark.parametrize("name", ["telegram", "matrix"])
    def test_a_target_that_holds_a_placeholder_is_no_href(self, name: str) -> None:
        converted = _CONVERTERS[name][1]("[a](`code`) and [b](https://b.test)")

        assert "\x00" not in converted
        assert converted.count("<a href=") == 1
        assert '<a href="https://b.test">b</a>' in converted


_HOSTILE: dict[str, Callable[[int], str]] = {
    "bracket-open": lambda n: "[" * n,
    "bracket-then-close": lambda n: "[" * n + "]",
    "link-without-close": lambda n: "[a](" * (n // 4),
    "link-closing-at-the-end": lambda n: "[a](" * (n // 4) + ")",
}


def _convert_in(convert: Callable[[str], str], text: str) -> float:
    """The least CPU seconds of two conversions of ``text``; the least leaves out scheduler and collector noise."""
    best = float("inf")
    for _ in range(2):
        started = time.process_time()
        convert(text)
        best = min(best, time.process_time() - started)
    return best


class TestLinearTime:
    # The pattern took 0.9 s on 20,000 characters of these. The text grows eightfold: linear time grows about eightfold,
    # quadratic time about sixty-four-fold, which a fixed limit on a few thousand characters would not tell apart.
    @pytest.mark.parametrize("family", sorted(_HOSTILE))
    @pytest.mark.parametrize("name", sorted(_CONVERTERS))
    def test_time_grows_in_proportion_to_the_text(self, name: str, family: str) -> None:
        convert = _CONVERTERS[name][1]
        short = _convert_in(convert, _HOSTILE[family](50_000))
        assert short < 1.0  # a pattern that restarts from every opener fails here, before the long text is tried

        long = _convert_in(convert, _HOSTILE[family](400_000))

        assert long < 24 * max(short, 0.001)
