"""The failed-tests report names every failing test of a pytest run where a reader of the run page sees it.

[INPUT]
- .github/actions/report-failed-tests/report_failed_tests.py (POS: the script, run by path the way the action runs it)
- .github/actions/report-failed-tests/action.yml (POS: the composite action that runs the script)
- .github/workflows/*.yml (POS: the jobs that write a junit report and the step that reports it)
- scripts/ci/run_default_tests.sh (POS: writes the junit report the server job reads)

[OUTPUT]
- what the script reads (failures and errors in file then document order, unreadable files skipped), prints (the whole set
  first, the message of the first nine after it, ten annotations at most, workflow-command characters escaped, the listing
  under GitHub's size limit, a notice when nothing was written) and writes (the step summary), and that it exits 0
- that every job which writes a junit report calls the action after its tests with ``if: failure()`` and the same path, that
  the server job reads the path its runner writes, and that the action runs the script on its input

[POS]
GitHub keeps ten error annotations per step and shows a job's log only to a signed-in reader, so the annotations are what
decides whether a failed run can be read at all. A report that names the first nine failures reads as the whole set, and a job
that writes a junit report nobody reports leaves the reader with ``Process completed with exit code 1``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import cast
from urllib.parse import unquote
from xml.etree import ElementTree as ET

import pytest
import yaml

from tests.support.desktop_release import ACTIONS, REPO_ROOT, WORKFLOWS, YamlMapping, workflow_jobs

_ACTION_DIR = ACTIONS / "report-failed-tests"
_SCRIPT = _ACTION_DIR / "report_failed_tests.py"
_USES = "./.github/actions/report-failed-tests"
_ANNOTATIONS_PER_STEP = 10
_ANNOTATION_BYTES = 64 * 1024
_JUNIT_FLAG = re.compile(r"--junitxml=([^\s)]+)")

Case = tuple[str, str, str, str]  # classname, name, kind (pass / skip / failure / error), message


def _junit(path: Path, cases: Iterable[Case]) -> Path:
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for classname, name, kind, message in cases:
        case = ET.SubElement(suite, "testcase", classname=classname, name=name)
        if kind == "skip":
            ET.SubElement(case, "skipped", message="skipped")
        elif kind in ("failure", "error"):
            ET.SubElement(case, kind, message=message).text = message
    ET.ElementTree(root).write(path)
    return path


def _failing(count: int) -> list[Case]:
    return [(f"tests.test_mod{n}.TestCase", f"test_{n}", "failure", f"assert {n} == 0") for n in range(count)]


def _run(*args: str, summary: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"}
    if summary is not None:
        env["GITHUB_STEP_SUMMARY"] = str(summary)
    return subprocess.run([sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, timeout=60, check=False, env=env)


def _annotations(result: subprocess.CompletedProcess[str]) -> list[tuple[str, str]]:
    """(title, message) of every error command the script printed, unescaped the way GitHub shows them."""
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"::error title=(.*?)::(.*)", line)
        assert match is not None, line
        rows.append((unquote(match.group(1)), unquote(match.group(2))))
    return rows


class TestWhatTheScriptReportsAndPrints:
    def test_failures_and_errors_are_listed_and_passes_and_skips_are_not(self, tmp_path: Path) -> None:
        junit = _junit(
            tmp_path / "unit.xml",
            [
                ("tests.a.TestA", "test_pass", "pass", ""),
                ("tests.a.TestA", "test_fail", "failure", "assert 1 == 2"),
                ("tests.a.TestA", "test_skip", "skip", ""),
                ("tests.b", "test_error", "error", "fixture 'db' not found"),
            ],
        )

        result = _run(str(junit))

        assert result.returncode == 0
        title, listing = _annotations(result)[0]
        assert title == "failed-tests (2)"
        assert listing.split("\n") == ["tests.a.TestA::test_fail", "tests.b::test_error"]

    def test_the_first_annotation_lists_every_test_and_the_step_stays_within_ten(self, tmp_path: Path) -> None:
        junit = _junit(tmp_path / "unit.xml", _failing(25))

        annotations = _annotations(_run(str(junit)))

        assert len(annotations) == _ANNOTATIONS_PER_STEP
        title, listing = annotations[0]
        assert title == "failed-tests (25)"
        assert listing.split("\n") == [f"tests.test_mod{n}.TestCase::test_{n}" for n in range(25)]
        assert [t for t, _ in annotations[1:]] == [f"tests.test_mod{n}.TestCase" for n in range(9)]
        assert annotations[1][1] == "test_0::assert 0 == 0"

    def test_fewer_failures_than_the_limit_print_one_annotation_each_after_the_listing(self, tmp_path: Path) -> None:
        annotations = _annotations(_run(str(_junit(tmp_path / "unit.xml", _failing(3)))))

        assert len(annotations) == 4

    def test_a_listing_too_long_for_an_annotation_ends_with_the_number_left_out_not_with_a_cut_name(self, tmp_path: Path) -> None:
        cases = [("tests." + "m" * 150, f"test_{n:03d}_" + "x" * 150, "failure", "boom") for n in range(600)]

        title, listing = _annotations(_run(str(_junit(tmp_path / "unit.xml", cases))))[0]

        *named, left_out = listing.split("\n")
        assert title == "failed-tests (600)"
        assert len(listing.encode()) < _ANNOTATION_BYTES
        assert all(re.fullmatch(r"tests\.m{150}::test_\d{3}_x{150}", name) for name in named)
        assert named[0].endswith("test_000_" + "x" * 150)
        assert left_out == f"... and {600 - len(named)} more, named in the step summary"
        assert 0 < len(named) < 600

    def test_a_message_is_cut_to_two_lines_and_to_a_fixed_length(self, tmp_path: Path) -> None:
        message = "first line\nsecond line\nthird line"
        long_message = "y" * 500
        junit = _junit(
            tmp_path / "unit.xml",
            [("tests.a", "test_lines", "failure", message), ("tests.a", "test_long", "failure", long_message)],
        )

        annotations = _annotations(_run(str(junit)))

        assert annotations[1][1] == "test_lines::first line | second line"
        assert annotations[2][1] == "test_long::" + "y" * 180

    def test_characters_a_workflow_command_reads_as_syntax_are_escaped(self, tmp_path: Path) -> None:
        junit = _junit(tmp_path / "unit.xml", [("tests.a:b,c", "test_%", "failure", "100% sure")])

        result = _run(str(junit))
        listing_line, detail_line = result.stdout.splitlines()

        assert listing_line == "::error title=failed-tests (1)::tests.a:b,c::test_%25"
        assert detail_line == "::error title=tests.a%3Ab%2Cc::test_%25::100%25 sure"

    def test_files_that_cannot_be_read_are_skipped_and_the_others_are_read(self, tmp_path: Path) -> None:
        (tmp_path / "broken.xml").write_text("<testsuites><testcase")
        _junit(tmp_path / "good.xml", _failing(1))

        annotations = _annotations(_run(str(tmp_path / "*.xml")))

        assert annotations[0][0] == "failed-tests (1)"

    def test_every_file_the_pattern_names_is_read_in_name_order(self, tmp_path: Path) -> None:
        _junit(tmp_path / "b.xml", [("tests.b", "test_b", "failure", "b")])
        _junit(tmp_path / "a.xml", [("tests.a", "test_a", "failure", "a")])

        _title, listing = _annotations(_run(str(tmp_path / "*.xml")))[0]

        assert listing.split("\n") == ["tests.a::test_a", "tests.b::test_b"]

    def test_a_run_that_wrote_no_report_says_so(self, tmp_path: Path) -> None:
        summary = tmp_path / "summary.md"

        result = _run(str(tmp_path / "missing.xml"), summary=summary)

        assert result.returncode == 0
        assert _annotations(result) == [("failed-tests", "no junit entries were written")]
        assert "No failures recorded in the junit report." in summary.read_text(encoding="utf-8")

    def test_the_step_summary_lists_every_failure_with_its_message(self, tmp_path: Path) -> None:
        summary = tmp_path / "summary.md"
        junit = _junit(tmp_path / "unit.xml", _failing(12))

        _run(str(junit), summary=summary)

        text = summary.read_text(encoding="utf-8")
        assert text.startswith("## Failed tests\n")
        assert text.count("- `tests.test_mod") == 12
        assert "  - assert 11 == 0" in text

    def test_the_summary_is_appended_to_what_other_steps_wrote(self, tmp_path: Path) -> None:
        summary = tmp_path / "summary.md"
        summary.write_text("earlier step\n", encoding="utf-8")

        _run(str(_junit(tmp_path / "unit.xml", _failing(1))), summary=summary)

        assert summary.read_text(encoding="utf-8").startswith("earlier step\n## Failed tests\n")

    @pytest.mark.parametrize("args", [(), ("a.xml", "b.xml")])
    def test_a_wrong_number_of_arguments_is_a_usage_error(self, args: tuple[str, ...]) -> None:
        result = _run(*args)

        assert result.returncode == 2
        assert "usage:" in result.stderr


def _steps(job: YamlMapping) -> list[YamlMapping]:
    """The steps of a job; a job that calls a reusable workflow has none."""
    return cast("list[YamlMapping]", job.get("steps", []))


def _junit_jobs() -> dict[tuple[str, str], str]:
    """(workflow file, job id) -> the junit path of every job that passes ``--junitxml`` to pytest in its own steps."""
    found: dict[tuple[str, str], str] = {}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for job_id, job in workflow_jobs(workflow.name).items():
            for step in _steps(job):
                match = _JUNIT_FLAG.search(str(step.get("run", "")))
                if match:
                    found[(workflow.name, job_id)] = match.group(1)
    return found


class TestWhoCallsTheAction:
    def test_the_jobs_that_write_a_junit_report_are_found(self) -> None:
        assert _junit_jobs().keys() >= {("harness-test.yml", "unit"), ("harness-test.yml", "browser-integration")}

    def test_every_job_that_writes_a_junit_report_reports_its_failures_after_its_tests(self) -> None:
        for (workflow, job_id), path in _junit_jobs().items():
            steps = _steps(workflow_jobs(workflow)[job_id])
            test_step = next(i for i, s in enumerate(steps) if f"--junitxml={path}" in str(s.get("run", "")))
            reports = [s for s in steps[test_step + 1 :] if s.get("uses") == _USES]

            assert len(reports) == 1, (workflow, job_id)
            assert reports[0].get("if") == "failure()", (workflow, job_id)
            assert cast("YamlMapping", reports[0]["with"]) == {"junit": path}, (workflow, job_id)

    def test_the_server_job_reads_the_report_its_runner_writes(self) -> None:
        steps = _steps(workflow_jobs("server-unit-tests.yml")["default-pytest"])
        runner = next(i for i, s in enumerate(steps) if "run_default_tests.sh" in str(s.get("run", "")))
        report = steps[runner + 1]
        runner_script = REPO_ROOT / "myrm-agent-server/scripts/ci/run_default_tests.sh"
        written = _JUNIT_FLAG.search(runner_script.read_text(encoding="utf-8"))

        assert report.get("uses") == _USES
        assert report.get("if") == "failure()"
        assert written is not None
        assert cast("YamlMapping", report["with"]) == {"junit": written.group(1)}

    def test_the_action_runs_the_script_on_its_input(self) -> None:
        action = cast("YamlMapping", yaml.safe_load((_ACTION_DIR / "action.yml").read_text(encoding="utf-8")))
        runs = cast("YamlMapping", action["runs"])
        (step,) = cast("list[YamlMapping]", runs["steps"])

        assert runs["using"] == "composite"
        assert cast("YamlMapping", cast("YamlMapping", action["inputs"])["junit"])["required"] is True
        assert step["shell"] == "bash"
        assert cast("YamlMapping", step["env"]) == {"JUNIT": "${{ inputs.junit }}"}
        assert step["run"] == 'python3 "$GITHUB_ACTION_PATH/report_failed_tests.py" "$JUNIT"'
