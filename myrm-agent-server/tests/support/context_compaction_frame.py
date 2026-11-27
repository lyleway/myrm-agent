"""The app at a pinned width, to check where the strategy panel sits and how its compression result reads in each theme.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: CDP page helpers, wait_for_state)
- tests.support.context_compaction_e2e (POS: ATTACH_CHAT_JS, the script that loads a chat through the E2E bridge)

[OUTPUT]
- open_app_frame() / close_app_frame(): a same-origin frame of a fixed size loads a route of the app, ready once the chat composer there is hydrated; closing it drops that page
- attach_chat_in_frame(): loads a chat into the store of the app in the frame, through its E2E bridge
- in_frame(): a page probe as the app in the frame would run it, so the helpers that probe a page work on it unchanged
- answer_compact_with_js(): the app in the frame gets a reply the test chose to every compact request, for the lines a healthy server cannot be made to produce
- panel_layout_js(): where the open strategy panel sits and whether the fields of its compress control can be reached
- outcome_look_js(): the contrast and the room of the line under the compress button, in a given theme

[POS]
``window.resizeTo`` does nothing in this Chrome and the app switches to its phone layout below 1024px, so only a frame
of a pinned width shows the app in one layout or the other. Contrast is measured on what a user sees: every ancestor's
background and the text colour are composited on a canvas, which resolves whatever colour syntax the theme variables use.
"""

from __future__ import annotations

import json

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state
from tests.support.context_compaction_e2e import ATTACH_CHAT_JS

FRAME_ID = "e2e-app-frame"
# WCAG AA for normal-size text.
MIN_TEXT_CONTRAST = 4.5

_FRAME_STATE_JS = """((id, width, height, route) => {
  const frame = document.getElementById(id);
  if (!frame) {
    const created = document.createElement('iframe');
    created.id = id;
    created.style.cssText = `position:fixed;left:0;top:0;width:${width}px;height:${height}px;border:0;z-index:2147483647;background:#fff`;
    created.src = location.origin + route;
    document.body.appendChild(created);
    return { ready: false, stage: 'loading' };
  }
  const win = frame.contentWindow;
  const input = win.document.querySelector('[data-chat-input]');
  const hydrated = !!input && Object.keys(input).some((key) => key.startsWith('__reactProps'));
  return { ready: hydrated, stage: 'hydrating', innerWidth: win.innerWidth, hasInput: !!input, path: win.location.pathname };
})"""

_BRIDGE_READY_JS = "(() => ({ ready: typeof window.__MYRM_E2E_CHAT__?.attachToChat === 'function' }))()"

# Where the open panel sits and whether its fields can be pressed: each is scrolled into view (the panel scrolls inside its
# sheet or popover) and the element at its centre has to be the field itself, not something laid over it.
_PANEL_LAYOUT_JS = """((compressLabel) => {
  const panel = document.querySelector('[data-testid="context-usage-panel"]');
  const container = panel ? panel.closest('[role="dialog"]') : null;
  if (!panel || !container) return { ready: false, hasPanel: !!panel };
  const rect = container.getBoundingClientRect();
  // The sheet slides in and the popover is positioned after mounting: a rectangle that stopped moving is the settled one.
  const key = [rect.left, rect.top, rect.width, rect.height].map(Math.round).join(',');
  const settled = window.__e2ePanelKey === key;
  window.__e2ePanelKey = key;
  const reach = (el) => {
    if (!el) return null;
    el.scrollIntoView({ block: 'center' });
    const box = el.getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    const inside = box.left >= 0 && box.right <= window.innerWidth && box.top >= 0 && box.bottom <= window.innerHeight;
    return { inside, reachable: !!hit && el.contains(hit), width: Math.round(box.width) };
  };
  const button = Array.from(panel.querySelectorAll('button')).find((el) => (el.textContent || '').trim() === compressLabel);
  const root = document.documentElement;
  return {
    ready: settled && !!button,
    viewport: [window.innerWidth, window.innerHeight],
    container: { left: Math.round(rect.left), top: Math.round(rect.top), right: Math.round(rect.right), bottom: Math.round(rect.bottom) },
    insideViewport: rect.left >= 0 && rect.top >= 0 && rect.right <= window.innerWidth && rect.bottom <= window.innerHeight,
    pageOverflowsX: root.scrollWidth > root.clientWidth,
    panelOverflowsX: container.scrollWidth > container.clientWidth,
    input: reach(panel.querySelector('input[type="text"]')),
    button: reach(button),
  };
})"""

# The theme is switched by the class on <html>; transitions are switched off so a measurement right after the swap does
# not read the previous theme's colours.
_OUTCOME_LOOK_JS = """((head, theme) => {
  const panel = document.querySelector('[data-testid="context-usage-panel"]');
  const container = panel ? panel.closest('[role="dialog"]') : null;
  const line = panel ? Array.from(panel.querySelectorAll('span')).find((el) => (el.textContent || '').includes(head)) : null;
  if (!line || !container) return { ready: false, hasLine: !!line };
  if (!document.getElementById('e2e-no-transition')) {
    const style = document.createElement('style');
    style.id = 'e2e-no-transition';
    style.textContent = '*, *::before, *::after { transition: none !important; }';
    document.head.appendChild(style);
  }
  document.documentElement.classList.remove('light', 'dark');
  document.documentElement.classList.add(theme);
  line.scrollIntoView({ block: 'center' });
  const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const paint = (css) => { ctx.fillStyle = css; ctx.fillRect(0, 0, 1, 1); };
  const pixel = () => Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3);
  const luminance = (rgb) => {
    const [r, g, b] = rgb.map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  paint('#ffffff');
  const chain = [];
  for (let node = line; node; node = node.parentElement) chain.unshift(node);
  for (const node of chain) paint(window.getComputedStyle(node).backgroundColor);
  const background = pixel();
  paint('rgb(' + background.join(',') + ')');
  paint(window.getComputedStyle(line).color);
  const text = pixel();
  const hi = Math.max(luminance(background), luminance(text));
  const lo = Math.min(luminance(background), luminance(text));
  const box = line.getBoundingClientRect();
  const room = container.getBoundingClientRect();
  return {
    ready: true,
    theme,
    ratio: (hi + 0.05) / (lo + 0.05),
    text,
    background,
    fontPx: parseFloat(window.getComputedStyle(line).fontSize),
    clipped: line.scrollWidth > line.clientWidth + 1,
    insideContainer: box.left >= room.left && box.right <= room.right,
  };
})"""


# Answers the compact request of the app in the frame with a reply the test chose, and passes every other request on. The
# reply is kept on ``window`` so the next press can be answered differently without installing the wrapper again.
_ANSWER_COMPACT_JS = r"""((answer) => {
  window.__e2eCompactAnswer = answer;
  if (!window.__e2eRealFetch) {
    window.__e2eRealFetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      const target = typeof input === 'string' ? input : input instanceof Request ? input.url : String(input);
      if (/\/chats\/[^/]+\/compact$/.test(new URL(target, location.href).pathname)) {
        const headers = { 'Content-Type': 'application/json' };
        return Promise.resolve(new Response(JSON.stringify(window.__e2eCompactAnswer), { status: 200, headers }));
      }
      return window.__e2eRealFetch(input, init);
    };
  }
  return { ready: true };
})"""


def in_frame(probe: str) -> str:
    """``probe`` (a page expression) as the app in the frame would run it, so a helper that probes a page works on the frame."""
    return (
        f"(() => {{ const frame = document.getElementById({json.dumps(FRAME_ID)});"
        f" return frame ? frame.contentWindow.eval({json.dumps(probe)}) : {{ ready: false, stage: 'no-frame' }}; }})()"
    )


def open_app_frame(
    client: ChromeMcpClient, page: McpPage, *, route: str, viewport: tuple[int, int], timeout_sec: float = 240.0
) -> None:
    """Load ``route`` of the app in a frame of ``viewport`` size on top of the page and wait until its composer is hydrated.

    The frame is a second full app load: on a cold dev server its chunks can take minutes to hydrate.
    """
    width, height = viewport
    state = f"({_FRAME_STATE_JS})({json.dumps(FRAME_ID)}, {width}, {height}, {json.dumps(route)})"
    shown = wait_for_state(client, page, state, timeout_sec=timeout_sec)
    assert shown.get("innerWidth") == width, f"the frame is not {width}px wide: {shown!r}"


def close_app_frame(client: ChromeMcpClient, page: McpPage) -> None:
    """Remove the frame: the next ``open_app_frame`` starts a fresh page, with no state left from the one before."""
    client.evaluate(
        page,
        f"(() => {{ document.getElementById({json.dumps(FRAME_ID)})?.remove(); return {{ ok: true }}; }})()",
        timeout_sec=15.0,
    )


def attach_chat_in_frame(client: ChromeMcpClient, page: McpPage, chat_id: str) -> None:
    wait_for_state(client, page, in_frame(_BRIDGE_READY_JS), timeout_sec=90.0)
    attached = client.evaluate(page, in_frame(f"({ATTACH_CHAT_JS})({json.dumps(chat_id)})"), timeout_sec=60.0)
    assert isinstance(attached, dict) and attached.get("ok") is True, f"attachToChat in the frame failed: {attached!r}"


def answer_compact_with_js(answer: dict[str, object]) -> str:
    """Make the app in the frame get ``answer`` (a whole response body) from every compact request it sends from now on."""
    return in_frame(f"({_ANSWER_COMPACT_JS})({json.dumps(answer)})")


def panel_layout_js(compress_label: str) -> str:
    return in_frame(f"({_PANEL_LAYOUT_JS})({json.dumps(compress_label)})")


def outcome_look_js(head: str, theme: str) -> str:
    return in_frame(f"({_OUTCOME_LOOK_JS})({json.dumps(head)}, {json.dumps(theme)})")
