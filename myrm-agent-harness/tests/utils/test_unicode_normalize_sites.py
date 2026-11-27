"""The code that normalizes text from a file, a model or a page hands ``unicodedata`` runs of at most 30 marks."""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from collections.abc import Callable
from functools import partial

import pytest

from myrm_agent_harness.core.security.path.respelling import generate_unicode_path_candidates
from myrm_agent_harness.eval.decontam import normalize_answer
from myrm_agent_harness.toolkits.memory._internal.hash_utils import compute_normalized_hash
from myrm_agent_harness.toolkits.memory.strategies.consolidation_closure import facts_of
from myrm_agent_harness.utils.unicode_normalize import NormalizationForm

_LIMIT = 30
_NFKD = partial(unicodedata.normalize, "NFKD")

# Marks of two combining classes in turn; the invisible separator between them is removed by the path stage that maps
# invisible characters away, which joins the marks into one long run only after that mapping.
_STACK = "\u0301\u0316" * 3_000
_SEPARATED_STACK = "\u0301\u200b\u0316\u200b" * 1_500

_SITES: dict[str, Callable[[str], object]] = {
    "normalized-hash": compute_normalized_hash,
    "consolidation-closure": facts_of,
    "answer-match": normalize_answer,
    "path-respelling": generate_unicode_path_candidates,
}


def _longest_run(text: str) -> int:
    longest = current = 0
    for char in _NFKD(text):
        current = current + 1 if unicodedata.combining(char) else 0
        longest = max(longest, current)
    return longest


@pytest.mark.parametrize("site", _SITES)
@pytest.mark.parametrize("stack", [_STACK, _SEPARATED_STACK], ids=["marks", "marks-joined-by-a-later-stage"])
def test_a_stack_of_marks_never_reaches_unicodedata_unbounded(
    monkeypatch: pytest.MonkeyPatch, site: str, stack: str
) -> None:
    real = unicodedata.normalize
    longest: list[int] = []

    def spy(form: NormalizationForm, text: str) -> str:
        longest.append(_longest_run(text))
        return real(form, text)

    monkeypatch.setattr(unicodedata, "normalize", spy)

    _SITES[site]("price 4500 /tmp/a" + stack)

    assert longest
    assert max(longest) <= _LIMIT


def _hash_with_plain_nfkc(content: str) -> str:
    text = unicodedata.normalize("NFKC", content).lower()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class TestNormalizedHash:
    _WORDS = (
        "Hello",
        "ÉCOLE",
        "naïve",
        "ภาษาไทย",
        "اللُّغَةُ",
        "Tiếng",
        "中文",
        "がぎぐ",
        "한국어",
        "ﬁne",
        "①②",
        "Ⅻ",
        "x\u0301",
    )
    _GLUE = (" ", "  ", "\n", ", ", "-", "\u0301", "\u0316\u0301", "\u0344", "\u200b")

    def test_is_the_hash_of_plain_nfkc_for_text_within_the_limit(self) -> None:
        rnd = random.Random(2026)
        for _ in range(5_000):
            text = "".join(rnd.choice(self._WORDS) + rnd.choice(self._GLUE) for _ in range(rnd.randint(1, 12)))

            assert compute_normalized_hash(text) == _hash_with_plain_nfkc(text), text.encode("unicode_escape")

    def test_marks_past_the_limit_are_dropped_from_the_hash_as_before(self) -> None:
        # Normalization keeps the marks and the hash then strips them, so the joiners the guard adds leave no trace.
        text = "cafe" + "\u0301" * 40 + " au lait"

        assert compute_normalized_hash(text) == _hash_with_plain_nfkc(text)
