"""What the manual-compaction Chrome E2Es share on the page side: the toasts, the composer and the strategy panel.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: CDP page helpers, wait_for_state)
- tests.support.chrome_skill_hooks_slash_js (POS: the composer's page-side scripts)
- tests.support.context_compaction_e2e (POS: locale_text, the shipped wording)

[OUTPUT]
- record_toasts() / wait_for_toast() / read_toasts(): every toast the page shows, in the order it showed them, and a wait for one by its wording
- type_in_composer() / wait_for_palette_choice() / press_enter() / wait_for_composer(): a command typed and entered the way a user does
- wait_for_compact_dialog() / compact_dialog_is_open() / tick_dont_remind() / confirm_compact_dialog() / forget_compact_reminder() / read_compact_reminder(): the confirmation a typed `/compact <topic>` asks for, and the choice it stores
- panel_ready_js() / press_compress_js() / rearm_compress_js() / compressing_js() / outcome_js(): the compress control of the strategy panel behind the usage ring
- tokens_from_shown() / count_in(): the number a panel line or a toast states, read as a figure

[POS]
The page's own words are what a user reads, so a test takes them from the locale files and watches the page for them:
a toast outlives a poll only briefly, hence the observer instead of a look at the screen.
"""

from __future__ import annotations

import json
import re

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state
from tests.support.chrome_skill_hooks_slash_js import COMPOSER_READY_JS, TYPE_JS
from tests.support.context_compaction_e2e import locale_text

# A toast changes in place (a loading one turns into its result), so each look at a toast keeps the type and text it showed then.
_RECORD_TOASTS_JS = """(() => {
  if (window.__e2eToasts) return { ok: true, installed: false };
  const seen = [];
  window.__e2eToasts = seen;
  const record = () => {
    for (const el of document.querySelectorAll('[data-sonner-toast]')) {
      const text = (el.textContent || '').replace(/\\s+/g, ' ').trim();
      const type = el.getAttribute('data-type') || '';
      const stamp = `${type}|${text}`;
      if (!text || el.__e2eStamp === stamp) continue;
      el.__e2eStamp = stamp;
      seen.push({ type, text });
    }
  };
  new MutationObserver(record).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
  record();
  return { ok: true, installed: true };
})()"""

# Ready once one toast carries every fragment in order; the text between the fragments is what filled the placeholders.
_TOAST_SHOWN_JS = """((fragments, type) => {
  const toasts = window.__e2eToasts;
  const summary = (toasts ?? []).map((toast) => `${toast.type}: ${toast.text.slice(0, 140)}`);
  for (const toast of toasts ?? []) {
    if (type && toast.type !== type) continue;
    let at = 0;
    const values = [];
    for (let i = 0; i < fragments.length; i += 1) {
      const found = toast.text.indexOf(fragments[i], at);
      if (found < 0) { at = -1; break; }
      if (i > 0) values.push(toast.text.slice(at, found).trim());
      at = found + fragments[i].length;
    }
    if (at >= 0) return { ready: true, toast, values, seen: summary };
  }
  return { ready: false, recorderInstalled: toasts !== undefined, seen: summary };
})"""

# The key the confirm dialog's "don't remind me" box writes to the browser's storage (DONT_REMIND_COMPACT_STORAGE_KEY).
_REMINDER_KEY = "dontRemindCompact"

_DIALOG_JS = """((title) => {
  const dialog = document.querySelector('[role="alertdialog"]');
  const text = dialog ? dialog.innerText : '';
  return { ready: text.includes(title) && !!dialog.querySelector('#dontRemindCompact'), text: text.slice(0, 300) };
})"""

# The box and the button are pressed one poll apart, as two clicks of a user are: the confirm handler reads the box's
# state from the render that follows its click.
_TICK_DONT_REMIND_JS = """(() => {
  const box = document.querySelector('[role="alertdialog"] #dontRemindCompact');
  if (box && !box.checked) box.click();
  return { ready: !!box && box.checked };
})()"""

_CONFIRM_DIALOG_JS = """((label) => {
  const dialog = document.querySelector('[role="alertdialog"]');
  const button = dialog ? Array.from(dialog.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === label) : null;
  if (button) button.click();
  return { ready: !!button };
})"""

# The palette highlights one command; Enter runs it, so this is what a user is about to run.
_PALETTE_CHOICE_JS = """((needle) => {
  const palette = document.querySelector('[data-testid="slash-command-palette"]');
  const items = palette ? Array.from(palette.querySelectorAll('[cmdk-item]')) : [];
  const chosen = items.find((el) => el.classList.contains('bg-accent'));
  const chosenText = (chosen?.textContent ?? '').trim();
  return {
    ready: chosenText.includes(needle),
    chosen: chosenText.slice(0, 80),
    entries: items.slice(0, 6).map((el) => (el.textContent || '').slice(0, 60)),
    hasPalette: !!palette,
  };
})"""


def _evaluated(client: ChromeMcpClient, page: McpPage, script: str) -> dict[str, object]:
    state = client.evaluate(page, script, timeout_sec=15.0)
    assert isinstance(state, dict), state
    return state


def record_toasts(client: ChromeMcpClient, page: McpPage) -> None:
    installed = _evaluated(client, page, _RECORD_TOASTS_JS)
    assert installed.get("ok") is True, f"toast recorder not installed: {installed!r}"


def wait_for_toast(
    client: ChromeMcpClient,
    page: McpPage,
    page_url: str,
    fragments: list[str],
    *,
    kind: str | None = None,
    timeout_sec: float = 90.0,
) -> tuple[dict[str, str], list[str]]:
    """Wait for a toast that carries ``fragments`` in order (of the given ``kind``, if any); returns it and the values between them."""
    script = f"({_TOAST_SHOWN_JS})({json.dumps(fragments)}, {json.dumps(kind)})"
    shown = wait_for_state(client, page, script, timeout_sec=timeout_sec, page_url=page_url)
    toast, values = shown.get("toast"), shown.get("values")
    assert isinstance(toast, dict) and isinstance(values, list), shown
    return {key: str(text) for key, text in toast.items()}, [str(value) for value in values]


def read_toasts(client: ChromeMcpClient, page: McpPage) -> list[str]:
    """Every toast shown so far as ``type: text``, for a failure message or a check that one did not show."""
    seen = _evaluated(client, page, "(() => ({ toasts: (window.__e2eToasts ?? []).map((t) => `${t.type}: ${t.text}`) }))()")
    toasts = seen.get("toasts")
    assert isinstance(toasts, list), seen
    return [str(toast) for toast in toasts]


def type_in_composer(client: ChromeMcpClient, page: McpPage, page_url: str, text: str) -> None:
    """Put ``text`` in the composer once React owns it, as keystrokes would."""
    wait_for_state(client, page, COMPOSER_READY_JS, timeout_sec=60.0, page_url=page_url)
    typed = client.evaluate(page, f"({TYPE_JS})({json.dumps(text)})", timeout_sec=15.0)
    assert isinstance(typed, dict) and typed.get("ok") is True and typed.get("value") == text, f"typing failed: {typed!r}"


def wait_for_palette_choice(client: ChromeMcpClient, page: McpPage, page_url: str, command: str) -> dict[str, object]:
    """Wait until the slash palette has ``command`` highlighted (``/compact`` for the compaction command)."""
    return wait_for_state(client, page, f"({_PALETTE_CHOICE_JS})({json.dumps(command)})", timeout_sec=30.0, page_url=page_url)


def press_enter(client: ChromeMcpClient, page: McpPage) -> None:
    client.press_key(page, "Enter")


def wait_for_composer(client: ChromeMcpClient, page: McpPage, page_url: str, text: str) -> None:
    """Wait until the composer holds exactly ``text``: a command that ran empties it, one that failed keeps what was typed."""
    script = f"""(() => {{
  const value = document.querySelector('[data-chat-input]')?.value ?? null;
  return {{ ready: value === {json.dumps(text)}, value }};
}})()"""
    wait_for_state(client, page, script, timeout_sec=20.0, page_url=page_url)


def forget_compact_reminder(client: ChromeMcpClient, page: McpPage) -> None:
    """Make the browser ask again before a typed compaction: the choice lives in storage that outlasts the test."""
    _evaluated(client, page, f"(() => {{ localStorage.removeItem({json.dumps(_REMINDER_KEY)}); return {{ ok: true }}; }})()")


def read_compact_reminder(client: ChromeMcpClient, page: McpPage) -> str | None:
    value = _evaluated(client, page, f"(() => ({{ value: localStorage.getItem({json.dumps(_REMINDER_KEY)}) }}))()").get("value")
    return value if isinstance(value, str) else None


def wait_for_compact_dialog(client: ChromeMcpClient, page: McpPage, page_url: str, title: str) -> None:
    wait_for_state(client, page, f"({_DIALOG_JS})({json.dumps(title)})", timeout_sec=30.0, page_url=page_url)


def compact_dialog_is_open(client: ChromeMcpClient, page: McpPage) -> bool:
    return (
        _evaluated(client, page, "(() => ({ open: !!document.querySelector('[role=\"alertdialog\"]') }))()").get("open") is True
    )


def tick_dont_remind(client: ChromeMcpClient, page: McpPage, page_url: str) -> None:
    wait_for_state(client, page, _TICK_DONT_REMIND_JS, timeout_sec=15.0, page_url=page_url)


def confirm_compact_dialog(client: ChromeMcpClient, page: McpPage, page_url: str, label: str) -> None:
    wait_for_state(client, page, f"({_CONFIRM_DIALOG_JS})({json.dumps(label)})", timeout_sec=15.0, page_url=page_url)


def _strategy_label(locale: str, key: str) -> str:
    return locale_text(locale, "chat", "contextUsage", "strategy", key)


def _button_js(label: str) -> str:
    """JS expression for the button of the open strategy panel that reads ``label`` (``null`` while the panel is closed)."""
    return f"""(() => {{
    const panel = document.querySelector('[data-testid="context-usage-panel"]');
    return panel
      ? Array.from(panel.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === {json.dumps(label)}) ?? null
      : null;
  }})()"""


def panel_ready_js(locale: str) -> str:
    """Open the strategy panel behind the ring; ready once the compress button of this language is there and enabled."""
    return f"""(() => {{
  const ring = document.querySelector('[data-testid="context-usage-indicator"]');
  const panel = document.querySelector('[data-testid="context-usage-panel"]');
  if (ring && !panel) ring.click();
  const button = {_button_js(_strategy_label(locale, "compressContext"))};
  return {{
    ready: !!button && !button.disabled && document.documentElement.lang.startsWith({json.dumps(locale)}),
    hasRing: !!ring,
    hasPanel: !!panel,
    lang: document.documentElement.lang,
    buttons: panel ? Array.from(panel.querySelectorAll('button')).map((b) => (b.textContent || '').trim()) : [],
  }};
}})()"""


def press_compress_js(locale: str) -> str:
    """Press compress once; the flag keeps a re-poll from pressing it again while the model is working."""
    return f"""(() => {{
  const button = {_button_js(_strategy_label(locale, "compressContext"))};
  if (button && !button.disabled && !window.__e2eCompressPressed) {{
    window.__e2eCompressPressed = true;
    button.click();
  }}
  return {{ ready: window.__e2eCompressPressed === true }};
}})()"""


def rearm_compress_js() -> str:
    """Let ``press_compress_js`` press again, for a page that is kept across several presses."""
    return "(() => { delete window.__e2eCompressPressed; return { ready: true }; })()"


def compressing_js(locale: str) -> str:
    """Ready while the panel's button says a compression is under way and refuses another press."""
    return f"""(() => {{
  const button = {_button_js(_strategy_label(locale, "compressing"))};
  return {{ ready: !!button && button.disabled }};
}})()"""


def outcome_js(*, head: str, tail: str) -> str:
    """Read the line under the compress button: ready once ``head`` is shown; ``shown`` is what sits up to ``tail``."""
    return f"""(() => {{
  const panel = document.querySelector('[data-testid="context-usage-panel"]');
  const text = panel ? panel.innerText : '';
  const head = {json.dumps(head)};
  const tail = {json.dumps(tail)};
  const start = text.indexOf(head);
  let shown = null;
  if (start >= 0) {{
    const from = start + head.length;
    const end = tail ? text.indexOf(tail, from) : from;
    if (end >= from) shown = text.slice(from, end).trim();
  }}
  return {{ ready: shown !== null, shown, panelText: text.slice(-400), requests: window.__e2eCompactRequests ?? null }};
}})()"""


def tokens_from_shown(shown: str) -> float:
    """Tokens a panel line states (``12.6K`` reads as 12600; the panel rounds to a tenth of a thousand)."""
    multiplier = {"K": 1_000, "M": 1_000_000}.get(shown[-1], 1)
    return float(shown.rstrip("KM")) * multiplier


def count_in(text: str) -> int:
    """The whole number a toast states in place of a placeholder (any digit grouping is ignored)."""
    digits = re.sub(r"\D", "", text)
    assert digits, f"no number in {text!r}"
    return int(digits)
