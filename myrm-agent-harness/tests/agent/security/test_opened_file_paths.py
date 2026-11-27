"""The files a call opens are not the spellings its verdict judges.

A verdict errs wide, since judging one spelling more can only tighten it. A grant must not: it opens the folder of
each file, and a spelling that names nothing falls back to the folder above. These tests pin the narrow set, and that
the wide set always contains it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from myrm_agent_harness.agent.security.judged_paths import judged_file_paths, opened_file_paths

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create names that end in a space")


def test_a_tool_other_than_a_read_opens_exactly_the_text_it_is_given() -> None:
    args: dict[str, object] = {"path": "/x/a.txt ", "file_path": "/x/v:2", "paths": ["/x/b.txt:3-9"]}

    assert opened_file_paths("file_write", args, "/ws") == ["/x/a.txt ", "/x/b.txt:3-9", "/x/v:2"]


def test_a_read_opens_its_path_without_the_line_range() -> None:
    args: dict[str, object] = {"paths": ["/x/f.txt:3-9", "/x/g.txt:4-", "/x/h:v2.txt"]}

    assert opened_file_paths("file_read", args, "/ws") == ["/x/f.txt", "/x/g.txt", "/x/h:v2.txt"]


def test_a_read_opens_the_respelling_in_place_of_a_typed_path_that_names_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("x", encoding="utf-8")

    opened = opened_file_paths("file_read", {"paths": [f'"{tmp_path}/a.csv"']}, str(tmp_path / "ws"))

    assert opened == [str(tmp_path / "a.csv")]


@POSIX_ONLY
def test_a_read_opens_the_typed_spelling_when_it_exists_though_its_trimmed_twin_is_judged_too(tmp_path: Path) -> None:
    folder = tmp_path / "d "
    folder.mkdir()
    args: dict[str, object] = {"path": str(folder)}

    assert opened_file_paths("file_read", args, str(tmp_path)) == [str(folder)]
    assert str(folder).strip() in judged_file_paths("file_read", args, str(tmp_path))


def test_urls_and_virtual_files_name_no_host_file() -> None:
    args: dict[str, object] = {"paths": ["https://example.com/a", "vault://x", "/mcp/skill/SKILL.md", "/x/a.txt"]}

    assert opened_file_paths("file_read", args, None) == ["/x/a.txt"]


def test_a_path_list_sent_as_json_text_is_read_as_the_list() -> None:
    assert opened_file_paths("file_write", {"paths": '["/x/a.txt", "/x/b.txt"]'}, None) == ["/x/a.txt", "/x/b.txt"]


def test_the_same_file_is_listed_once() -> None:
    assert opened_file_paths("file_write", {"path": "/x/a.txt", "paths": ["/x/a.txt"]}, None) == ["/x/a.txt"]


@pytest.mark.parametrize(
    ("permission", "args"),
    [
        ("file_read", {"path": "/x/a.txt "}),
        ("file_read", {"paths": [" /x/a.txt", "/x/b.txt:3-9"]}),
        ("file_read", {"file_path": "/x/v:2"}),
        ("file_read", {"paths": ["/x/f.txt:3-"]}),
        ("file_write", {"path": "/x/a.txt "}),
        ("file_write", {"paths": [" /x/a.txt", "/x/b.txt"]}),
        ("file_write", {"file_path": "/x/v:2"}),
        ("file_write", {"paths": ' ["/x/a.txt "]'}),
    ],
)
def test_every_file_a_call_opens_is_among_the_files_its_verdict_judges(
    permission: str, args: dict[str, object]
) -> None:
    assert set(opened_file_paths(permission, args, "/ws")) <= set(judged_file_paths(permission, args, "/ws"))
