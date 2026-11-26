"""Tests for surrogate character handling in text_sanitizer: removal by sanitize_text, repair by encodable_text."""

import pytest

from myrm_agent_harness.utils.text_sanitizer import encodable_text, sanitize_llm_output, sanitize_text


class TestSurrogateCharacterSanitization:
    def test_removes_unpaired_high_surrogate(self) -> None:
        """Test removal of unpaired high surrogate (U+D800-U+DBFF)."""
        text = "Hello\ud800World"
        assert sanitize_text(text) == "HelloWorld"

    def test_removes_unpaired_low_surrogate(self) -> None:
        """Test removal of unpaired low surrogate (U+DC00-U+DFFF)."""
        text = "Hello\udc00World"
        assert sanitize_text(text) == "HelloWorld"

    def test_removes_multiple_surrogates(self) -> None:
        """Test removal of multiple unpaired surrogates."""
        text = "A\ud800B\udc00C\udbffD"
        assert sanitize_text(text) == "ABCD"

    def test_preserves_valid_unicode(self) -> None:
        """Test that valid Unicode characters are preserved."""
        text = "Hello 世界  \u4e2d\u6587"
        assert sanitize_text(text) == text

    def test_surrogate_with_other_control_chars(self) -> None:
        """Test surrogate removal combined with other control chars."""
        raw = "A\ud800\x00B\ufffdC<|endoftext|>D\udc00E"
        expected = "ABCDE"
        assert sanitize_text(raw) == expected

    def test_sanitize_llm_output_uses_sanitize_text(self) -> None:
        """Test that sanitize_llm_output delegates to sanitize_text."""
        text = "Hello\ud800World"
        assert sanitize_llm_output(text) == sanitize_text(text)


class TestEncodableText:
    @pytest.mark.parametrize(
        "text", ["", "plain ascii", "naïve café", "\u4e2d\u6587", "emoji \U0001f600", "\ufffd", "\x00\x85 control"]
    )
    def test_an_encodable_text_is_the_same_object(self, text: str) -> None:
        content = "".join([text, ""])  # a fresh object, so identity below is not interning luck

        assert encodable_text(content) is content

    def test_each_lone_surrogate_becomes_a_replacement_character(self) -> None:
        assert encodable_text("a\udc80b\ud83d\ude00") == "a\ufffdb\ufffd\ufffd"

    def test_nothing_but_the_lone_surrogates_changes(self) -> None:
        assert encodable_text("keep \x85 <think>x</think> \ud800 \u4e2d") == "keep \x85 <think>x</think> \ufffd \u4e2d"

    def test_the_result_encodes_as_utf_8(self) -> None:
        assert encodable_text("a\ud800b").encode("utf-8") == b"a\xef\xbf\xbdb"
