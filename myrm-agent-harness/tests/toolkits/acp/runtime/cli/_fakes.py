"""Test doubles shared by the CLI runtime tests: a scripted stand-in for the CLIs."""

from __future__ import annotations

import json
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    CliSessionStore,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)

# A POSIX script standing in for the CLI agents. It behaves as the real binary does (verified against claude 2.1.x):
# a new conversation announces its id first (``system/init``) and is only found again from the directory that created
# it; resuming an unknown id fails fast without announcing anything; one failing turn can end in a max-turns /
# API-error ``result`` line. Which command it plays is decided by its own file name (``claude``, or any other name
# for a CLI that prints plain events); scenario.json next to it picks what a turn does (one dict for every call, or a
# list by call index).
# ``stall_before_stdin`` makes it start reading its stdin only after that many seconds, like a CLI still running its start hooks.
# ``asks`` (Claude only) is a list of ``{"tool_name", "input"}``: given ``--permission-prompt-tool``, the stand-in puts each
# to the MCP tool named by ``--mcp-config`` over HTTP, as Claude Code does, and reports the answers in its text.
# Like Claude Code, it takes any number of ``--mcp-config`` files and logs what each held while the turn ran.
_SCRIPT = """\
#!__PYTHON__
import json, os, signal, sys, time, uuid

here = os.path.dirname(os.path.abspath(__file__))
name = os.path.basename(sys.argv[0])
argv = sys.argv[1:]
cwd = os.getcwd()


def read_json(path, default):
    return json.load(open(path)) if os.path.exists(path) else default


mcp_config_paths = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--mcp-config"]
calls_path = os.path.join(here, "calls.jsonl")
index = len(open(calls_path).readlines()) if os.path.exists(calls_path) else 0
scenario = read_json(os.path.join(here, "scenario.json"), {})
if isinstance(scenario, list):
    scenario = scenario[min(index, len(scenario) - 1)]

time.sleep(scenario.get("stall_before_stdin", 0))
prompt = sys.stdin.read() if "-p" in argv else (argv[-1] if argv else "")
with open(calls_path, "a") as handle:
    mcp_configs = [read_json(path, None) for path in mcp_config_paths]
    handle.write(json.dumps({"name": name, "argv": argv, "cwd": cwd, "prompt": prompt, "env": dict(os.environ),
                             "mcp_configs": mcp_configs}) + "\\n")

if scenario.get("ignore_sigterm"):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if scenario.get("exit_on_sigterm"):
    # The real claude traps SIGTERM and exits with 143, a positive code, rather than dying by the signal.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(scenario["exit_on_sigterm"]))


def emit(obj):
    print(obj if isinstance(obj, str) else json.dumps(obj), flush=True)


def finish(code=0):
    if scenario.get("stderr"):
        sys.stderr.write(scenario["stderr"] + "\\n")
    sys.exit(scenario.get("exit", code))


def hang_if(key):
    if scenario.get(key):
        time.sleep(120)


def ask_host(tool_name, tool_input):
    # Claude Code's permission prompt: initialize the MCP endpoint of --mcp-config, then call its tool.
    import urllib.request

    server = next(c["mcpServers"]["myrm_permissions"] for c in mcp_configs if "myrm_permissions" in c["mcpServers"])
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **server["headers"]}

    def post(request_id, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(server["url"], data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())

    client = {"name": "claude-code", "version": "2"}
    post(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": client})
    answer = post(2, "tools/call", {"name": "approve", "arguments": {"tool_name": tool_name, "input": tool_input}})
    return json.loads(answer["result"]["content"][0]["text"])


disk = os.path.join(here, "conversations.json")
known = read_json(disk, [])


def register(conversation):
    known.append(conversation)
    json.dump(known, open(disk, "w"))


hang_if("hang_before_output")
if scenario.get("fail_before_output"):
    finish(1)
if scenario.get("big_line"):
    emit({"type": "assistant", "content": "x" * scenario["big_line"]})

if name == "claude":
    resume = argv[argv.index("--resume") + 1] if "--resume" in argv else None
    if resume is not None and {"id": resume, "cwd": cwd} not in known:
        message = "No conversation found with session ID: " + resume
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": resume,
              "terminal_reason": "aborted_streaming", "errors": [message]})
        sys.stderr.write(message + "\\n")
        sys.exit(1)
    session_id = resume or str(uuid.uuid4())
    if resume is None:
        register({"id": session_id, "cwd": cwd})
    emit({"type": "system", "subtype": "init", "session_id": session_id, "cwd": cwd})
    hang_if("hang_after_init")
    time.sleep(scenario.get("silent_seconds", 0))
    answers = []
    if "--permission-prompt-tool" in argv and scenario.get("asks"):
        for ask in scenario["asks"]:
            answers.append(ask_host(ask["tool_name"], ask["input"]))
        json.dump(answers, open(os.path.join(here, "answers.json"), "w"))
    for number, status in enumerate(scenario.get("retry_statuses", [])):
        # Claude reports every retry of a failed request with the status the API answered; the turn can go on after.
        error = "authentication_failed" if status == 401 else "server_error"
        emit({"type": "system", "subtype": "api_retry", "attempt": number + 1, "max_retries": 10,
              "retry_delay_ms": 500.0, "error_status": status, "error": error, "session_id": session_id})
    hang_if("hang_after_retries")
    text = scenario.get("text", session_id)
    if answers:
        text = "; ".join(answer["behavior"] for answer in answers)
    for number in range(scenario.get("tools", 0)):
        call = {"type": "tool_use", "id": "toolu_%d" % number, "name": "Bash", "input": {"command": "echo %d" % number}}
        emit({"type": "assistant", "message": {"id": "msg_t%d" % number, "content": [call]}})
        result = {"type": "tool_result", "tool_use_id": "toolu_%d" % number, "content": "out %d" % number}
        emit({"type": "user", "message": {"content": [result]}})
    if "--include-partial-messages" in argv and scenario.get("partial"):
        start = {"type": "message_start", "message": {"id": "msg_1"}}
        emit({"type": "stream_event", "api_message_id": "msg_1", "event": start})
        for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
            delta = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}
            emit({"type": "stream_event", "api_message_id": "msg_1", "event": delta})
    if text:
        emit({"type": "assistant", "message": {"id": "msg_1", "content": [{"type": "text", "text": text}]}})
    fail = scenario.get("fail")
    if fail == "max_turns":
        emit({"type": "result", "subtype": "error_max_turns", "is_error": True, "terminal_reason": "max_turns",
              "errors": ["Reached maximum number of turns (3)"], "session_id": session_id})
        finish(1)
    if fail == "api_error":
        notice = {"id": "msg_err", "model": "<synthetic>", "content": [{"type": "text", "text": "API Error: 400 bad"}]}
        emit({"type": "assistant", "error": "invalid_request", "message": notice})
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "API Error: 400 bad",
              "api_error_status": 400, "terminal_reason": "api_error", "session_id": session_id})
        finish(1)
    if fail == "not_logged_in":
        # Nobody is logged in: no API call is made, so the result has no status, only the error category.
        text = "Not logged in \\u00b7 Please run /login"
        notice = {"id": "msg_err", "model": "<synthetic>", "content": [{"type": "text", "text": text}]}
        emit({"type": "assistant", "error": "authentication_failed", "message": notice})
        emit({"type": "result", "subtype": "success", "is_error": True, "result": text,
              "api_error_status": None, "session_id": session_id})
        finish(1)
    hang_if("hang_after_text")
    if fail == "crash":
        finish(3)
    usage = {"input_tokens": 10, "output_tokens": 5}
    emit({"type": "result", "subtype": "success", "is_error": False, "result": text, "usage": usage,
          "terminal_reason": "completed", "session_id": session_id})
    finish(0)

else:
    hang_if("hang_after_init")
    for number in range(scenario.get("error_notices", 0)):
        # A CLI can report every retry of a request as an error line; the turn can still succeed afterwards.
        emit({"type": "error", "message": "Request failed, retrying (%d/5)" % (number + 1)})
    if scenario.get("fail") == "error_line_only":
        emit({"type": "error", "message": "model not supported"})
        finish(1)
    emit({"type": "assistant", "content": scenario.get("text", "ok")})
    finish(0)
"""


@dataclass
class FakeCli:
    """The scripted CLI stand-ins of one test, plus the log of how they were invoked."""

    bin_dir: Path

    @property
    def workspace(self) -> Path:
        """The directory a test starts its CLI turns in unless it names another."""
        return self.bin_dir.parent

    def command(self, name: str = "claude") -> Path:
        """The executable that plays ``name``; its file name decides which CLI it imitates."""
        path = self.bin_dir / name
        if not path.exists():
            path.write_text(_SCRIPT.replace("__PYTHON__", sys.executable))
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def set_scenario(self, scenario: dict[str, object] | list[dict[str, object]]) -> None:
        (self.bin_dir / "scenario.json").write_text(json.dumps(scenario))

    def calls(self, name: str | None = None) -> list[dict[str, object]]:
        path = self.bin_dir / "calls.jsonl"
        if not path.exists():
            return []
        calls = [json.loads(line) for line in path.read_text().splitlines()]
        return [call for call in calls if name is None or call["name"] == name]

    def mcp_configs(self, name: str = "claude") -> list[dict[str, dict[str, dict[str, object]]]]:
        """What each ``--mcp-config`` file of the first call held while its turn ran, in command-line order."""
        configs = self.calls(name)[0]["mcp_configs"]
        assert isinstance(configs, list)
        return configs

    def resume_args(self, name: str = "claude") -> list[str | None]:
        """The CLI session id each call asked to resume (None for a fresh session)."""
        resumed: list[str | None] = []
        for call in self.calls(name):
            argv = call["argv"]
            assert isinstance(argv, list)
            resumed.append(argv[argv.index("--resume") + 1] if "--resume" in argv else None)
        return resumed


def make_runtime(
    fake_cli: FakeCli,
    name: str = "claude",
    *,
    store: CliSessionStore | None = None,
    workspace: str | None = None,
    **overrides: object,
) -> CliRuntime:
    """A ``CliRuntime`` for the stand-in that plays ``name``; ``overrides`` replace ``RuntimeConfig`` fields.

    The runtime starts the CLI in ``workspace``, by default ``fake_cli.workspace``.
    """
    settings: dict[str, object] = {
        "backend_type": "cli",
        "command": str(fake_cli.command(name)),
        "args": ["-p", "--output-format", "stream-json"] if name == "claude" else [],
        "permission_mode": "safe",
        "timeout_seconds": 20,
    }
    settings.update(overrides)
    return CliRuntime(
        name,
        RuntimeConfig(**settings),  # type: ignore[arg-type]
        workspace=workspace or str(fake_cli.workspace),
        session_store=store,
    )


async def run_turn(runtime: CliRuntime, prompt: str = "hello", session_id: str = "s1") -> list[RuntimeEvent]:
    return [event async for event in runtime.run_turn(prompt, session_id)]


def types_of(events: list[RuntimeEvent]) -> list[RuntimeEventType]:
    return [event.type for event in events]


def text_of(events: list[RuntimeEvent]) -> str:
    return "".join(str(e.data["content"]) for e in events if e.type == RuntimeEventType.TEXT_DELTA)


def errors_of(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    return [event for event in events if event.type == RuntimeEventType.ERROR]


def error_codes_of(events: list[RuntimeEvent]) -> list[AcpErrorCode]:
    codes: list[AcpErrorCode] = []
    for event in errors_of(events):
        error = event.data["error"]
        assert isinstance(error, AcpError)
        codes.append(error.code)
    return codes


def statuses_of(events: list[RuntimeEvent]) -> list[str]:
    return [str(e.data["status"]) for e in events if e.type == RuntimeEventType.STATUS_UPDATE]
