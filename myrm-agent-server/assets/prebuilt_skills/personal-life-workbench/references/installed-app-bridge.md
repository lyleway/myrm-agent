# Installed-App Bridge (`window.myrm`)

Read this before writing a page that uses `window.myrm`. The contract summary is in `SKILL.md` §4.

A page shown in chat or on the dashboard has no network (`fetch()` and `XMLHttpRequest` are blocked) and no `window.myrm`, so it stays an offline workbench. `window.myrm` appears only after the user installs the artifact as an app, and each capability works only if the user approved it. The page runs only while the app is open: promise no reminders while it is closed.

## Declaration

Declare what the page needs in one block (omit what is unused):

```html
<script type="application/json" id="myrm-app">{"notify": true, "fetch": ["api.open-meteo.com"], "ai": true, "tray": true, "shortcut": ["CommandOrControl+Shift+K"], "float": true}</script>
```

- `fetch` takes exact public host names: lower-case, at most 20, no wildcards, `localhost`, IP addresses or ports. List every host the page calls; any other host is refused. A malformed block declares nothing.
- `ai` lets the page ask the AI model the user configured through `myrm.llm.complete`; it takes no scope. Declare it only for a feature that needs a model answer, because every call spends the user's model quota.
- `tray`, `shortcut` and `float` work only in the app's own desktop window (the Myrm desktop app, opened from the app list or a launcher). In a browser tab or inside a chat they are refused with `unsupported`, so declare them only for pages that still make sense without them. `shortcut` lists at most 4 global key combinations, each with a modifier other than Shift plus a letter, digit or F1-F12 (`CommandOrControl`, `Control`, `Alt`, `Shift`); anything else makes the whole list void.
- Adding a capability, host or key combination in a later edit makes the app ask the user again.

## Calls (all return promises)

- `myrm.notify({ title, body })`: title required (max 80 chars), body optional (max 200). Max 5 per minute.
- `myrm.fetch(url, { method, headers, body })`: like `fetch`, resolves to a `Response`. `https` only, `GET`/`HEAD`/`POST`, text bodies, request max 256 KB, response max 1 MB, 20 s timeout, about 1 call per second. Cookies are never sent and redirects are not followed, so call the final URL. Use keyless public APIs: page source is not secret.
- `myrm.llm.complete({ prompt, system, maxTokens })` (or just a prompt string): resolves to the answer text. `prompt` is required (max 16000 chars), `system` optional (max 4000), `maxTokens` optional (default 1024, max 4096). It is a single completion on the user's model with no tools and no memory of the conversation, so put everything the answer needs in the prompt. Call it only from a user action, never on load or in a loop: it costs money, takes seconds (60 s limit), allows about 20 calls per minute, and each app has a small daily budget on top of the user's own. The answer is untrusted text: show it with `textContent`, never `innerHTML`.
- `myrm.tray.set({ title, tooltip })`: puts one item in the menu bar or system tray, named after the app; `title` (max 24 chars, shown beside the icon on macOS) and `tooltip` (max 80), at least one of them. `myrm.tray.clear()` removes it. While the item exists, closing the window only hides it; the user quits from the item's menu. Max 30 calls per minute.
- `myrm.shortcut.register(accelerator)` / `myrm.shortcut.unregister(accelerator)`: only a combination listed in `shortcut`. When the user presses it, the page receives `myrm.on('shortcut', ({ accelerator }) => ...)`. A combination another app or the system holds fails with `shortcut_conflict`; never assume it registered.
- `myrm.float(on)`: keeps the window above others (`true`) or stops doing so.
- `myrm.on('permission-revoked', ({ capability }) => ...)`: the user took a permission back; stop using it and update the page. Tray items and key combinations are already gone.

## Failing well

Check `window.myrm` before use and catch every call. `error.code` is `permission_denied` (declined or not yet decided), `not_declared`, `host_not_granted`, `shortcut_not_granted`, `shortcut_conflict`, `unsupported`, `rate_limited`, `budget_exceeded` (the user's or the app's daily budget is used up), `unavailable` (no model, or one without a known price), `blocked` (the user's risk rules hold the text, for example one that contains a key) or `timeout`; treat any other code as a failed request. On failure keep showing the last result (save each success with `saveState`) plus a short inline note. Retry only on a user action or the page's own timer, never in a loop.
