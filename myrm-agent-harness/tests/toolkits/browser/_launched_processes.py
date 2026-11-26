"""The Playwright drivers and browser main processes the running test process started, read from the process table.

[INPUT]
- myrm_agent_harness.toolkits.browser.process_owner::OWNER_ENV_VAR / owner_stamp_env (POS: ownership stamp)
- psutil: the real process table

[OUTPUT]
- driver_processes: the Playwright drivers this process started
- browser_main_processes: the browser main processes this process launched

[POS]
Real-browser tests that prove what stays running (or does not come back) after a shutdown or a driver death count
processes instead of trusting the pool's own bookkeeping. Only processes below this test process, or stamped with its
owner stamp, are returned: a browser of the user's is never seen.
"""

from __future__ import annotations

import contextlib

import psutil

from myrm_agent_harness.toolkits.browser.process_owner import OWNER_ENV_VAR, owner_stamp_env

_BROWSER_MAIN_PROCESS_FLAG = "--remote-debugging-pipe"


def driver_processes() -> dict[int, psutil.Process]:
    """The Playwright drivers this process started (the bundled `driver/node` executable)."""
    drivers: dict[int, psutil.Process] = {}
    for child in psutil.Process().children(recursive=True):
        with contextlib.suppress(psutil.Error):  # a Chromium helper may exit while the list is walked
            argv = child.cmdline()
            if argv and "/driver/" in argv[0].replace("\\", "/"):
                drivers[child.pid] = child
    return drivers


def browser_main_processes() -> dict[int, psutil.Process]:
    """The browser main processes this process launched: Playwright gives them the pipe flag, the launcher the stamp.

    A helper (`--type=...`) carries the flag too and is not a browser. Found through the stamp, not through the
    driver: a Linux container may start the browser beside the driver.
    """
    stamp = owner_stamp_env()[OWNER_ENV_VAR]
    browsers: dict[int, psutil.Process] = {}
    for proc in psutil.process_iter():
        # A process of another user, or one that just exited, cannot be read and is none of ours.
        with contextlib.suppress(psutil.Error, OSError, SystemError):
            argv = proc.cmdline()
            is_main = _BROWSER_MAIN_PROCESS_FLAG in argv and not any(arg.startswith("--type=") for arg in argv)
            if is_main and proc.environ().get(OWNER_ENV_VAR) == stamp:
                browsers[proc.pid] = proc
    return browsers
