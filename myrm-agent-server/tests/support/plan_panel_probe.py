"""Page-side recorder and judgment for the plan panel LIVE Chrome E2E.

One real turn leaves a timeline in the page: every ``plan_update`` stream event, every change of the rows the plan
panel lists (with whether the turn had ended by then), every read of the plan endpoint with what it answered, and every
change of the open chat. The judgment here pairs the events with the panel, so a run proves that each plan the stream
announced reached the user's panel before the turn ended, not only that the server stored it.
"""

from __future__ import annotations

from dataclasses import dataclass

# A plan row as the panel shows it: its text and its status.
Step = tuple[str, str]

# The plan endpoint names a cancelled todo "skipped"; every other status keeps its name.
STEP_STATUS = {
    "pending": "pending",
    "in_progress": "in_progress",
    "completed": "completed",
    "blocked": "blocked",
    "cancelled": "skipped",
}

DONE_MARKER = "PLAN_PANEL_DONE"

# What the user sees of the plan: one row per step the panel lists. A row counts as seen only when it takes up space.
PANEL_JS = r"""(() => ({
  width: window.innerWidth,
  steps: Array.from(document.querySelectorAll('[data-testid="goal-plan-step"]')).map((el) => ({
    text: (el.textContent || '').replace(/^\s*\d+\.\s*/, '').trim(),
    status: el.getAttribute('data-status'),
    visible: el.getClientRects().length > 0,
  })),
}))()"""

# Starts a fresh timeline for one turn. The panel, the chat store and the stream hook are all event driven, so the
# timeline does not depend on timers (a background tab throttles them): it keeps every `plan_update` stream event, every
# change of the panel's rows together with whether the turn had ended, every read of the plan endpoint with what it
# answered, and every change of the open chat (a new chat id restarts the panel's plan sync).
RECORDER_JS = r"""(() => {
  const rec = window.__planE2e ?? (window.__planE2e = { installed: false, events: [], panel: [], planReads: [], chats: [], t0: 0 });
  const openChat = () => window.__myrmChatStore?.getState?.()?.chatId ?? null;
  rec.events.length = 0;
  rec.panel.length = 0;
  rec.planReads.length = 0;
  rec.chats.length = 0;
  rec.t0 = performance.now();
  const now = () => Math.round(performance.now() - rec.t0);
  rec.chats.push({ atMs: 0, chatId: openChat() });
  if (!rec.installed) {
    rec.installed = true;
    let lastKey = '';
    const record = () => {
      const steps = Array.from(document.querySelectorAll('[data-testid="goal-plan-step"]')).map((el) => ({
        text: (el.textContent || '').replace(/^\s*\d+\.\s*/, '').trim(),
        status: el.getAttribute('data-status'),
        visible: el.getClientRects().length > 0,
      }));
      const messages = window.__myrmChatStore?.getState?.()?.messages ?? [];
      const assistants = messages.filter((m) => m?.role === 'assistant');
      const ended = assistants.length > 0 && Boolean(assistants[assistants.length - 1]?.completionStatus);
      const key = JSON.stringify([steps, ended]);
      if (key === lastKey) return;
      lastKey = key;
      rec.panel.push({ atMs: now(), steps, ended });
    };
    new MutationObserver(record).observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['data-status'],
    });
    window.__myrmChatStore?.subscribe?.(() => {
      const chatId = openChat();
      if (chatId !== rec.chats[rec.chats.length - 1]?.chatId) rec.chats.push({ atMs: now(), chatId });
      record();
    });
    const tap = window.__MYRM_E2E_RECORD_SSE__;
    window.__MYRM_E2E_RECORD_SSE__ = (type, messageId, data) => {
      if (type === 'plan_update') {
        rec.events.push({
          atMs: now(),
          revision: data?.revision ?? null,
          todos: (data?.todos ?? []).map((todo) => ({ content: todo.content, status: todo.status })),
        });
      }
      tap?.(type, messageId, data);
    };
    const nativeFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : (input?.url ?? '');
      const answer = nativeFetch(input, init);
      if (!/\/goals\/[^/]+\/plan(\?|$)/.test(url)) return answer;
      const read = { atMs: now(), doneMs: null, status: null, revision: null };
      rec.planReads.push(read);
      return answer.then(
        (res) => {
          read.doneMs = now();
          read.status = res.status;
          res.clone().json().then((body) => { read.revision = body?.plan?.revision ?? null; }, () => {});
          return res;
        },
        (error) => {
          read.doneMs = now();
          read.status = -1;
          throw error;
        },
      );
    };
  }
  return { ok: true, hasStreamTap: typeof window.__MYRM_E2E_RECORD_SSE__ === 'function', hasStore: Boolean(window.__myrmChatStore?.subscribe) };
})()"""

TURN_PROBE_JS = """(() => {
  const messages = window.__myrmChatStore?.getState?.()?.messages ?? [];
  const assistants = messages.filter((m) => m?.role === 'assistant');
  return {
    hasRecorder: Boolean(window.__planE2e?.installed),
    events: window.__planE2e?.events.length ?? 0,
    ended: assistants.length > 0 && Boolean(assistants[assistants.length - 1]?.completionStatus),
  };
})()"""

DUMP_JS = "(() => JSON.parse(JSON.stringify({ ...(window.__planE2e ?? {}), width: window.innerWidth })))()"


@dataclass(frozen=True)
class PlanEvent:
    """One ``plan_update`` stream event: when the page received it and the plan it carried."""

    at_ms: int
    revision: int
    steps: list[Step]


@dataclass(frozen=True)
class PanelRow:
    """The plan panel at one moment: the rows it listed, whether all took up space, whether the turn had ended."""

    at_ms: int
    steps: list[Step]
    visible: bool
    ended: bool


@dataclass(frozen=True)
class PlanRead:
    """One read of the plan endpoint by the page: when it started, when it ended, its status and the revision it served.

    ``status`` is None while the read is in flight and -1 when it failed; ``revision`` is None when no plan was served.
    """

    at_ms: int
    done_ms: int | None
    status: int | None
    revision: int | None


@dataclass(frozen=True)
class OpenChat:
    """The chat the page had open from ``at_ms`` on."""

    at_ms: int
    chat_id: str | None


@dataclass(frozen=True)
class Turn:
    """What one real turn left behind in the page."""

    chat_id: str
    width: int
    events: list[PlanEvent]
    panel: list[PanelRow]
    plan_reads: list[PlanRead]
    open_chats: list[OpenChat]


def prompt(tag: str) -> str:
    """Make the model write a three-item plan and then update two of its items, with a long reply afterwards.

    The long reply keeps the turn running after the last write, so the panel has time to show the final plan before the
    terminal event.
    """
    return (
        "Use only the todo_write tool, exactly twice, and call no other tool.\n"
        "First call: merge=false, goal='Plan panel check', todos=["
        f"{{'id':'p1','content':'Alpha {tag}','status':'in_progress'}}, "
        f"{{'id':'p2','content':'Beta {tag}','status':'pending'}}, "
        f"{{'id':'p3','content':'Gamma {tag}','status':'pending'}}].\n"
        "Second call, after the first one returned: merge=true, todos=["
        "{'id':'p1','status':'completed'}, {'id':'p2','status':'in_progress'}].\n"
        f"Then write the numbers 1 to 60, one per line, and end with the line {DONE_MARKER}."
    )


def as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict), f"expected an object from the page, got {value!r}"
    return value


def as_list(value: object) -> list[object]:
    assert isinstance(value, list), f"expected a list from the page, got {value!r}"
    return value


def as_int(value: object) -> int:
    assert isinstance(value, int), f"expected a number from the page, got {value!r}"
    return value


def as_optional_int(value: object) -> int | None:
    return None if value is None else as_int(value)


def panel_steps(rows: object) -> tuple[list[Step], bool]:
    """The (text, status) of each panel row, and whether every row takes up space."""
    steps = [as_dict(row) for row in as_list(rows)]
    return [(str(step["text"]), str(step["status"])) for step in steps], all(step["visible"] is True for step in steps)


def plan_steps(plan: dict[str, object]) -> list[Step]:
    """The (description, status) of each step of a plan the server serves."""
    steps = [as_dict(step) for step in as_list(plan["steps"])]
    return [(str(step["description"]), str(step["status"])) for step in steps]


def parse_turn(chat_id: str, raw: object) -> Turn:
    """Turn the recorder's dump into typed events and panel rows (a todo's status becomes the plan step's status)."""
    rec = as_dict(raw)
    events = []
    for item in map(as_dict, as_list(rec["events"])):
        todos = [as_dict(todo) for todo in as_list(item["todos"])]
        steps = [(str(todo["content"]), STEP_STATUS[str(todo["status"])]) for todo in todos]
        events.append(PlanEvent(as_int(item["atMs"]), as_int(item["revision"]), steps))
    panel = []
    for item in map(as_dict, as_list(rec["panel"])):
        steps, visible = panel_steps(item["steps"])
        panel.append(PanelRow(as_int(item["atMs"]), steps, visible, item["ended"] is True))
    reads = [
        PlanRead(
            as_int(item["atMs"]),
            as_optional_int(item["doneMs"]),
            as_optional_int(item["status"]),
            as_optional_int(item["revision"]),
        )
        for item in map(as_dict, as_list(rec["planReads"]))
    ]
    chats = [
        OpenChat(as_int(item["atMs"]), None if item["chatId"] is None else str(item["chatId"]))
        for item in map(as_dict, as_list(rec["chats"]))
    ]
    return Turn(chat_id, as_int(rec["width"]), events, panel, reads, chats)


def shown_in_turn(event: PlanEvent, panel: list[PanelRow]) -> PanelRow | None:
    """The first panel state that lists the event's plan after the event arrived and before the turn ended."""
    return next((row for row in panel if row.at_ms >= event.at_ms and not row.ended and row.steps == event.steps), None)


def timeline(turn: Turn) -> str:
    """One line for the run log: which events reached the panel in time and how long each took."""
    shown = [shown_in_turn(event, turn.panel) for event in turn.events]
    latencies = [None if row is None else row.at_ms - event.at_ms for event, row in zip(turn.events, shown, strict=True)]
    reads = [(read.at_ms, read.done_ms, read.status, read.revision) for read in turn.plan_reads]
    opened = [(chat.at_ms, chat.chat_id) for chat in turn.open_chats]
    return (
        f"chat={turn.chat_id} events={len(turn.events)} revisions={[event.revision for event in turn.events]} "
        f"event_at_ms={[event.at_ms for event in turn.events]} shown_in_turn={[row is not None for row in shown]} "
        f"event_to_panel_ms={latencies} plan_reads(at,done,status,revision)={reads} open_chats={opened} width={turn.width}"
    )


def check_turn(turn: Turn, plan: dict[str, object]) -> list[str]:
    """Assert what must hold whatever the model did; return what only a model that wrote twice can show."""
    events = turn.events
    revisions = [event.revision for event in events]
    assert revisions == list(range(1, len(events) + 1)), (
        f"each write must stream exactly one plan_update: revisions={revisions} served_revision={plan.get('revision')}"
    )
    assert plan.get("revision") == len(events), f"events={len(events)} served plan={plan}"
    assert plan_steps(plan) == events[-1].steps, f"the served plan differs from the last event: {plan} vs {events[-1]}"

    shown = [shown_in_turn(event, turn.panel) for event in events]
    assert shown[-1] is not None, (
        f"the last plan never reached the panel before the turn ended: {timeline(turn)} events={events} panel={turn.panel[-12:]}"
    )
    assert all(row.visible for row in shown if row is not None), (
        f"the panel listed the plan out of the user's sight (width={turn.width}): {shown}"
    )

    if len(events) < 2:
        return [f"the model wrote the plan {len(events)} time(s)"]
    if sum(row is not None for row in shown) < 2:
        return [f"only the last of {len(events)} plans reached the panel in time: {timeline(turn)}"]
    return []
