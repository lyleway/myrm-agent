"""Behavior tests of the in-page fetch shim: its JavaScript runs in Node against a stub ``fetch`` that plays the backend.

One Node process plays every scenario (timers of a few tens of milliseconds, no browser, no network) and prints what
each one observed as JSON; the tests below read that outcome scenario by scenario.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import cast

import pytest

from tests.e2e.config_sync_deadline.fault_shim import FAULT_SHIM_JS

# The page: `window` is the global object, `location` an app URL, `fetch` a backend that answers every call with a small
# JSON body (with headers a real backend may send) and remembers what it was asked. The shim source arrives on stdin.
_SCENARIOS_JS = r"""
const shimSource = require('node:fs').readFileSync(0, 'utf8');
globalThis.window = globalThis;
Object.defineProperty(globalThis, 'location', { value: { href: 'http://app.test/settings/search' }, configurable: true });

const realCalls = [];
window.fetch = async (input, init) => {
  realCalls.push({ url: typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, hasSignal: Boolean(init && init.signal) });
  return new Response('{"ok":true}', {
    status: 200,
    headers: { 'content-type': 'application/json', 'content-length': '11', 'content-encoding': 'gzip' },
  });
};

const install = () => (0, eval)(shimSource);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const snapshot = () => window.__cfgFault.snapshot();
const lastEntry = () => snapshot().entries.at(-1);
const arm = (...args) => window.__cfgFault.arm(...args);
const post = (marker, init = {}) =>
  window.fetch('/api/v1/config/sync', { method: 'POST', body: JSON.stringify({ k: marker }), ...init });
// Tracks a promise: `settled` stays null while it is pending, then holds 'resolved' or the name of its rejection.
const track = (promise) => {
  const state = { settled: null };
  promise.then(() => { state.settled = 'resolved'; }, (error) => { state.settled = error && error.name; });
  return state;
};

const outcome = {};
(async () => {
  outcome.install = { first: install(), second: install() };

  await window.fetch('http://app.test/api/v1/other');
  await window.fetch('/api/v1/config?keys=chatSettings');
  const passed = lastEntry();
  outcome.passThrough = {
    realCalls: realCalls.length, logged: snapshot().entries.length, method: passed.method, path: passed.path,
    fault: passed.fault, status: passed.status, answered: passed.headersAt !== null, bodyAt: passed.bodyAt, abortedAt: passed.abortedAt,
  };

  await window.fetch(new URL('http://app.test/api/v1/config/sync'), { method: 'POST', body: '{}' });
  const fromUrl = lastEntry();
  await window.fetch(new Request('http://app.test/api/v1/config/webFetchEscalation', { method: 'PUT', body: '{}' }));
  const fromRequest = lastEntry();
  outcome.inputKinds = { urlMethod: fromUrl.method, urlPath: fromUrl.path, requestMethod: fromRequest.method, requestPath: fromRequest.path };

  await window.fetch('/api/v1/config/sync', {
    method: 'POST',
    body: JSON.stringify({ a: 'jina-e2e-aaa1x', b: 'jina-e2e-aaa1x', c: 'jina-e2e-aaa1y', d: 'jina-E2E-upper', e: 'unrelated' }),
  });
  outcome.markers = { listed: lastEntry().markers };

  {
    arm('hold_headers', 'jina-e2e-hold1', 0, true, 'POST');
    const controller = new AbortController();
    const callsBefore = realCalls.length;
    const held = track(post('jina-e2e-hold1', { signal: controller.signal }));
    await sleep(40);
    const before = { settled: held.settled, entry: lastEntry() };
    const realCall = realCalls.at(-1);
    controller.abort();
    await sleep(10);
    const after = lastEntry();
    outcome.hold = {
      pendingBeforeAbort: before.settled === null, fault: before.entry.fault, answeredBeforeAbort: before.entry.headersAt !== null,
      abortedBeforeAbort: before.entry.abortedAt !== null, realCallsMade: realCalls.length - callsBefore,
      realRequestHasNoSignal: realCall.hasSignal === false, settledAfterAbort: held.settled,
      abortedAtRecorded: after.abortedAt !== null, status: after.status,
    };
  }

  {
    arm('hold_headers', 'jina-e2e-hold2', 0, false, 'POST');
    const controller = new AbortController();
    const callsBefore = realCalls.length;
    const held = track(post('jina-e2e-hold2', { signal: controller.signal }));
    await sleep(20);
    controller.abort();
    await sleep(10);
    outcome.holdWithoutApply = { realCallsMade: realCalls.length - callsBefore, settled: held.settled };
  }

  {
    arm('slow_headers', 'jina-e2e-slowh', 80, true, 'POST');
    const startedAt = performance.now();
    const response = await post('jina-e2e-slowh');
    const entry = lastEntry();
    outcome.slowHeaders = {
      status: response.status, waitedMs: performance.now() - startedAt, headersAfterStartMs: entry.headersAt - entry.startedAt, fault: entry.fault,
    };
  }

  {
    arm('slow_headers', 'jina-e2e-slowh2', 60000, true, 'POST');
    const controller = new AbortController();
    const waiting = track(post('jina-e2e-slowh2', { signal: controller.signal }));
    await sleep(20);
    controller.abort();
    await sleep(10);
    const entry = lastEntry();
    outcome.slowHeadersCancelled = { settled: waiting.settled, abortedAtRecorded: entry.abortedAt !== null, answered: entry.headersAt !== null };
  }

  {
    arm('slow_body', 'jina-e2e-slowb', 80, true, 'POST');
    const response = await post('jina-e2e-slowb');
    const atHeaders = lastEntry();
    const text = await response.text();
    const atEnd = lastEntry();
    outcome.slowBody = {
      status: response.status, text, fault: atEnd.fault,
      headersBeforeBody: atHeaders.headersAt !== null && atHeaders.bodyAt === null,
      bodyAfterHeadersMs: atEnd.bodyAt - atEnd.headersAt,
      contentLengthDropped: !response.headers.has('content-length'),
      contentEncodingDropped: !response.headers.has('content-encoding'),
      contentType: response.headers.get('content-type'),
    };
  }

  {
    arm('stall_body', 'jina-e2e-stall', 0, true, 'POST');
    const controller = new AbortController();
    const response = await post('jina-e2e-stall', { signal: controller.signal });
    const read = track(response.text());
    await sleep(50);
    const before = { settled: read.settled, entry: lastEntry() };
    controller.abort();
    await sleep(10);
    const after = lastEntry();
    outcome.stallBody = {
      status: response.status, readPendingBeforeAbort: before.settled === null, answeredBeforeAbort: before.entry.headersAt !== null,
      abortedBeforeAbort: before.entry.abortedAt !== null, readSettledAfterAbort: read.settled,
      bodyEverDelivered: after.bodyAt !== null, abortedAtRecorded: after.abortedAt !== null,
    };
  }

  {
    arm('hold_headers', 'jina-e2e-only', 0, false, 'POST');
    await post('jina-e2e-else');
    const withoutNeedle = lastEntry().fault;
    await window.fetch('/api/v1/config/jina-e2e-only');
    const otherMethod = lastEntry().fault;
    const controller = new AbortController();
    const held = track(post('jina-e2e-only', { signal: controller.signal }));
    await sleep(10);
    const matching = lastEntry().fault;
    await post('jina-e2e-only');
    const afterConsumed = lastEntry().fault;
    controller.abort();
    await sleep(10);
    outcome.selection = { withoutNeedle, otherMethod, matching, afterConsumed, heldSettled: held.settled };
  }

  {
    arm('hold_headers', '/config/webFetchEscalation', 0, false, 'GET');
    await window.fetch('/api/v1/config/sync', { method: 'POST', body: '{"note":"/config/webFetchEscalation"}' });
    const postWithFragment = lastEntry().fault;
    const controller = new AbortController();
    const held = track(window.fetch('/api/v1/config/webFetchEscalation', { signal: controller.signal }));
    await sleep(10);
    const entry = lastEntry();
    controller.abort();
    await sleep(10);
    outcome.getByPath = { postWithFragment, fault: entry.fault, method: entry.method, path: entry.path, heldSettled: held.settled };
  }

  {
    const controller = new AbortController();
    controller.abort();
    await post('jina-e2e-late', { signal: controller.signal });
    const entry = lastEntry();
    outcome.alreadyAborted = { abortedAtIsStart: entry.abortedAt === entry.startedAt };
  }

  {
    const taken = snapshot();
    taken.entries[0].fault = 'mutated';
    outcome.snapshot = {
      hasClock: typeof taken.now === 'number' && typeof taken.timeOrigin === 'number',
      entryCount: taken.entries.length === snapshot().entries.length,
      isACopy: snapshot().entries[0].fault !== 'mutated',
    };
  }

  process.stdout.write(JSON.stringify(outcome), () => process.exit(0));
})().catch((error) => { console.error(error); process.exit(1); });
"""

_Facts = dict[str, object]


@pytest.fixture(scope="module")
def outcome() -> dict[str, _Facts]:
    node = shutil.which("node")
    assert node, "Node.js is required to run the JavaScript of the config-sync fault shim"
    completed = subprocess.run(
        [node, "-e", _SCENARIOS_JS], input=FAULT_SHIM_JS, capture_output=True, text=True, timeout=60, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout, "the scenarios ended before printing an outcome: a request the shim held was never released"
    return cast(dict[str, _Facts], json.loads(completed.stdout))


def _number(value: object) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool), value
    return float(value)


def test_installing_twice_reuses_the_first_installation(outcome: dict[str, _Facts]) -> None:
    assert outcome["install"] == {"first": {"installed": True, "reused": False}, "second": {"installed": True, "reused": True}}


def test_a_call_to_anything_but_the_config_api_passes_through_unlogged_and_a_config_call_is_logged(
    outcome: dict[str, _Facts],
) -> None:
    assert outcome["passThrough"] == {
        "realCalls": 2,
        "logged": 1,
        "method": "GET",
        "path": "/api/v1/config?keys=chatSettings",
        "fault": "pass",
        "status": 200,
        "answered": True,
        "bodyAt": None,
        "abortedAt": None,
    }


def test_the_method_and_path_of_a_url_or_request_object_are_logged_like_those_of_a_string(outcome: dict[str, _Facts]) -> None:
    assert outcome["inputKinds"] == {
        "urlMethod": "POST",
        "urlPath": "/api/v1/config/sync",
        "requestMethod": "PUT",
        "requestPath": "/api/v1/config/webFetchEscalation",
    }


def test_the_markers_of_a_request_body_are_listed_once_each_in_the_order_they_first_appear(outcome: dict[str, _Facts]) -> None:
    assert outcome["markers"] == {"listed": ["jina-e2e-aaa1x", "jina-e2e-aaa1y"]}


def test_a_held_write_gets_no_answer_while_the_backend_still_takes_it_until_the_app_cancels_it(
    outcome: dict[str, _Facts],
) -> None:
    assert outcome["hold"] == {
        "pendingBeforeAbort": True,
        "fault": "hold_headers",
        "answeredBeforeAbort": False,
        "abortedBeforeAbort": False,
        "realCallsMade": 1,
        "realRequestHasNoSignal": True,
        "settledAfterAbort": "AbortError",
        "abortedAtRecorded": True,
        "status": None,
    }


def test_a_held_write_that_is_not_applied_never_reaches_the_backend(outcome: dict[str, _Facts]) -> None:
    assert outcome["holdWithoutApply"] == {"realCallsMade": 0, "settled": "AbortError"}


def test_slow_headers_arrive_after_the_delay_with_the_real_answer(outcome: dict[str, _Facts]) -> None:
    facts = outcome["slowHeaders"]

    assert (facts["status"], facts["fault"]) == (200, "slow_headers")
    assert _number(facts["waitedMs"]) >= 75
    assert _number(facts["headersAfterStartMs"]) >= 75


def test_an_app_that_gives_up_during_the_wait_for_slow_headers_gets_an_abort_and_no_answer(outcome: dict[str, _Facts]) -> None:
    assert outcome["slowHeadersCancelled"] == {"settled": "AbortError", "abortedAtRecorded": True, "answered": False}


def test_a_slow_body_follows_its_headers_after_the_delay_without_the_headers_that_describe_the_real_bytes(
    outcome: dict[str, _Facts],
) -> None:
    facts = outcome["slowBody"]

    assert (facts["status"], facts["text"], facts["fault"]) == (200, '{"ok":true}', "slow_body")
    assert facts["headersBeforeBody"] is True
    assert _number(facts["bodyAfterHeadersMs"]) >= 75
    assert (facts["contentLengthDropped"], facts["contentEncodingDropped"]) == (True, True)
    assert facts["contentType"] == "application/json"


def test_a_stalled_body_never_ends_and_fails_the_read_when_the_app_cancels(outcome: dict[str, _Facts]) -> None:
    assert outcome["stallBody"] == {
        "status": 200,
        "readPendingBeforeAbort": True,
        "answeredBeforeAbort": True,
        "abortedBeforeAbort": False,
        "readSettledAfterAbort": "AbortError",
        "bodyEverDelivered": False,
        "abortedAtRecorded": True,
    }


def test_an_armed_fault_takes_only_the_request_with_its_needle_and_method_and_only_once(outcome: dict[str, _Facts]) -> None:
    assert outcome["selection"] == {
        "withoutNeedle": "pass",
        "otherMethod": "pass",
        "matching": "hold_headers",
        "afterConsumed": "pass",
        "heldSettled": "AbortError",
    }


def test_a_get_is_matched_on_its_path_and_never_on_a_body_that_names_the_path(outcome: dict[str, _Facts]) -> None:
    assert outcome["getByPath"] == {
        "postWithFragment": "pass",
        "fault": "hold_headers",
        "method": "GET",
        "path": "/api/v1/config/webFetchEscalation",
        "heldSettled": "AbortError",
    }


def test_a_request_whose_signal_is_already_aborted_counts_as_cancelled_at_its_start(outcome: dict[str, _Facts]) -> None:
    assert outcome["alreadyAborted"] == {"abortedAtIsStart": True}


def test_a_snapshot_carries_the_page_clock_and_copies_of_the_entries(outcome: dict[str, _Facts]) -> None:
    assert outcome["snapshot"] == {"hasClock": True, "entryCount": True, "isACopy": True}
