"""In-page ``fetch`` shim for config-sync Chrome E2E: delays or withholds the answer of one write and records what the app did to it.

The shim wraps ``window.fetch`` for ``/api/v1/config`` calls only and passes everything else through. A fault is armed
for the next request that carries a needle: a ``POST`` whose body holds a marker string, or a ``GET`` whose path holds a
fragment. That request is answered late, never, or with a body that never finishes, while the real backend still
receives it (unless ``apply`` is false). Every config request is logged with the time its headers arrived and the time
the app aborted it, so a test can tell a cancelled request from an abandoned one.

[INPUT]
- tests.support.chrome_mcp_e2e::ChromeMcpClient, McpPage (POS: Shared real-Chrome MCP helpers for formal UI E2E tests;
  imported for type checking only)

[OUTPUT]
- install_fault_shim(client, page): wrap the page's fetch for /api/v1/config calls and pass every other call through
- arm_fault(client, page, kind, needle, delay_ms=, apply=, method=): answer the next config request that carries the
  needle late, never, or with a body that never ends
- read_log(client, page): every config request since the install, with the header, body and abort moments on the page clock
- ConfigRequest / ConfigLog / FaultKind: one logged request, the log read together with the page clock, the four fault kinds

[POS]
Fault injection layer of the config-sync deadline E2E; the only code that touches the page's fetch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage

FaultKind = Literal["slow_headers", "hold_headers", "slow_body", "stall_body"]

# The shim lists the ``jina-e2e-<phase>`` strings in each request body as its ``markers``, so a test can pick its own writes.
FAULT_SHIM_JS = r"""(() => {
  if (window.__cfgFault) return { installed: true, reused: true };
  const realFetch = window.fetch.bind(window);
  const state = { armed: null, log: [] };
  const CONFIG_URL = /\/api\/v1\/config(\/|\?|$)/;
  const MARKERS = /jina-e2e-[a-z0-9]+/g;

  const abortError = () => new DOMException('The operation was aborted.', 'AbortError');

  // Resolves after `ms` (never for Infinity); rejects like an aborted fetch when the signal fires first.
  const wait = (ms, signal) => new Promise((resolve, reject) => {
    if (signal && signal.aborted) { reject(abortError()); return; }
    let timer = null;
    const onAbort = () => { clearTimeout(timer); reject(abortError()); };
    if (Number.isFinite(ms)) {
      timer = setTimeout(() => {
        if (signal) signal.removeEventListener('abort', onAbort);
        resolve();
      }, ms);
    }
    if (signal) signal.addEventListener('abort', onAbort, { once: true });
  });

  // A body that delivers `bytes` after `ms` (never for Infinity) and fails like an aborted read when the signal fires.
  const slowBody = (bytes, ms, signal, entry) => new ReadableStream({
    start(controller) {
      let timer = null;
      const onAbort = () => { clearTimeout(timer); controller.error(abortError()); };
      if (signal && signal.aborted) { onAbort(); return; }
      if (Number.isFinite(ms)) {
        timer = setTimeout(() => {
          entry.bodyAt = performance.now();
          controller.enqueue(bytes);
          controller.close();
        }, ms);
      }
      if (signal) signal.addEventListener('abort', onAbort, { once: true });
    },
  });

  // The real request lives on independently of the app's abort: the backend has taken the write by the time the answer is lost.
  const detached = (init) => ({ ...init, signal: undefined });

  const answered = (entry, response) => {
    entry.headersAt = performance.now();
    entry.status = response.status;
    return response;
  };

  const withBody = (delayOf) => async (url, init, signal, armed, entry) => {
    const real = await realFetch(url, detached(init));
    const bytes = new Uint8Array(await real.arrayBuffer());
    answered(entry, real);
    const headers = new Headers(real.headers);
    headers.delete('content-encoding');
    headers.delete('content-length');
    return new Response(slowBody(bytes, delayOf(armed), signal, entry), {
      status: real.status,
      statusText: real.statusText,
      headers,
    });
  };

  const faults = {
    slow_headers: async (url, init, signal, armed, entry) => {
      const real = realFetch(url, detached(init));
      await wait(armed.delayMs, signal);
      return answered(entry, await real);
    },
    hold_headers: async (url, init, signal, armed) => {
      if (armed.apply) realFetch(url, detached(init)).catch(() => undefined);
      await wait(Infinity, signal);
    },
    slow_body: withBody((armed) => armed.delayMs),
    stall_body: withBody(() => Infinity),
  };

  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (!CONFIG_URL.test(url)) return realFetch(input, init);
    const method = String((init && init.method) || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
    const body = init && typeof init.body === 'string' ? init.body : '';
    const parsed = new URL(url, location.href);
    const entry = {
      method,
      path: parsed.pathname + parsed.search,
      fault: 'pass',
      markers: [...new Set(body.match(MARKERS) || [])],
      startedAt: performance.now(),
      headersAt: null,
      bodyAt: null,
      status: null,
      abortedAt: null,
    };
    state.log.push(entry);
    const signal = init && init.signal;
    if (signal) {
      if (signal.aborted) entry.abortedAt = entry.startedAt;
      else signal.addEventListener('abort', () => { entry.abortedAt = performance.now(); }, { once: true });
    }
    const armed = state.armed;
    if (armed && method === armed.method && (method === 'POST' ? body : entry.path).includes(armed.needle)) {
      state.armed = null;
      entry.fault = armed.kind;
      return faults[armed.kind](url, init, signal, armed, entry);
    }
    return realFetch(input, init).then((response) => answered(entry, response));
  };

  window.__cfgFault = {
    arm: (kind, needle, delayMs, apply, method) => {
      state.armed = { kind, needle, delayMs, apply, method };
      return true;
    },
    snapshot: () => ({
      now: performance.now(),
      timeOrigin: performance.timeOrigin,
      entries: state.log.map((entry) => ({ ...entry })),
    }),
  };
  return { installed: true, reused: false };
})()"""


@dataclass(frozen=True, slots=True)
class ConfigRequest:
    """One ``/api/v1/config`` request the page made; times are ``performance.now()`` milliseconds of that page."""

    method: str
    path: str
    fault: str
    markers: tuple[str, ...]
    started_at_ms: float
    headers_at_ms: float | None
    body_at_ms: float | None
    status: int | None
    aborted_at_ms: float | None

    @property
    def is_config_sync(self) -> bool:
        return self.method == "POST" and self.path.endswith("/config/sync")

    @property
    def is_settled(self) -> bool:
        """Whether the request has ended: answered completely, or aborted by the app."""
        if self.aborted_at_ms is not None:
            return True
        if self.status is None:
            return False
        return self.fault not in ("slow_body", "stall_body") or self.body_at_ms is not None

    @property
    def latest_event_ms(self) -> float:
        """The last moment the request did anything: started, answered, finished its body, or was aborted."""
        return max(
            moment
            for moment in (self.started_at_ms, self.headers_at_ms, self.body_at_ms, self.aborted_at_ms)
            if moment is not None
        )

    def describe(self) -> str:
        """One line for a failure report: when the request started and what became of it, relative to its start."""

        def after_start(moment: float | None) -> str:
            return "-" if moment is None else f"+{moment - self.started_at_ms:.0f}ms"

        return (
            f"t={self.started_at_ms:.0f} {self.method} {self.path} fault={self.fault} "
            f"markers={','.join(self.markers) or '-'} status={self.status} headers={after_start(self.headers_at_ms)} "
            f"body={after_start(self.body_at_ms)} cancelled={after_start(self.aborted_at_ms)}"
        )


@dataclass(frozen=True, slots=True)
class ConfigLog:
    """The page's config requests read together with the page clock.

    A reader polls through a slow link, so "nothing happened for a while" must be judged on the page's own clock: two
    identical reads far apart in the reader's time say nothing about what the page did in between.
    """

    requests: tuple[ConfigRequest, ...]
    now_ms: float
    time_origin_ms: float

    @property
    def quiet_for_ms(self) -> float:
        """How long, on the page's clock, since any request started or ended; zero while one is still in flight."""
        if not all(request.is_settled for request in self.requests):
            return 0.0
        return self.now_ms - max((request.latest_event_ms for request in self.requests), default=0.0)

    def wall_ms(self, page_ms: float) -> float:
        """A page time as epoch milliseconds, comparable with the clock the backend stamps its writes with."""
        return self.time_origin_ms + page_ms


def _ms(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _parse_request(raw: object) -> ConfigRequest:
    if not isinstance(raw, dict):
        raise RuntimeError(f"config request entry is not an object: {raw!r}")
    started = _ms(raw.get("startedAt"))
    status = _ms(raw.get("status"))
    markers = raw.get("markers")
    if started is None or not isinstance(markers, list):
        raise RuntimeError(f"config request entry is malformed: {raw!r}")
    return ConfigRequest(
        method=str(raw.get("method")),
        path=str(raw.get("path")),
        fault=str(raw.get("fault")),
        markers=tuple(str(marker) for marker in markers),
        started_at_ms=started,
        headers_at_ms=_ms(raw.get("headersAt")),
        body_at_ms=_ms(raw.get("bodyAt")),
        status=None if status is None else int(status),
        aborted_at_ms=_ms(raw.get("abortedAt")),
    )


def install_fault_shim(client: ChromeMcpClient, page: McpPage) -> None:
    result = client.evaluate(page, FAULT_SHIM_JS)
    if not isinstance(result, dict) or result.get("installed") is not True:
        raise RuntimeError(f"config-sync fault shim did not install: {result!r}")


def arm_fault(
    client: ChromeMcpClient,
    page: McpPage,
    kind: FaultKind,
    needle: str,
    *,
    delay_ms: int = 0,
    apply: bool = True,
    method: Literal["GET", "POST"] = "POST",
) -> None:
    """Fault the next config request of ``method`` that carries ``needle``: in the body of a ``POST``, in the path of a
    ``GET``. ``apply`` lets the backend take a write first."""
    expression = (
        f"window.__cfgFault.arm({json.dumps(kind)}, {json.dumps(needle)}, {delay_ms}, {json.dumps(apply)}, {json.dumps(method)})"
    )
    if client.evaluate(page, expression) is not True:
        raise RuntimeError("config-sync fault shim is missing on the page (the page was reloaded?)")


def read_log(client: ChromeMcpClient, page: McpPage) -> ConfigLog:
    """Every config request the page has made since the shim was installed, in the order they started."""
    snapshot = client.evaluate(page, "window.__cfgFault.snapshot()")
    if not isinstance(snapshot, dict):
        raise RuntimeError(f"config-sync fault shim snapshot is unreadable: {snapshot!r}")
    entries, now, origin = snapshot.get("entries"), _ms(snapshot.get("now")), _ms(snapshot.get("timeOrigin"))
    if not isinstance(entries, list) or now is None or origin is None:
        raise RuntimeError(f"config-sync fault shim snapshot is unreadable: {snapshot!r}")
    return ConfigLog(tuple(_parse_request(entry) for entry in entries), now, origin)
