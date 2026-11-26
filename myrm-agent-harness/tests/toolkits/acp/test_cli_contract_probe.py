"""The judge of the real-CLI contract: it reads the three help layouts and goes red for a wrong flag or value.

The live contract tests hold the runtime's command lines to what the installed CLIs print. They are only worth their CI
minutes if the judge cannot be fooled, so these tests (which need no CLI) pin it on excerpts of real ``--help`` output
and on deliberately wrong command lines: a flag the CLI renamed, a value it no longer lists, a subcommand it moved.
"""

from __future__ import annotations

import stat
import sys
import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

from tests.toolkits.acp.cli_contract import CliOption, Scopes, check_args, parse_options, parse_probe

# Excerpts of ``claude --help`` (commander: a wrapped description closes with the choices), ``codex exec --help``
# (clap: the description sits on the lines below the option) and ``gemini --help`` (yargs: one long line per option).
COMMANDER_HELP = """\
Options:
  --add-dir <directories...>            More directories Claude can access
  -p, --print                           Print response and exit (useful for pipes).
  --output-format <format>              Output format (only works with --print):
                                        "text" (default), "json" (single
                                        result), or "stream-json" (realtime
                                        streaming) (choices: "text", "json",
                                        "stream-json")
  --permission-mode <mode>              Permission mode to use for the session
                                        (choices: "acceptEdits", "auto",
                                        "bypassPermissions", "manual",
                                        "dontAsk", "plan")
  -r, --resume [value]                  Resume a conversation by session ID or
                                        open interactive picker
  --verbose                             Override verbose mode setting
"""

CLAP_EXEC_HELP = """\
Options:
  -i, --image <FILE>...
          Optional image(s) to attach to the initial prompt

  -s, --sandbox <SANDBOX_MODE>
          Select the sandbox policy to use when executing model-generated shell commands

          [possible values: read-only, workspace-write, danger-full-access]

      --dangerously-bypass-approvals-and-sandbox
          Skip all confirmation prompts and execute commands without sandboxing.

      --skip-git-repo-check
          Allow running Codex outside a Git repository

      --json
          Print events to stdout as JSONL
"""

CLAP_TOP_HELP = """\
Options:
  -s, --sandbox <SANDBOX_MODE>
          Select the sandbox policy

          [possible values: read-only, workspace-write, danger-full-access]
"""

CLAP_RESUME_HELP = """\
Options:
      --skip-git-repo-check
          Allow running Codex outside a Git repository

      --json
          Print events to stdout as JSONL
"""

YARGS_HELP = """\
Options:
  -p, --prompt                    Run in non-interactive (headless) mode with the given prompt.  [string]
      --approval-mode             Set the approval mode: default (prompt for approval), auto_edit (auto-approve edit tools), yolo (auto-approve all tools), plan (read-only mode)  [string] [choices: "default", "auto_edit", "yolo", "plan"]
      --acp                       Starts the agent in ACP mode  [boolean]
  -o, --output-format             The format of the CLI output.  [string] [choices: "text", "json", "stream-json"]
"""


def _option(options: dict[str, CliOption], name: str) -> CliOption:
    option = options.get(name)
    assert option is not None, f"{name} not parsed"
    return option


class TestParseOptions:
    def test_commander_names_values_and_the_choices_that_close_a_wrapped_description(self) -> None:
        options = parse_options(COMMANDER_HELP)

        mode = _option(options, "--permission-mode")
        assert mode.takes_value
        assert mode.choices == frozenset({"acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan"})
        assert _option(options, "--output-format").choices == frozenset({"text", "json", "stream-json"})
        assert _option(options, "-p") is _option(options, "--print")
        assert not _option(options, "--print").takes_value
        assert _option(options, "--resume").takes_value and _option(options, "-r").names == ("-r", "--resume")
        assert _option(options, "--add-dir").choices is None
        assert not _option(options, "--verbose").takes_value

    def test_a_continuation_line_that_mentions_a_flag_does_not_open_an_option(self) -> None:
        text = COMMANDER_HELP + "                                        --resume <id> to pick the session)\n"

        options = parse_options(text)

        assert "--resume" in options and len({id(option) for option in options.values()}) == 6

    def test_clap_reads_the_possible_values_and_tells_a_flag_from_an_option_that_takes_a_value(self) -> None:
        options = parse_options(CLAP_EXEC_HELP)

        assert _option(options, "--sandbox").choices == frozenset(
            {"read-only", "workspace-write", "danger-full-access"}
        )
        assert _option(options, "-s").takes_value
        assert _option(options, "--image").takes_value and _option(options, "-i").names == ("-i", "--image")
        assert not _option(options, "--json").takes_value
        assert not _option(options, "--dangerously-bypass-approvals-and-sandbox").takes_value

    def test_yargs_reads_one_line_entries_with_their_type_tags(self) -> None:
        options = parse_options(YARGS_HELP)

        assert _option(options, "--approval-mode").choices == frozenset({"default", "auto_edit", "yolo", "plan"})
        assert _option(options, "--prompt").takes_value and _option(options, "-p").names == ("-p", "--prompt")
        assert not _option(options, "--acp").takes_value
        assert _option(options, "--output-format").choices == frozenset({"text", "json", "stream-json"})


CLAUDE: Scopes = {(): parse_options(COMMANDER_HELP)}
CODEX: Scopes = {
    (): parse_options(CLAP_TOP_HELP),
    ("exec",): parse_options(CLAP_EXEC_HELP),
    ("exec", "resume"): parse_options(CLAP_RESUME_HELP),
}
CODEX_SUBCOMMANDS = frozenset({"exec", "resume"})
SESSION_ID = "11111111-2222-4333-8444-555555555555"


class TestCheckArgs:
    def test_takes_the_command_lines_the_runtime_builds(self) -> None:
        claude = [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "plan",
            "--resume",
            SESSION_ID,
        ]
        codex = ["--sandbox", "read-only", "exec", "resume", "--json", "--skip-git-repo-check", SESSION_ID]

        assert check_args(CLAUDE, claude, positionals=frozenset({SESSION_ID})) == []
        assert check_args(CODEX, codex, subcommands=CODEX_SUBCOMMANDS, positionals=frozenset({SESSION_ID})) == []

    def test_a_renamed_flag_is_refused_in_every_layout(self) -> None:
        assert check_args(CLAUDE, ["--permission-modes", "plan"]) == [
            "--permission-modes is not an option of the top level"
        ]
        assert check_args(CODEX, ["exec", "--skip-git-check"], subcommands=CODEX_SUBCOMMANDS) == [
            "--skip-git-check is not an option of exec"
        ]
        yargs: Scopes = {(): parse_options(YARGS_HELP)}
        assert check_args(yargs, ["--approvals-mode", "plan"]) == ["--approvals-mode is not an option of the top level"]

    def test_a_value_the_cli_no_longer_lists_is_refused(self) -> None:
        problems = check_args(CLAUDE, ["--permission-mode", "default"])

        assert len(problems) == 1 and problems[0].startswith("--permission-mode default is not among ")
        assert check_args(CODEX, ["--sandbox", "workspace-writes"], subcommands=CODEX_SUBCOMMANDS)
        assert check_args(CODEX, ["--sandbox=workspace-writes"], subcommands=CODEX_SUBCOMMANDS)

    def test_a_listed_value_is_taken_in_the_spaced_and_the_equals_form(self) -> None:
        assert check_args(CODEX, ["--sandbox=workspace-write"], subcommands=CODEX_SUBCOMMANDS) == []
        assert check_args(CLAUDE, ["--permission-mode", "plan"]) == []

    def test_an_unlisted_value_the_probe_vouches_for_is_taken_and_one_it_does_not_vouch_for_is_refused(self) -> None:
        asked: list[tuple[tuple[str, ...], str, str]] = []

        def probe(scope: Sequence[str], option: str, value: str) -> bool:
            asked.append((tuple(scope), option, value))
            return value == "default"

        assert check_args(CLAUDE, ["--permission-mode", "default"], probe=probe) == []
        assert check_args(CLAUDE, ["--permission-mode", "bogus"], probe=probe)
        assert asked == [((), "--permission-mode", "default"), ((), "--permission-mode", "bogus")]

    def test_an_option_is_judged_in_the_scope_it_stands_in(self) -> None:
        # `--image` belongs to `exec`, not to the top level: before the subcommand it is a renamed or moved flag.
        assert check_args(CODEX, ["--image=a.png", "exec"], subcommands=CODEX_SUBCOMMANDS) == [
            "--image is not an option of the top level"
        ]
        assert check_args(CODEX, ["exec", "--image=a.png"], subcommands=CODEX_SUBCOMMANDS) == []

    def test_a_subcommand_the_cli_dropped_and_a_stray_word_are_refused(self) -> None:
        without_resume = {(): CODEX[()], ("exec",): CODEX[("exec",)]}

        assert check_args(without_resume, ["exec", "resume", "--json"], subcommands=CODEX_SUBCOMMANDS)
        assert check_args(CLAUDE, ["--verbose", "stray"]) == [
            "'stray' is neither an option, a known subcommand nor an expected argument"
        ]

    def test_a_boolean_flag_does_not_swallow_the_word_after_it(self) -> None:
        assert check_args(CODEX, ["exec", "--json", "stray"], subcommands=CODEX_SUBCOMMANDS) == [
            "'stray' is neither an option, a known subcommand nor an expected argument"
        ]


def _script(path: Path, body: str) -> str:
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.mark.skipif(sys.platform == "win32", reason="the fake CLIs are shebang scripts")
class TestParseProbe:
    VALIDATING = """\
        import sys
        args = sys.argv[1:]
        if "--mode" in args and args[args.index("--mode") + 1] not in ("a", "b"):
            print("error: invalid value", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    """
    LENIENT = """\
        import sys
        sys.exit(0)
    """
    HELP_FIRST = """\
        import sys
        if "--help" in sys.argv:
            print("help")
            sys.exit(0)
        sys.exit(1)
    """

    def test_vouches_for_a_value_a_validating_parser_takes_and_not_for_one_it_rejects(self, tmp_path: Path) -> None:
        probe = parse_probe(_script(tmp_path / "cli", self.VALIDATING), tmp_path)

        assert probe((), "--mode", "a")
        assert not probe((), "--mode", "c")

    def test_does_not_vouch_for_a_value_when_the_parser_takes_garbage_too(self, tmp_path: Path) -> None:
        probe = parse_probe(_script(tmp_path / "cli", self.LENIENT), tmp_path)

        assert not probe((), "--mode", "a")

    def test_does_not_vouch_for_a_parser_that_prints_its_help_before_it_validates(self, tmp_path: Path) -> None:
        probe = parse_probe(_script(tmp_path / "cli", self.HELP_FIRST), tmp_path)

        assert not probe((), "--mode", "a")
