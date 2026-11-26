"""A secret in a line a server launcher's own loggers write is masked, whichever launcher runs.

[INPUT]
- app/config/logging.py (POS: installs the redacting root handler that the application's and uvicorn's lines go through)
- app/startup/granian_log_config.json (POS: the redacting formatters granian's own loggers write through)
- app/startup/granian_runner.py (POS: granian starter, the optional multi-process mode)
- tests/support/fake_secrets.py (POS: provider-shaped fake credentials assembled at call time)

[OUTPUT]
- Outcome coverage: a token inside an error line of uvicorn (as the runner configures it) and of granian (as its config
  file configures it) never reaches the output, and the granian starter hands granian that config file

[POS]
Startup tests. ``test_uvicorn_log_routing.py`` pins the ``log_config=None`` keyword of every uvicorn launch from the
source; these tests run what the keyword and the config file are for. Both launchers give their loggers private
handlers by default, and a line written through one (the traceback of an unhandled exception) skips the redacting root
handler ``configure_logging`` installs, so a test that only reads the arguments would pass over a launcher that
changed what they do.
"""

from __future__ import annotations

import io
import json
import logging
import logging.config
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from app.config.logging import configure_logging
from app.startup import granian_runner
from tests.support.fake_secrets import openai_style_key

_GRANIAN_LOG_CONFIG = Path(__file__).resolve().parents[2] / "app" / "startup" / "granian_log_config.json"
_SECRET = openai_style_key("proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zA7bC0dE3fG6hJ9kL")
_ERROR_LINE = "Application callable raised an exception: Authorization: Bearer %s"


def _every_logger() -> list[logging.Logger]:
    named = [item for item in logging.Logger.manager.loggerDict.values() if isinstance(item, logging.Logger)]
    return [logging.getLogger(), *named]


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """``configure_logging``, uvicorn's ``Config`` and granian's ``dictConfig`` rewrite process-wide logger state."""
    saved = [(logger, logger.handlers[:], logger.level, logger.propagate, logger.disabled) for logger in _every_logger()]
    yield
    known = {id(entry[0]) for entry in saved}
    for logger in _every_logger():
        if id(logger) not in known:
            logger.handlers.clear()
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
            logger.disabled = False
    for logger, handlers, level, propagate, disabled in saved:
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
        logger.disabled = disabled


def test_a_secret_in_a_uvicorn_error_line_is_masked_under_the_runners_logging_setup(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, isolated_logging: None
) -> None:
    monkeypatch.delenv("MYRM_LOG_FORMAT", raising=False)
    configure_logging()  # what ``app.main`` does on import, before the runner starts uvicorn
    uvicorn.Config("app.main:app", log_level="warning", access_log=False, log_config=None)  # the runner's launch shape

    logging.getLogger("uvicorn.error").error(_ERROR_LINE, _SECRET)

    written = capsys.readouterr()
    assert _SECRET not in written.out + written.err
    assert "Application callable raised an exception" in written.out


def test_the_granian_config_masks_a_secret_through_both_of_its_formatters(isolated_logging: None) -> None:
    config = json.loads(_GRANIAN_LOG_CONFIG.read_text(encoding="utf-8"))
    formatters = list(config["formatters"])
    stream = io.StringIO()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            **config,
            "handlers": {name: {"class": "logging.StreamHandler", "formatter": name, "stream": stream} for name in formatters},
            "loggers": {f"probe.{name}": {"handlers": [name], "level": "INFO", "propagate": False} for name in formatters},
        }
    )

    for name in formatters:
        logging.getLogger(f"probe.{name}").error(_ERROR_LINE, _SECRET)

    assert sorted(formatters) == ["access", "generic"]
    assert stream.getvalue().count("Application callable raised an exception") == len(formatters)
    assert _SECRET not in stream.getvalue()


def test_granian_applies_the_config_to_its_own_loggers(isolated_logging: None) -> None:
    granian_log = pytest.importorskip("granian.log")
    granian_log.configure_logging(granian_log.LogLevels.info, json.loads(_GRANIAN_LOG_CONFIG.read_text(encoding="utf-8")))
    stream = io.StringIO()

    for name in ("_granian", "granian.access"):
        logger = logging.getLogger(name)
        (handler,) = logger.handlers
        assert isinstance(handler, logging.StreamHandler)
        handler.setStream(stream)
        logger.error(_ERROR_LINE, _SECRET)

    assert stream.getvalue().count("Application callable raised an exception") == 2
    assert _SECRET not in stream.getvalue()


def test_the_granian_starter_passes_granian_the_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(granian_runner, "run_startup_health_check", lambda **_: None)
    monkeypatch.setattr(subprocess, "run", lambda command, **_: commands.append(command))
    # The starter exports both worker counts; setting them here lets monkeypatch put the environment back.
    monkeypatch.setenv("GRANIAN_WORKERS", "1")
    monkeypatch.setenv("WEB_CONCURRENCY", "1")

    granian_runner.start_with_granian("127.0.0.1", 25808)

    (command,) = commands
    assert Path(command[command.index("--log-config") + 1]) == _GRANIAN_LOG_CONFIG
    assert _GRANIAN_LOG_CONFIG.is_file()
