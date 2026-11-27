"""Texts shared by the runtimes: what a tool call works on, what a failure said and how messages are set apart.

[INPUT]
no — leaf module, standard library only

[OUTPUT]
- tool_summary: one short line telling what a tool call works on
- MAX_TOOL_OUTPUT_CHARS: the most a tool result carries (one result can be a whole file; consumers show an excerpt)
- condense_failure_text / FAILURE_TEXT_CHARS: an agent's failure text as one bounded line (a quoted web page shrinks to its title)
- paragraph_break: the newlines that set a message apart from the text before it (a tool call came between)

[POS]
Text shaping of the events every runtime emits, kept in one place so a tool call or a failure reads the same whichever
agent produced it.
"""

from __future__ import annotations

import re

# One tool result can be a whole file; consumers only show a short excerpt of it.
MAX_TOOL_OUTPUT_CHARS = 2000
_MAX_SUMMARY_CHARS = 80
_SUMMARY_KEYS = ("file_path", "path", "command", "pattern", "query", "url", "description")


def tool_summary(tool_input: object) -> str:
    """One short line telling what a tool call works on (a path, a command, a query); empty when nothing fits."""
    if not isinstance(tool_input, dict):
        return ""
    for key in _SUMMARY_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            first_line = value.strip().splitlines()[0].strip()
            if len(first_line) > _MAX_SUMMARY_CHARS:
                return first_line[: _MAX_SUMMARY_CHARS - 1] + "…"
            return first_line
    return ""


def paragraph_break(before: str, after: str) -> str:
    """The newlines that set ``after`` apart from ``before`` by a blank line, counting the ones already there.

    An agent streams the text before and after a tool call as separate messages with no break between them, which
    read as one run-on sentence once the tool call is not shown inline. Nothing precedes the first message.
    """
    if not before:
        return ""
    present = len(before) - len(before.rstrip("\n")) + len(after) - len(after.lstrip("\n"))
    return "\n" * max(0, 2 - present)


FAILURE_TEXT_CHARS = 500

# When a proxy, a firewall or a region block answers instead of the model API, the agent quotes the whole web page.
_WEB_PAGE = re.compile(r"<(?:!doctype\s+html|html)\b.*?(?:</html>|\Z)", re.IGNORECASE | re.DOTALL)
_WEB_PAGE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def _web_page_marker(page: re.Match[str]) -> str:
    title = _WEB_PAGE_TITLE.search(page.group())
    name = _WHITESPACE.sub(" ", title.group(1)).strip() if title else ""
    return f"[web page: {name}]" if name else "[web page]"


def condense_failure_text(text: str) -> str:
    """A CLI's failure text as one bounded line: a quoted web page shrinks to its title, whitespace collapses.

    Failure text is shown to a person and logged, so the full page or stack a CLI may print has no place in it;
    ordinary one-line messages pass through unchanged.
    """
    condensed = _WHITESPACE.sub(" ", _WEB_PAGE.sub(_web_page_marker, text)).strip()
    if len(condensed) <= FAILURE_TEXT_CHARS:
        return condensed
    return condensed[: FAILURE_TEXT_CHARS - 1] + "…"
