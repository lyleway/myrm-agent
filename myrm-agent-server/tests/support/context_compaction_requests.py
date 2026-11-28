"""The compactions a Chrome E2E page starts, as the page saw them: what it sent, that the server accepted it, how its run ended.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: CDP page helpers, wait_for_state)

[OUTPUT]
- CompactRequest: one manual compaction as the page saw it
- record_compact_requests() / read_compact_requests(): install the page-side recorder, read what it kept
- wait_for_compact_requests_sent() / wait_for_compact_answers(): wait until compactions were started, or until none is still running

[POS]
A compaction takes the real model minutes and the request that starts it is answered at once, so a test does not await
it: a recorder around the page's ``fetch`` keeps each start together with the outcome of the run the page then polls,
which is what the page itself shows the user.
"""

from __future__ import annotations

from dataclasses import dataclass

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state

# Passive: every compaction the page starts goes through unchanged. The request that starts one is answered 202 at once, naming
# the run, and the page then polls that run, so a poll that reads an ended run gives every request that was answered with
# that run id its outcome (a request that joined a run in flight was answered with the same id).
_RECORD_COMPACT_REQUESTS_JS = r"""(() => {
  if (window.__e2eCompactRequests) return { ok: true, installed: false };
  const requests = [];
  window.__e2eCompactRequests = requests;
  const original = window.fetch.bind(window);
  const elapsed = (request) => Math.round(performance.now() - request.startedAt);
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const matched = /\/chats\/([^/]+)\/compact(\/runs\/([^/?]+))?(\?|$)/.exec(url);
    if (!matched) return original(input, init);
    const polledRunId = matched[3];
    if (polledRunId) {
      const polled = await original(input, init);
      polled.clone().json().then((body) => {
        const run = body?.data ?? body;
        if (run?.status !== 'succeeded' && run?.status !== 'failed') return;
        for (const request of requests) {
          if (request.runId !== polledRunId || request.state !== null) continue;
          Object.assign(request, { state: run.status, result: run.result ?? null, failure: run.error ?? null, ms: elapsed(request) });
        }
      }).catch(() => {});
      return polled;
    }
    const request = { runId: null, status: null, error: null, ms: null, sent: null, state: null, result: null, failure: null, startedAt: performance.now() };
    requests.push(request);
    try {
      request.sent = typeof init?.body === 'string' ? JSON.parse(init.body) : null;
    } catch {
      request.sent = null;
    }
    try {
      const response = await original(input, init);
      request.status = response.status;
      if (response.status === 202) {
        // Read before the response goes on: the page's first poll comes after it and must find the run id already recorded.
        const accepted = await response.clone().json().catch(() => null);
        request.runId = (accepted?.data ?? accepted)?.run_id ?? null;
      } else {
        request.ms = elapsed(request);
      }
      return response;
    } catch (error) {
      request.error = error?.name ?? String(error);
      request.ms = elapsed(request);
      throw error;
    }
  };
  return { ok: true, installed: true };
})()"""

# Ready once every compaction the page started has ended (or was refused, or has failed), and at least `count` were started.
_COMPACT_ANSWERED_JS = """((count) => {
  const requests = window.__e2eCompactRequests ?? [];
  const answered = (request) =>
    request.error !== null || (request.status !== null && (request.status !== 202 || request.state !== null));
  const open = requests.filter((request) => !answered(request)).length;
  return { ready: requests.length >= count && open === 0, sent: requests.length, unanswered: open };
})"""


@dataclass(frozen=True)
class CompactRequest:
    """One manual compaction as the page saw it: what it sent, that the server accepted it (``status`` is 202) and how its run ended.

    ``ms`` is how long the page waited for the run to end; ``state`` is ``succeeded`` (``result`` is what the service
    answered, a decline included) or ``failed`` (``failure`` is the error), and stays ``None`` while the run is going.
    """

    status: int | None
    error: str | None
    ms: int | None
    sent: dict[str, object] | None
    state: str | None
    result: dict[str, object] | None
    failure: dict[str, object] | None

    @property
    def compacted(self) -> bool:
        return bool(self.result and self.result.get("compacted") is True)

    @property
    def reason(self) -> object:
        return self.result.get("reason") if self.result else None

    @property
    def tokens_saved(self) -> int:
        saved = self.result.get("tokens_saved") if self.result else None
        return saved if isinstance(saved, int) else 0

    @property
    def message_count(self) -> int:
        count = self.result.get("message_count") if self.result else None
        return count if isinstance(count, int) else 0


def record_compact_requests(client: ChromeMcpClient, page: McpPage) -> None:
    installed = client.evaluate(page, _RECORD_COMPACT_REQUESTS_JS, timeout_sec=15.0)
    assert isinstance(installed, dict) and installed.get("ok") is True, f"request recorder not installed: {installed!r}"


def read_compact_requests(client: ChromeMcpClient, page: McpPage) -> list[CompactRequest]:
    """The compactions the page started since ``record_compact_requests``; one whose run is still going has no state or result yet."""
    seen = client.evaluate(page, "(() => ({ requests: window.__e2eCompactRequests ?? null }))()", timeout_sec=15.0)
    assert isinstance(seen, dict) and isinstance(seen.get("requests"), list), (
        f"the request recorder was lost with the page: {seen!r}"
    )
    return [
        CompactRequest(
            status=item.get("status") if isinstance(item.get("status"), int) else None,
            error=item.get("error") if isinstance(item.get("error"), str) else None,
            ms=item.get("ms") if isinstance(item.get("ms"), int) else None,
            sent=item.get("sent") if isinstance(item.get("sent"), dict) else None,
            state=item.get("state") if isinstance(item.get("state"), str) else None,
            result=item.get("result") if isinstance(item.get("result"), dict) else None,
            failure=item.get("failure") if isinstance(item.get("failure"), dict) else None,
        )
        for item in seen["requests"]
        if isinstance(item, dict)
    ]


def wait_for_compact_requests_sent(
    client: ChromeMcpClient, page: McpPage, page_url: str, count: int, *, timeout_sec: float = 30.0
) -> None:
    """Wait until the page has started at least ``count`` compactions, whether or not their runs have ended."""
    script = f"(() => ({{ ready: (window.__e2eCompactRequests ?? []).length >= {count} }}))()"
    wait_for_state(client, page, script, timeout_sec=timeout_sec, page_url=page_url)


def wait_for_compact_answers(client: ChromeMcpClient, page: McpPage, page_url: str, count: int, *, timeout_sec: float) -> None:
    """Wait until at least ``count`` compactions were started and none is still running."""
    wait_for_state(client, page, f"({_COMPACT_ANSWERED_JS})({count})", timeout_sec=timeout_sec, page_url=page_url)
