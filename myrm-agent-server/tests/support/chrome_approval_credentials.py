"""The one-time credential form on a bash approval card, as the user fills it in the WebUI.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: CDP page helpers)

[OUTPUT]
- open_credential_form(): expand the card's "password or key for this command" section
- stage_credential(): type one variable name and value and press the card's stage button
- STAGE_REFUSED / STAGED_READY: the words the page shows when the server refused the name / holds the credential

[POS]
Server pytest-only helper for the approval-card Chrome E2E tests. Like the approval helpers, it presses the real buttons:
the staging request is the one the card builds.
"""

from __future__ import annotations

import json

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, wait_for_state

# A refused request (HTTP 400/422) is worded "check the name"; a request that failed for any other reason says "try
# again in a moment", so this text can only come from the server's verdict on the name.
STAGE_REFUSED = r"(Could not prepare it|准备失败).*(Check the variable name and value, then try again|请检查变量名和值后重试)"
# Once the server holds a credential, the section's heading counts it.
STAGED_READY = r"(Password or key ready \(1\)|密码或密钥已就绪（1）)"

_KEY_PLACEHOLDER = "API_SECRET_KEY"
_SECRET_PLACEHOLDER = "••••••••••••"

# The form stays mounted while the section is collapsed, so "open" is the section button's own state.
_OPEN_SECTION_JS = """(() => {
  const opener = Array.from(document.querySelectorAll('button[aria-expanded]')).find((b) =>
    /Add a password or key for this command|Password or key ready|为这条命令添加密码或密钥|密码或密钥已就绪/.test(
      (b.textContent || '').trim()
    )
  );
  if (!opener) return { ready: false, err: 'credential-section-not-found' };
  if (opener.getAttribute('aria-expanded') === 'true') return { ready: true };
  opener.click();
  return { ready: false, opened: true };
})()"""


def _set_input_js(placeholder: str, value: str) -> str:
    return f"""(() => {{
  const el = Array.from(document.querySelectorAll('input')).find((i) => i.placeholder === {json.dumps(placeholder)});
  if (!el) return {{ ok: false, err: 'input-not-found' }};
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
  setter.call(el, {json.dumps(value)});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  return {{ ok: true }};
}})()"""


# The button stays disabled until a name and a value are in, so a click that finds it disabled is not ready yet.
_CLICK_STAGE_JS = """(() => {
  const stage = Array.from(document.querySelectorAll('button')).find((b) =>
    /Use for this command|用于这条命令/.test((b.textContent || '').trim())
  );
  if (!stage || stage.disabled) return { ready: false, err: stage ? 'stage-disabled' : 'stage-not-found' };
  stage.click();
  return { ready: true };
})()"""


def open_credential_form(client: ChromeMcpClient, page: McpPage) -> None:
    """Expand the card's credential section (one that is already expanded stays so)."""
    wait_for_state(client, page, _OPEN_SECTION_JS, timeout_sec=30.0)


def stage_credential(client: ChromeMcpClient, page: McpPage, key: str, secret: str) -> None:
    """Type ``key`` and ``secret`` into the open form (replacing what is there) and press the stage button."""
    for placeholder, value in ((_KEY_PLACEHOLDER, key), (_SECRET_PLACEHOLDER, secret)):
        typed = client.evaluate(page, _set_input_js(placeholder, value), timeout_sec=15.0)
        assert isinstance(typed, dict) and typed.get("ok") is True, typed
    wait_for_state(client, page, _CLICK_STAGE_JS, timeout_sec=15.0)
