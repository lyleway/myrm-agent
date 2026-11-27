"""One scenario of the config-sync deadline E2E: the card on a fresh page wired to the fault shim, and the phases run on it.

A scenario owns one page, so a failure points at one flow and a slow host stretches one scenario, not all of them. The
stored ``webFetchEscalation`` value is reset before the page opens, because the page reads it when it loads, and put back
afterwards.

[INPUT]
- cdp_chat.support::fetch_config_value, get_e2e_api_url, put_config_value, wait_e2e_provider_ready (POS: Stable import
  surface of the E2E chat helpers; it defines nothing itself)
- tests.support.chrome_mcp_e2e::open_settings_subroute, warm_ui_route (POS: Shared real-Chrome MCP helpers for formal UI
  E2E tests)
- tests.e2e.config_sync_deadline.card_session::CONFIG_KEY, SETTINGS_ROUTE, CardSession (POS: Card layer of the config-sync
  deadline E2E: follows one edit from the keyboard to the database)
- tests.e2e.config_sync_deadline.fault_shim::install_fault_shim (POS: Fault injection layer of the config-sync deadline
  E2E; the only code that touches the page's fetch)

[OUTPUT]
- DEFAULT_CONFIG: the stored value every scenario starts from (switched off, no key)
- deadline_card(config): the card of a fresh page with the shim installed; `config` is the value stored before the page loads
- run_phases(card, phases): runs the phases in order, prints their evidence, and fails once with every failing phase

[POS]
Scenario layer of the config-sync deadline E2E: arranges one page and reports what its phases found.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

import pytest
from cdp_chat.support import fetch_config_value, get_e2e_api_url, put_config_value, wait_e2e_provider_ready

from tests.e2e.config_sync_deadline.card_session import CONFIG_KEY, SETTINGS_ROUTE, CardSession
from tests.e2e.config_sync_deadline.fault_shim import install_fault_shim
from tests.support.chrome_mcp_e2e import open_settings_subroute, warm_ui_route

DEFAULT_CONFIG: dict[str, object] = {
    "enabled": False,
    "jinaApiKey": None,
    "firecrawl": {"inheritFromSearch": True, "api_key": None, "apiBase": None, "networkIsolationConfirmed": False},
    "sessionCap": 5,
}


@contextmanager
def deadline_card(config: Mapping[str, object] | None = None) -> Iterator[CardSession]:
    """The card of a fresh page, with the key's stored value set to `config` (the defaults) first and put back afterwards."""
    api_base = get_e2e_api_url()
    if not wait_e2e_provider_ready(api_url=api_base):
        pytest.fail("E2E backend not ready for the config-sync deadline signoff")

    backup = fetch_config_value(CONFIG_KEY, api_url=api_base)
    try:
        put_config_value(CONFIG_KEY, dict(DEFAULT_CONFIG if config is None else config), api_url=api_base)
        warm_ui_route(SETTINGS_ROUTE)
        with open_settings_subroute(SETTINGS_ROUTE, timeout_ms=90_000) as (client, page):
            card = CardSession.open(client, page, api_base)
            install_fault_shim(client, page)
            yield card
    finally:
        if backup:
            put_config_value(CONFIG_KEY, backup, api_url=api_base)


def run_phases(card: CardSession, phases: Mapping[str, Callable[[], dict[str, object]]]) -> dict[str, dict[str, object]]:
    """Runs each phase and adds how long it took; one failing phase does not hide the others.

    A failure carries every config request the page made, which is what tells a cancel from a hang.
    """
    evidence: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for name, phase in phases.items():
        started = time.monotonic()
        try:
            evidence[name] = {**phase(), "took_s": round(time.monotonic() - started)}
        except AssertionError as error:
            requests = "\n".join(request.describe() for request in card.log().requests)
            failures.append(f"phase {name!r}: {error}\nconfig requests of the page:\n{requests}")
    print(f"E2E_CONFIG_SYNC_DEADLINE: {json.dumps(evidence)}", flush=True)
    assert not failures, "\n\n".join(failures)
    return evidence
