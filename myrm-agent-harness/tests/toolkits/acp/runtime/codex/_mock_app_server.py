"""A scripted stand-in for ``codex app-server``: JSON-RPC over stdio, a scenario file saying what each turn does.

Run as ``python _mock_app_server.py`` with ``MOCK_APP_SERVER_SCENARIO`` naming the scenario (a JSON file); everything
it receives, and what it was started with, is appended to ``log.jsonl`` next to it. It stands in for what a real Codex
cannot be made to do on demand: die in the middle of a turn, stay silent, refuse a request, ignore an interrupt, send
the notifications of another turn. What a real Codex does is covered by the ``test_real_codex_*`` tests.

Scenario keys (all optional):
  processes      one scenario for each server process that is started, in order (the last repeats); the keys below
                 then go inside each of them
  handshake      "ok" (default), "silent" (never answers ``initialize``) or "exit" (writes ``stderr`` and exits ``exit_code``)
  login          "ok" (default) or "refuse" for ``account/login/start``
  config         the ``config`` that ``config/read`` answers with
  resume         "ok" (default): ``thread/resume`` loads the thread asked for; "unknown": it refuses
  turn_start_delay  seconds ``turn/start`` takes to be answered
  turns          a list of turns, one per ``turn/start`` (the last one repeats); a turn is a list of steps:
    {"notify": {"method", "params"}}      send a notification
    {"ask": {"method", "params"}}         send a request of the server and log the answer
    {"complete": {"status", "error"}}     send ``turn/completed``
    {"exit": code, "stderr": text}        write ``stderr`` and exit
    {"wait_interrupt": true}              block until ``turn/interrupt`` arrives (it is answered)
    {"hang": true}                        stay alive and ignore everything, an interrupt included
    {"sleep": seconds}
    {"refuse": {"code", "message"}}       (first step only) answer ``turn/start`` with this error instead of a turn
The strings ``$thread`` and ``$turn`` inside a step are replaced by the ids of the running turn.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

SCENARIO_PATH = Path(os.environ["MOCK_APP_SERVER_SCENARIO"])
LOG = SCENARIO_PATH.with_name("log.jsonl")


def pick_scenario() -> dict[str, object]:
    scenario = json.loads(SCENARIO_PATH.read_text())
    processes = scenario.get("processes")
    if not processes:
        return scenario  # type: ignore[no-any-return]
    started = sum('{"start"' in line for line in LOG.read_text().splitlines()) if LOG.exists() else 0
    return processes[min(started, len(processes) - 1)]  # type: ignore[no-any-return]


SCENARIO = pick_scenario()

incoming: queue.Queue[dict[str, object] | None] = queue.Queue()
state = {"threads": 0, "turns": 0, "asked": 1000}


def log(entry: Mapping[str, object]) -> None:
    with LOG.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def send(message: Mapping[str, object]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def read_stdin() -> None:
    for line in sys.stdin:
        incoming.put(json.loads(line))
    incoming.put(None)


def substitute(value: object, ids: Mapping[str, str]) -> object:
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [substitute(item, ids) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, ids) for key, item in value.items()}
    return value


def take(accept: Callable[[dict[str, object]], bool]) -> dict[str, object]:
    """The next message that ``accept`` wants; an interrupt that arrives meanwhile is answered and remembered."""
    while True:
        message = incoming.get()
        if message is None:
            sys.exit(0)
        log({"in": message})
        if message.get("method") == "turn/interrupt":
            send({"id": message["id"], "result": {}})
            state["interrupted"] = True
            if accept(message):
                return message
            continue
        if accept(message):
            return message


def run_turn(steps: list[dict[str, object]], ids: Mapping[str, str]) -> None:
    for step in steps:
        if "notify" in step:
            send(substitute(step["notify"], ids))  # type: ignore[arg-type]
        elif "ask" in step:
            state["asked"] += 1
            ask = substitute(step["ask"], ids)
            assert isinstance(ask, dict)
            send({"id": state["asked"], "method": ask["method"], "params": ask["params"]})
            answer = take(lambda m: m.get("id") == state["asked"] and "method" not in m)
            log({"answer": answer.get("result", answer.get("error"))})
        elif "complete" in step:
            complete = step["complete"]
            assert isinstance(complete, dict)
            turn: dict[str, object] = {"id": ids["$turn"], "status": complete["status"], "items": []}
            if complete.get("error"):
                turn["error"] = complete["error"]
            send({"method": "turn/completed", "params": {"threadId": ids["$thread"], "turn": turn}})
        elif "exit" in step:
            sys.stderr.write(str(step.get("stderr", "")))
            sys.stderr.flush()
            sys.exit(int(step["exit"]))  # type: ignore[call-overload]
        elif "wait_interrupt" in step:
            if not state.get("interrupted"):
                take(lambda m: m.get("method") == "turn/interrupt")
            state["interrupted"] = False
        elif "hang" in step:
            while True:
                time.sleep(3600)
        elif "sleep" in step:
            time.sleep(float(step["sleep"]))  # type: ignore[arg-type]


def handle(message: dict[str, object]) -> None:
    method, params = message.get("method"), message.get("params")
    assert isinstance(params, dict) or params is None
    params = params or {}
    reply: Callable[[object], None] = lambda result: send({"id": message["id"], "result": result})  # noqa: E731
    match method:
        case "initialize":
            if SCENARIO.get("handshake") == "exit":
                sys.stderr.write(str(SCENARIO.get("stderr", "")))
                sys.stderr.flush()
                sys.exit(cast("int", SCENARIO.get("exit_code", 1)))
            if SCENARIO.get("handshake") != "silent":
                reply({"userAgent": "mock", "codexHome": "/tmp", "platformFamily": "unix", "platformOs": "linux"})
        case "account/login/start":
            if SCENARIO.get("login") == "refuse":
                send({"id": message["id"], "error": {"code": -32600, "message": "The key is not valid"}})
            else:
                reply({"type": "apiKey"})
        case "config/read":
            reply({"config": SCENARIO.get("config", {}), "origins": {}})
        case "thread/start":
            state["threads"] += 1
            reply({"thread": {"id": f"thread-{state['threads']}"}})
        case "thread/resume":
            if SCENARIO.get("resume") == "unknown":
                send({"id": message["id"], "error": {"code": -32600, "message": "no rollout found for thread id"}})
            else:
                reply({"thread": {"id": params["threadId"]}})
        case "turn/start":
            turns = cast("list[list[dict[str, object]]]", SCENARIO.get("turns", [[]]))
            steps = turns[min(state["turns"], len(turns) - 1)]
            state["turns"] += 1
            if steps and "refuse" in steps[0]:
                send({"id": message["id"], "error": steps[0]["refuse"]})
                return
            ids = {"$thread": str(params["threadId"]), "$turn": f"turn-{state['turns']}"}
            time.sleep(float(SCENARIO.get("turn_start_delay", 0)))  # type: ignore[arg-type]
            reply({"turn": {"id": ids["$turn"], "status": "inProgress", "items": []}})
            run_turn(steps, ids)
        case "turn/interrupt":
            reply({})
        case _ if "id" in message:
            send({"id": message["id"], "error": {"code": -32601, "message": f"unknown method {method}"}})


def main() -> None:
    log({"start": {"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ), "pid": os.getpid()}})
    threading.Thread(target=read_stdin, daemon=True).start()
    while True:
        message = incoming.get()
        if message is None:
            return
        log({"in": message})
        handle(message)


if __name__ == "__main__":
    main()
