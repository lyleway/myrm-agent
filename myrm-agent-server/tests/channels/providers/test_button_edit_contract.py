"""Static guard: a channel that declares native buttons has a test of its placeholder edit.

Nothing re-sends the buttons of a reply once its placeholder is replaced, so each channel that declares
``capabilities.buttons`` needs a test that fails when its ``edit_placeholder_message`` drops them. A channel that
starts declaring buttons fails here until it has that test and is named in ``_EDIT_COVERED``.
"""

from __future__ import annotations

from pathlib import Path

from app.channels.providers.registry import _BUILTIN_SPECS, get_channel_class_safe

_TESTS_ROOT = Path(__file__).resolve().parents[2]

# Registry name -> the test (under ``tests/``) that fails when the placeholder edit of that channel drops the buttons.
_EDIT_COVERED = {
    "feishu": "channels/providers/feishu/test_feishu_channel.py",
    "slack": "channels/providers/slack/test_slack_message_blocks.py",
    "teams": "channels/providers/msteams/test_msteams_placeholder_edit.py",
    "telegram": "channels/providers/telegram/test_telegram_placeholder_buttons.py",
}


def test_every_channel_that_declares_buttons_has_a_test_of_its_placeholder_edit() -> None:
    declaring = {
        name for name in _BUILTIN_SPECS if (cls := get_channel_class_safe(name)) is not None and cls.capabilities.buttons
    }

    unaudited = sorted(declaring - _EDIT_COVERED.keys())
    assert not unaudited, (
        f"{unaudited} declare capabilities.buttons: add a test showing edit_placeholder_message keeps the buttons "
        "and name it in _EDIT_COVERED"
    )


def test_the_tests_named_as_covering_a_placeholder_edit_exist() -> None:
    missing = sorted(path for path in _EDIT_COVERED.values() if not (_TESTS_ROOT / path).is_file())

    assert not missing, f"_EDIT_COVERED names tests that do not exist: {missing}"
