"""Every custom event reaches the run's stream through ``utils/event_utils.py``; nothing else imports LangChain's emitters.

[INPUT]
- src/myrm_agent_harness/**/*.py: every import of ``dispatch_custom_event`` / ``adispatch_custom_event`` from LangChain

[OUTPUT]
- Fails when a module other than ``utils/event_utils.py`` imports LangChain's own custom-event emitters. They reach
  ``astream_events`` callbacks and never the ``custom`` stream mode the stream executor consumes, so the event is lost
  without any error: no test of the emitting code notices, because it only ever sees its own emitter being called. The
  scan matches ``from langchain... import <emitter>`` by name, so an emitter reached as a module attribute
  (``import langchain_core.callbacks.manager as m; m.dispatch_custom_event(...)``) is not seen.

[POS]
tests/architecture/test_custom_event_single_source.py - guard for ``utils/event_utils.py``: its wrappers write through
the run's StreamWriter, the one channel the executor reads.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "myrm_agent_harness"
_WRAPPER_MODULE = "utils/event_utils.py"
_LANGCHAIN_MODULES = ("langchain_core.callbacks", "langchain.callbacks")
_LANGCHAIN_EMITTERS = frozenset({"dispatch_custom_event", "adispatch_custom_event"})


def langchain_emitter_imports(source: str) -> Iterator[tuple[int, str]]:
    """``(line, emitter)`` for each import in *source* that takes a custom-event emitter from LangChain."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(_LANGCHAIN_MODULES):
            for alias in node.names:
                if alias.name in _LANGCHAIN_EMITTERS:
                    yield node.lineno, alias.name


@pytest.mark.architecture
def test_only_the_wrapper_module_imports_langchains_custom_event_emitters() -> None:
    offenders = [
        f"{relative}:{line}: import {emitter}"
        for path in sorted(_SRC.rglob("*.py"))
        if (relative := path.relative_to(_SRC).as_posix()) != _WRAPPER_MODULE
        for line, emitter in langchain_emitter_imports(path.read_text(encoding="utf-8"))
    ]

    assert not offenders, (
        "emit through myrm_agent_harness.utils.event_utils (dispatch_custom_event, or emit_custom_event_sync from a "
        "synchronous tool): LangChain's emitters never reach the custom stream the executor consumes:\n"
        + "\n".join(offenders)
    )


@pytest.mark.architecture
def test_the_wrapper_module_still_imports_the_langchain_fallback() -> None:
    assert list(langchain_emitter_imports((_SRC / _WRAPPER_MODULE).read_text(encoding="utf-8")))


class TestTheScannerSeesWhatItShould:
    def test_the_synchronous_emitter_import_is_found(self) -> None:
        source = "from langchain_core.callbacks.manager import dispatch_custom_event"

        assert list(langchain_emitter_imports(source)) == [(1, "dispatch_custom_event")]

    def test_an_emitter_imported_inside_a_function_is_found(self) -> None:
        source = "def emit() -> None:\n    from langchain_core.callbacks import adispatch_custom_event\n"

        assert list(langchain_emitter_imports(source)) == [(2, "adispatch_custom_event")]

    def test_the_harness_wrapper_and_other_langchain_imports_are_not_findings(self) -> None:
        source = (
            "from myrm_agent_harness.utils.event_utils import dispatch_custom_event\n"
            "from langchain_core.callbacks.manager import CallbackManager\n"
        )

        assert list(langchain_emitter_imports(source)) == []
