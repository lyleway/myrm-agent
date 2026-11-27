"""The Remote Web Fetch Fallback card of ``/settings/search`` on a live Chrome page, and the ``webFetchEscalation`` config it saves.

A user saves the Jina key by typing into the card; the page's sync layer then writes the whole value to the backend.
``CardSession`` types into the card the same way and reads back what the card shows, what the backend stores, and the
requests the page made (through ``config_sync_fault_shim``), so a test can follow one edit from the keyboard to the
database and tell when each step happened.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from cdp_chat.support import fetch_config_value
from e2e_core.orchestrator import touch_wall_progress

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    _warm_ui_parallel_wait_sec,
    dismiss_blocking_modals,
    http_json,
    wait_for_state,
)
from tests.support.config_sync_fault_shim import ConfigLog, ConfigRequest, read_log

CONFIG_KEY = "webFetchEscalation"
SETTINGS_ROUTE = "/settings/search"

# Page-clock silence that proves a write is final: the outbox retries after a 1 s backoff, so a repeat would show by then.
QUIET_MS = 5_000.0

_FIND_CARD_JS = """const card = Array.from(document.querySelectorAll('section')).find((el) =>
    /Remote Web Fetch Fallback|网页抓取远程兜底/i.test(el.textContent || '')
  );
"""

_EXPAND_CARD_JS = (
    "(() => {\n  "
    + _FIND_CARD_JS
    + """  if (!card) return { ready: false, reason: 'card_missing' };
  const header = card.querySelector('button[aria-expanded]');
  if (!header) return { ready: false, reason: 'expand_btn_missing' };
  if (header.getAttribute('aria-expanded') !== 'true') {
    header.click();
    return { ready: false, reason: 'expanding_card' };
  }
  const input = card.querySelector('input[type="password"]');
  return input ? { ready: true, jina: input.value } : { ready: false, reason: 'jina_input_missing' };
})()"""
)

_TYPE_KEY_JS = (
    "const typeKey = (value) => {\n  "
    + _FIND_CARD_JS
    + """  const input = card && card.querySelector('input[type="password"]');
  if (!input) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
};
"""
)

_EDIT_JINA_KEY_JS = "((value) => {\n" + _TYPE_KEY_JS + "  return typeKey(value);\n})"

# Types `older`, then `newer` the moment the page has started the held write of `older`, and arms `thenArm` (a fault
# for the next request) in the same turn. The window between a request starting and the app giving up on it is the
# headers deadline, shorter than a slow reader's round trips, so all of it runs inside the page.
_EDIT_WHILE_HELD_JS = (
    "(async (older, newer, thenArm) => {\n"
    + _TYPE_KEY_JS
    + """  if (!typeKey(older)) return { ok: false, reason: 'jina_input_missing' };
  const giveUpAt = performance.now() + 30000;
  while (performance.now() < giveUpAt) {
    const held = window.__cfgFault.snapshot().entries.some(
      (entry) => entry.fault === 'hold_headers' && entry.markers.includes(older)
    );
    if (held) {
      typeKey(newer);
      if (thenArm) window.__cfgFault.arm(thenArm.kind, thenArm.needle, 0, thenArm.apply, thenArm.method);
      return { ok: true, newerAt: performance.now() };
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  return { ok: false, reason: 'held_request_never_started' };
})"""
)

_READ_JINA_KEY_JS = (
    "(() => {\n  "
    + _FIND_CARD_JS
    + """  const input = card && card.querySelector('input[type="password"]');
  return input ? input.value : null;
})()"""
)

_READ_ENABLED_SWITCH_JS = (
    "(() => {\n  "
    + _FIND_CARD_JS
    + """  const toggle = card && card.querySelector('button[role="switch"]');
  return toggle ? toggle.getAttribute('aria-checked') : null;
})()"""
)


_RUN_TOKEN = uuid.uuid4().hex[:8]


def run_marker(step: str) -> str:
    """A Jina key value unique to this run and `step`, which the fault shim finds in a request body by its prefix.

    The audit log outlives a run on a runtime that is reused, so counting the writes of a value that an earlier run
    also typed would count that run's rows too.
    """
    return f"jina-e2e-{_RUN_TOKEN}{step}"


def jina_key(value: object) -> object:
    """The Jina key of a `webFetchEscalation` value: the backend keeps snake_case, the app sends camelCase."""
    return (value.get("jina_api_key") or value.get("jinaApiKey")) if isinstance(value, dict) else None


@dataclass(frozen=True)
class CardSession:
    """The card on a live page, plus the backend it saves to."""

    client: ChromeMcpClient
    page: McpPage
    api_base: str

    @classmethod
    def open(cls, client: ChromeMcpClient, page: McpPage, api_base: str) -> CardSession:
        """Waits until the card is on the page, expanded, with its Jina key input."""
        dismiss_blocking_modals(client, page)
        state = wait_for_state(client, page, _EXPAND_CARD_JS, timeout_sec=_warm_ui_parallel_wait_sec(120.0))
        assert state.get("ready") is True, state
        return cls(client, page, api_base)

    def edit(self, value: str) -> None:
        typed = self.client.evaluate(self.page, f"({_EDIT_JINA_KEY_JS})({json.dumps(value)})")
        assert typed is True, "the Jina key input is missing on the page"

    def edit_while_held(self, older: str, newer: str, *, hold_settlement_read: bool = False) -> float:
        """Types `older`, then `newer` while the page holds the write of `older`; returns the page time of the second edit.

        With `hold_settlement_read`, the read of the key's server copy, which settles the version conflict the second
        edit meets, is armed to stall in the same page turn as that edit.
        """
        touch_wall_progress(current_node="config_sync_edit_while_held")
        then_arm = (
            {"kind": "hold_headers", "needle": f"/config/{CONFIG_KEY}", "method": "GET", "apply": False}
            if hold_settlement_read
            else None
        )
        script = f"({_EDIT_WHILE_HELD_JS})({json.dumps(older)}, {json.dumps(newer)}, {json.dumps(then_arm)})"
        result = self.client.evaluate(self.page, script, timeout_sec=60.0)
        assert isinstance(result, dict) and result.get("ok") is True, result
        newer_at = result.get("newerAt")
        assert isinstance(newer_at, (int, float)), result
        return float(newer_at)

    def shown(self) -> object:
        return self.client.evaluate(self.page, _READ_JINA_KEY_JS)

    def enabled_switch(self) -> object:
        """The card's header switch as the page shows it: the `aria-checked` string, or None when the card is missing."""
        return self.client.evaluate(self.page, _READ_ENABLED_SWITCH_JS)

    def stored(self) -> object:
        return jina_key(fetch_config_value(CONFIG_KEY, api_url=self.api_base))

    def applied_versions(self, marker: str) -> list[str]:
        """Versions of the writes of `marker` the backend applied: its audit log holds one row per applied write."""
        history = http_json("GET", f"{self.api_base}/api/v1/config/{CONFIG_KEY}/history?limit=100")
        assert isinstance(history, list), history
        return [
            str(entry.get("version"))
            for entry in history
            if isinstance(entry, dict) and jina_key(entry.get("new_value")) == marker
        ]

    def applied_count(self, marker: str) -> int:
        return len(self.applied_versions(marker))

    def applied_at_ms(self, marker: str) -> int:
        """The epoch millisecond the backend applied the one write of `marker`: a version is `<that millisecond>_<counter>`."""
        versions = self.applied_versions(marker)
        assert len(versions) == 1, f"{marker!r} must be applied exactly once, the audit log holds {versions}"
        return int(versions[0].split("_", 1)[0])

    def wait_stored(self, expected: str, *, timeout_sec: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            touch_wall_progress(current_node="config_sync_wait_stored")
            if self.stored() == expected:
                return
            time.sleep(0.5)
        raise AssertionError(f"the backend did not store {expected!r} within {timeout_sec}s; it holds {self.stored()!r}")

    def log(self) -> ConfigLog:
        return read_log(self.client, self.page)

    def writes(self, marker: str) -> list[ConfigRequest]:
        return [request for request in self.log().requests if request.is_config_sync and marker in request.markers]

    def settled_writes(self, marker: str, *, timeout_sec: float = 120.0) -> list[ConfigRequest]:
        """The marker's writes once every one has ended and the page has been quiet for a while, on its own clock."""
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            touch_wall_progress(current_node="config_sync_settle")
            log = self.log()
            writes = [request for request in log.requests if request.is_config_sync and marker in request.markers]
            if writes and all(write.is_settled for write in writes) and log.quiet_for_ms >= QUIET_MS:
                return writes
            time.sleep(0.5)
        raise AssertionError(f"the writes of {marker!r} did not settle within {timeout_sec}s: {self.writes(marker)}")

    def server_reads_after(self, started_ms: float) -> list[ConfigRequest]:
        """The conflict settlement's read of the key's server copy made after `started_ms`."""
        return [
            request
            for request in self.log().requests
            if request.method == "GET" and request.path.endswith(f"/config/{CONFIG_KEY}") and request.started_at_ms >= started_ms
        ]
