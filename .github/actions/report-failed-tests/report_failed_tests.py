"""Say which tests of a pytest run failed, in the part of a failed job that needs no sign-in.

The step summary lists every failing test with the first lines of its message, but only a signed-in reader can open it.
Annotations are shown on the run page to anyone, and GitHub keeps ten error annotations per step: the first annotation
lists every failing test, so the reader never takes the first nine for the whole set, and the nine after it carry the
message of each of those tests.

Usage: report_failed_tests.py JUNIT_GLOB. Always exits 0; the job is already red when this runs.
"""

from __future__ import annotations

import glob
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import NamedTuple

ANNOTATION_LIMIT = 10
# GitHub rejects an annotation message of more than 64 KB.
LISTING_CHARS = 60_000
MESSAGE_LINES = 2
DETAIL_CHARS = 180


class Failure(NamedTuple):
    classname: str
    name: str
    message: tuple[str, ...]

    @property
    def identifier(self) -> str:
        return f"{self.classname}::{self.name}"


def read_failures(pattern: str) -> list[Failure]:
    """Every failed or errored test case of the junit files the pattern names, in file then document order."""
    failures: list[Failure] = []
    for path in sorted(glob.glob(pattern)):
        try:
            root = ET.parse(path).getroot()  # noqa: S314 — junit written by this job's own pytest run, not external input
        except (ET.ParseError, OSError):
            continue
        for case in root.iter("testcase"):
            for tag in ("failure", "error"):
                node = case.find(tag)
                if node is not None:
                    lines = (node.get("message") or "").strip().splitlines()[:MESSAGE_LINES]
                    failures.append(Failure(case.get("classname") or "", case.get("name") or "", tuple(lines)))
    return failures


def summary_markdown(failures: Iterable[Failure]) -> str:
    rows = ["## Failed tests"]
    listed = False
    for failure in failures:
        listed = True
        rows.append(f"- `{failure.identifier}`")
        rows.extend(f"  - {line}" for line in failure.message)
    if not listed:
        rows.append("No failures recorded in the junit report.")
    return "\n".join(rows) + "\n"


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")


def _error(title: str, message: str) -> str:
    return f"::error title={_escape_property(title)}::{_escape_data(message)}"


def _listing(failures: list[Failure]) -> str:
    """One test per line; a set too long for an annotation ends with the number of tests left out, never a cut name."""
    lines: list[str] = []
    used = 0
    for failure in failures:
        used += len(failure.identifier) + 1
        if used > LISTING_CHARS:
            lines.append(f"... and {len(failures) - len(lines)} more, named in the step summary")
            break
        lines.append(failure.identifier)
    return "\n".join(lines)


def annotations(failures: list[Failure]) -> list[str]:
    """The workflow commands to print: the whole set first, then the message of the first few tests."""
    if not failures:
        return [_error("failed-tests", "no junit entries were written")]
    lines = [_error(f"failed-tests ({len(failures)})", _listing(failures))]
    for failure in failures[: ANNOTATION_LIMIT - 1]:
        detail = " | ".join(failure.message).replace("\n", " ")[:DETAIL_CHARS]
        lines.append(_error(failure.classname, f"{failure.name}::{detail}"))
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: report_failed_tests.py JUNIT_GLOB", file=sys.stderr)
        return 2
    failures = read_failures(argv[1])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(summary_markdown(failures))
    for line in annotations(failures):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
