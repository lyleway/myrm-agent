"""The page-side scripts of the delivery-health Chrome E2E: open the job, read the pill, check its copy, measure fit and contrast.

[INPUT]
- none (plain JavaScript source strings evaluated in the page)

[OUTPUT]
- HOST_PRELUDE / FRAME_PRELUDE: bind ``win`` and ``doc`` to the test page, or to the app in a fixed-size same-origin frame
- PILL: one poll step that opens the job from its card in the list and reports the pill, the page language and, while the
  pill is missing, the end of the page text
- OPEN_POPOVER / POPOVER_TEXT / AGE: scroll the pill into view and open its popover, check the copy in it, check the age the
  pill shows
- FRAME_PILL / FRAME_MEASURE: create the phone-width frame and wait for the pill in it; measure fit and text contrast in a theme
- probe: fill a script's ``__NAME__`` placeholders and wrap it into an expression

[POS]
Used by tests/e2e/test_cron_delivery_health_chrome_e2e.py; split out so that file stays the scenario.
"""

from __future__ import annotations

import json
import re

# A probe runs against the page it is handed (``doc`` and ``win``): the test page itself, or the app in a fixed-size
# same-origin frame, because ``window.resizeTo`` is a no-op in this Chrome and the layout switches to the phone shell
# below 1024px, so only a frame pins the viewport under test. A document that is still loading has no body, and a script
# that dereferences it ends `wait_for_state` as a failed transport instead of letting it poll, so the page text is
# always read with `?.`.
HOST_PRELUDE = "const win = window; const doc = document;"
FRAME_PRELUDE = """
  const frame = document.getElementById('e2e-app-frame');
  const win = frame ? frame.contentWindow : null;
  const doc = win ? win.document : null;
"""

# The user opens the job from its card in the list. Once the job is open the card is gone, so this clicks at most once per
# list; a reload lands on the list again and is followed by the same click.
PILL = """
  const status = doc.querySelector('[data-testid="connector-health-badge"]');
  const age = doc.querySelector('[data-testid="connector-health-age"]');
  const card = status
    ? null
    : Array.from(doc.querySelectorAll('[role="button"]')).find((el) => (el.textContent || '').includes(__NAME__));
  if (card && Date.now() - Number(card.dataset.e2eOpenedAt || 0) > 15000) {
    card.dataset.e2eOpenedAt = String(Date.now());
    card.click();
  }
  const bodyText = doc.body?.innerText || '';
  return {
    ready: status !== null && age !== null,
    status: status ? status.textContent.trim() : null,
    lang: doc.documentElement?.lang,
    tail: bodyText.slice(-300),
    // Read by `wait_for_state` to tell a blank or errored page, which it reloads, from one that is still loading.
    bodyLength: bodyText.length,
    errorOverlay: !!doc.querySelector('nextjs-portal')?.shadowRoot?.querySelector('[data-nextjs-dialog]'),
  };
"""

OPEN_POPOVER = """
  const pill = doc.querySelector('[data-testid="connector-health-badge"]')?.closest('button');
  if (!pill) {
    return { ready: false, reason: 'the pill of a failing destination is a button' };
  }
  if (pill.getAttribute('aria-expanded') !== 'true') {
    // A user taps what is in view; on a phone the delivery section sits far below the first screen. `instant` because
    // a smooth scroll would still be moving when the popover is measured.
    pill.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
    pill.click();
  }
  return { ready: doc.querySelector('[data-radix-popper-content-wrapper]') !== null };
"""

# The pill's age is `formatRelativeTime`'s phrase for the attempt's age, so every phrase for an age up to the real one
# (plus slack for the time the page took to render) is one it may show; the buckets are the ones that function uses.
AGE = r"""
  const spec = __SPEC__;
  const node = doc.querySelector('[data-testid="connector-health-age"]');
  if (!node) {
    return { ready: false };
  }
  const rtf = new Intl.RelativeTimeFormat(spec.locale, { numeric: 'auto' });
  const phrase = (seconds) => {
    const diff = -seconds;
    if (seconds < 60) return rtf.format(diff, 'second');
    if (seconds < 3600) return rtf.format(Math.round(diff / 60), 'minute');
    if (seconds < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
    return rtf.format(Math.round(diff / 86400), 'day');
  };
  const oldest = Math.max(0, Math.ceil((Date.now() - Date.parse(spec.attemptAt)) / 1000)) + 2;
  const allowed = new Set(Array.from({ length: oldest + 1 }, (_, seconds) => phrase(seconds)));
  const shown = (node.textContent || '').replace(/^\s*·\s*/, '').trim();
  return { ready: true, shown, allowedAge: allowed.has(shown), oldestSeconds: oldest };
"""

# The popover is portaled to the end of the body, so its text is in `innerText` of the page.
POPOVER_TEXT = """
  const spec = __SPEC__;
  const time = new Intl.DateTimeFormat(spec.locale, { dateStyle: 'medium', timeStyle: 'short' });
  const lastFailure = spec.lastFailure.replace('{time}', time.format(new Date(spec.attemptAt)));
  const text = doc.body?.innerText || '';
  const missing = [spec.headline, spec.hint, spec.streak, lastFailure, spec.basis].filter((part) => !text.includes(part));
  return { ready: missing.length === 0, missing, text: text.slice(-600) };
"""

FRAME_PILL = """
  if (!frame) {
    const created = document.createElement('iframe');
    created.id = 'e2e-app-frame';
    created.style.cssText = 'position:fixed;left:0;top:0;width:__WIDTH__px;height:__HEIGHT__px;border:0;z-index:2147483647;background:#fff';
    created.src = location.origin + '/settings/cron?job=' + encodeURIComponent(__JOB__);
    document.body.appendChild(created);
    return { ready: false, stage: 'loading' };
  }
  const pill = doc.querySelector('[data-testid="connector-health-badge"]')?.closest('button');
  return {
    ready: !!pill && Object.keys(pill).some((key) => key.startsWith('__reactProps')),
    viewportWidth: win.innerWidth,
    path: win.location.pathname + win.location.search,
    errorOverlay: !!doc.querySelector('nextjs-portal')?.shadowRoot?.querySelector('[data-nextjs-dialog]'),
    tail: (doc.body?.innerText || '').slice(-300),
  };
"""

# Fit and legibility as the user sees them. Text contrast is the WCAG ratio of the text color over the ancestors'
# backgrounds composited on a canvas, which resolves whatever color syntax the theme variables use.
FRAME_MEASURE = """
  const content = (doc.querySelector('[data-radix-popper-content-wrapper]') || {}).firstElementChild;
  const status = doc.querySelector('[data-testid="connector-health-badge"]');
  const age = doc.querySelector('[data-testid="connector-health-age"]');
  if (!content || !status || !age) {
    return { ready: false };
  }
  if (!doc.getElementById('e2e-no-transition')) {
    // Colors are transitioned, so a measurement right after the class swap would read the previous theme.
    const style = doc.createElement('style');
    style.id = 'e2e-no-transition';
    style.textContent = '*, *::before, *::after { transition: none !important; }';
    doc.head.appendChild(style);
  }
  doc.documentElement.classList.remove('light', 'dark');
  doc.documentElement.classList.add(__THEME__);
  const box = content.getBoundingClientRect();
  const placed = [box.left, box.top, box.width, box.height].map(Math.round).join(',');
  const settled = win.__e2ePlaced === placed;
  win.__e2ePlaced = placed;
  const ctx = doc.createElement('canvas').getContext('2d', { willReadFrequently: true });
  const paint = (css) => {
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
  };
  const pixel = () => Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3);
  const luminance = ([r, g, b]) => {
    const [lr, lg, lb] = [r, g, b].map((v) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb;
  };
  const contrast = (el) => {
    paint('#ffffff');
    const chain = [];
    for (let node = el; node; node = node.parentElement) chain.unshift(node);
    for (const node of chain) paint(win.getComputedStyle(node).backgroundColor);
    const background = pixel();
    paint('rgb(' + background.join(',') + ')');
    paint(win.getComputedStyle(el).color);
    const [hi, lo] = [luminance(background), luminance(pixel())].sort((a, b) => b - a);
    return { ratio: (hi + 0.05) / (lo + 0.05), background };
  };
  const popoverTexts = Array.from(content.querySelectorAll('p, span')).map((el, i) => [i + ':' + (el.textContent || '').slice(0, 24), el]);
  const pill = status.closest('button').getBoundingClientRect();
  const edges = (rect) => [rect.left, rect.top, rect.right, rect.bottom].map(Math.round);
  return {
    ready: settled,
    lang: doc.documentElement.lang,
    // The status word the pill shows, so the test knows which of the statuses' tones was measured.
    statusText: (status.textContent || '').trim(),
    viewportWidth: win.innerWidth,
    // Where each box sits (left, top, right, bottom), so a misfit reports how far it is off.
    viewportHeight: win.innerHeight,
    popoverBox: edges(box),
    pillBox: edges(pill),
    popoverInside: box.left >= 0 && box.top >= 0 && box.right <= win.innerWidth && box.bottom <= win.innerHeight,
    pillInside: pill.left >= 0 && pill.top >= 0 && pill.right <= win.innerWidth && pill.bottom <= win.innerHeight,
    pageOverflowsX: doc.documentElement.scrollWidth > doc.documentElement.clientWidth,
    popoverBackground: contrast(content).background,
    contrasts: Object.fromEntries([['status', status], ['age', age], ...popoverTexts].map(([name, el]) => [name, contrast(el).ratio])),
  };
"""


def probe(prelude: str, body: str, **values: object) -> str:
    """A probe over the page ``prelude`` binds, with its ``__NAME__`` placeholders filled by JSON values."""
    source = prelude + body
    for name, value in values.items():
        source = source.replace(f"__{name.upper()}__", json.dumps(value))
    assert not re.findall(r"__[A-Z]+__", source), f"unfilled probe placeholders in {body[:60]!r}"
    return f"(() => {{{source}}})()"
