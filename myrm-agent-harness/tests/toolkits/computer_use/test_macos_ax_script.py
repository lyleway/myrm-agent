"""macOS AX AppleScript generation: the scripts compile, caller strings stay data, a password field is never read.

Compiling sends no Apple event and the literal checks are pure computation, so no test here addresses an application.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.computer_use.perception.macos_ax.foreground import _AX_FOREGROUND_META_SCRIPT
from myrm_agent_harness.toolkits.computer_use.perception.macos_ax.scripts import (
    _AX_INVOKE_SCRIPT,
    _AX_SNAPSHOT_SCRIPT,
    _applescript_literal,
    _build_ax_invoke_script,
    _build_ax_snapshot_script,
)

requires_osascript = pytest.mark.skipif(shutil.which("osascript") is None, reason="osascript is macOS-only")
requires_osacompile = pytest.mark.skipif(shutil.which("osacompile") is None, reason="osacompile is macOS-only")

# Ends the literal it is embedded in, then continues with text that is not valid AppleScript. A script built from it
# only compiles when the string was kept inside its literal.
_BREAKOUT = '\\" ) ) garbage (( \\'


def _compiles(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    source = tmp_path / "script.applescript"
    source.write_text(script, encoding="utf-8")
    return subprocess.run(
        ["osacompile", "-o", str(tmp_path / "script.scpt"), str(source)],
        capture_output=True,
        text=True,
        timeout=30,
    )


@requires_osacompile
@pytest.mark.parametrize(
    "script",
    [_AX_SNAPSHOT_SCRIPT, _AX_INVOKE_SCRIPT, _AX_FOREGROUND_META_SCRIPT],
    ids=["snapshot", "invoke", "foreground"],
)
def test_ax_script_compiles(script: str, tmp_path: Path) -> None:
    result = _compiles(script, tmp_path)
    assert result.returncode == 0, result.stderr


@requires_osacompile
@pytest.mark.parametrize(
    "script",
    [
        _build_ax_snapshot_script(query=_BREAKOUT),
        _build_ax_snapshot_script(role=_BREAKOUT),
        _build_ax_snapshot_script(target_app=_BREAKOUT),
        _build_ax_invoke_script(target_app=_BREAKOUT),
    ],
    ids=["snapshot-query", "snapshot-role", "snapshot-app-name", "invoke-app-name"],
)
def test_caller_string_cannot_end_its_literal(script: str, tmp_path: Path) -> None:
    result = _compiles(script, tmp_path)
    assert result.returncode == 0, result.stderr


def test_ax_snapshot_script_regenerates_consistently() -> None:
    assert _build_ax_snapshot_script() == _AX_SNAPSHOT_SCRIPT


def test_identity_fields_reach_the_output_through_the_escaping_handlers() -> None:
    assert "my metaLine(appName, winTitle, bundleId, appPid)" in _AX_SNAPSHOT_SCRIPT
    assert "my foregroundLine(appName, winTitle, bundleId)" in _AX_FOREGROUND_META_SCRIPT


def test_element_text_reaches_the_output_only_through_the_escaping_handler() -> None:
    """A name and a value are read from the screen, so a row may carry them only after ``escapeText``."""
    assert "set safeName to my escapeText(elemName)" in _AX_SNAPSHOT_SCRIPT
    assert "set safeValue to my escapeText(elemValue)" in _AX_SNAPSHOT_SCRIPT
    rows = [line for line in _AX_SNAPSHOT_SCRIPT.splitlines() if "set end of outputLines to ((i as text)" in line]
    assert len(rows) == 1
    assert "& safeName &" in rows[0]
    assert "& safeValue &" in rows[0]
    assert "elemName" not in rows[0]
    assert "elemValue" not in rows[0]


def test_applescript_literal_escapes_the_backslash_before_the_quote() -> None:
    assert _applescript_literal('a"b\\c\\"') == '"a\\"b\\\\c\\\\\\""'


_LITERAL_VALUES = {
    "plain": "hello world",
    "quote": 'a"b',
    "backslash path": "C:\\Users\\me",
    "backslash quote": '\\"',
    "two backslashes quote": '\\\\"',
    "trailing backslash": "abc\\",
    "backslash n text": "\\n",
    "newline": "line1\nline2",
    "carriage return": "cr\rx",
    "tab": "tab\tx",
    "comment opener": "-- not a comment",
    "block comment opener": "(* not a comment *)",
    "line continuation": "a\u00acb",
    "cjk and emoji": "\u4f60\u597d\U0001f642",
    "pipe": "a|b",
    "vertical tab": "a\x0bb",
    "line separator": "a\u2028b",
    "breakout": _BREAKOUT,
    "arithmetic breakout": '\\" & (2+2) -- ',
    "empty": "",
}


@requires_osascript
@pytest.mark.parametrize("value", list(_LITERAL_VALUES.values()), ids=list(_LITERAL_VALUES))
def test_applescript_literal_evaluates_back_to_its_value(value: str) -> None:
    """The literal is assigned and its characters are read back; nothing outside the string is evaluated."""
    script = (
        f"set x to {_applescript_literal(value)}\n"
        "set codes to {}\n"
        "repeat with c in characters of x\n"
        "    set end of codes to (id of c)\n"
        "end repeat\n"
        'set AppleScript\'s text item delimiters to ","\n'
        "return codes as string"
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ",".join(str(ord(char)) for char in value)


def test_textedit_target_uses_bundle_id_selector() -> None:
    script = _build_ax_snapshot_script(target_app="TextEdit")
    assert 'bundle identifier is "com.apple.TextEdit"' in script
    assert 'application process "TextEdit"' not in script


def test_snapshot_script_resolves_secure_field_before_reading_its_value() -> None:
    """A password field must be identified by subrole and never have its value read.

    macOS reports a password field with role ``AXTextField``; only the subrole distinguishes it.
    The script therefore has to resolve the secure role *before* the ``value`` read, otherwise the
    secret is pulled out of the accessibility tree and can only be masked after the fact.
    """
    script = _AX_SNAPSHOT_SCRIPT
    assert "subrole of elem" in script
    assert 'set reportRole to "AXSecureTextField"' in script
    # The value read must be guarded, and the reported role must be the resolved one.
    assert 'if reportRole is not "AXSecureTextField" then' in script
    value_guard = script.index('if reportRole is not "AXSecureTextField" then')
    value_read = script.index("set elemValue to value of elem")
    assert value_guard < value_read, "the guard must precede the value read"
    assert "& reportRole &" in script


def test_secure_field_role_parses_without_exposing_its_value() -> None:
    """The secure role must survive parsing (so the step is recorded) with an empty value."""
    import subprocess
    from unittest.mock import patch

    from myrm_agent_harness.toolkits.computer_use.dref.types import is_secure_role
    from myrm_agent_harness.toolkits.computer_use.perception.macos_ax import capture

    stdout = "\n".join(
        [
            "Finder|||META|||Docs|||com.apple.finder|||123",
            "1|||AXTextField|||Search|||quarterly|||10|||20|||100|||20",
            "2|||AXSecureTextField|||Password||||||10|||60|||100|||20",
        ]
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
    with patch.object(capture, "_run_ax_snapshot", return_value=completed):
        snapshot = capture.capture_ax_snapshot("foreground")

    by_role = {ref.role: ref for ref in snapshot.refs.values()}
    assert "AXSecureTextField" in by_role, "the secure field must not be dropped from the tree"
    secure = by_role["AXSecureTextField"]
    assert secure.value == ""
    assert is_secure_role(secure.role) is True
    # ``ElementRef.name`` falls back to ``value`` when the element has no AXName, which would leak
    # the password through the name that the renderer sends to the model.
    assert "s3cr3t" not in f"{secure.name}{secure.value}"
    # A normal field keeps its value: redaction must stay scoped to secure roles.
    assert by_role["AXTextField"].value == "quarterly"
