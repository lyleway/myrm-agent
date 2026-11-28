"""Chrome E2E: speaking into the composer's mic button becomes text in the composer, with voice set up on the settings page first.

Real business flow through the WebUI. The one stand-in is the microphone: nobody speaks at the machine, so the page's
``getUserMedia`` hands the recorder a stream that plays real speech (a system voice reading a sentence). Everything behind the
page is the product's: the dictation WebSocket, the voice settings route, the local faster-whisper model and the database. The
backend is private and fresh, so a user who sets up voice before any model provider is the user under test:

    turn on Voice Interaction (the experimental feature that shows the mic button)
    -> open Settings > Channels > Voice, switch speech-to-text on and pick Local Whisper: the page saves the settings itself
    -> open the chat page, press the mic button and let the microphone speak
    -> the sentence is in the composer, and the server ended the dictation socket with a clean close.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_dictation_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    ensure_chat_route,
    get_e2e_api_url,
    get_e2e_ui_url,
    grant_user_activation,
    http_json,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.spoken_audio import SPOKEN_WORDS, synthesize_speech, whisper_base_model_cached

_VOICE_ROUTE = "/settings/channels?sub=voice"
_CHAT_PATH = "/"
_UPLOAD_CHUNK_CHARS = 32_000
_PAGE_TIMEOUT_MS = 120_000
# Whisper loads on first use and the host may be loaded: the transcript gets this long to reach the composer.
_DICTATION_TIMEOUT_SEC = 300.0
_CLEAN_CLOSE = 1000

_VOICE_PANEL_JS = """(() => {
  const panel = document.querySelector('[data-testid="voice-settings-panel"]');
  if (!panel) {
    const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
    const voiceTab = tabs.find((el) => /语音|Voice/i.test((el.textContent || '').trim()));
    if (voiceTab && voiceTab.getAttribute('aria-selected') !== 'true') voiceTab.click();
    return { ready: false, hasPanel: false, tabCount: tabs.length };
  }
  const toggle = document.getElementById('voice-stt-enabled');
  return { ready: Boolean(toggle), hasPanel: true, sttEnabled: toggle ? toggle.getAttribute('aria-checked') === 'true' : null };
})()"""

_SWITCH_STT_ON_JS = """(() => {
  const toggle = document.getElementById('voice-stt-enabled');
  if (!toggle) return { ok: false, err: 'stt-switch-missing' };
  if (toggle.getAttribute('aria-checked') !== 'true') toggle.click();
  return { ok: true };
})()"""

# The provider select only exists once speech-to-text is on. Radix closes an open select when its window blurs or resizes
# (the shared E2E Chrome moves focus between tabs), so one poll opens the list and picks from it without yielding for long,
# and the next poll opens it again if it closed in between.
_PICK_LOCAL_PROVIDER_JS = """(async () => {
  const trigger = document.getElementById('voice-stt-provider');
  if (!trigger) return { ready: false, why: 'provider select not rendered yet' };
  const findLocal = () => Array.from(document.querySelectorAll('[role="listbox"] [role="option"]'))
    .find((el) => /Local Whisper/.test(el.textContent || ''));
  let option = findLocal();
  if (!option && trigger.getAttribute('aria-expanded') !== 'true') {
    trigger.click();
    for (let waited = 0; waited < 1000 && !option; waited += 50) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      option = findLocal();
    }
  }
  if (!option) {
    return {
      ready: false,
      why: 'Local Whisper option not rendered yet',
      expanded: trigger.getAttribute('aria-expanded'),
      listboxes: document.querySelectorAll('[role="listbox"]').length,
      options: Array.from(document.querySelectorAll('[role="option"]')).map((el) => (el.textContent || '').trim()),
      focused: document.hasFocus(),
      visibility: document.visibilityState,
    };
  }
  const opts = { bubbles: true, cancelable: true, view: window, pointerId: 1 };
  option.dispatchEvent(new PointerEvent('pointerdown', opts));
  option.dispatchEvent(new PointerEvent('pointerup', opts));
  option.dispatchEvent(new MouseEvent('mouseup', opts));
  option.click();
  return { ready: true };
})()"""

# The mic button's aria-label names its state: idle, recording, transcribing (English and Chinese catalogs).
_MIC_BUTTON_FINDER = (
    "Array.from(document.querySelectorAll('button')).find("
    "(b) => /^(Voice input|Stop recording|Transcribing|语音输入|停止录音|正在转录)/.test((b.getAttribute('aria-label') || '').trim()))"
)

_COMPOSER_JS = f"""(() => {{
  const input = document.querySelector('textarea[data-chat-input]');
  const mic = {_MIC_BUTTON_FINDER};
  return {{ ready: Boolean(input) && Boolean(mic), hasInput: Boolean(input), hasMic: Boolean(mic) }};
}})()"""

_START_UPLOAD_JS = "(() => { window.__dictationWav = []; return { ok: true }; })()"

# The recorder gets a stream that is silent for the first second (the page calibrates its silence threshold on it) and then
# plays the speech. The socket spy records how the server ends the dictation, which decides whether the page keeps streaming.
_INSTALL_MICROPHONE_JS = """(async () => {
  const wavBase64 = (window.__dictationWav || []).join('');
  if (!wavBase64) return { ok: false, err: 'no-audio-uploaded' };
  const binary = atob(wavBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);

  const context = new AudioContext();
  await Promise.race([context.resume(), new Promise((resolve) => setTimeout(resolve, 3000))]);
  const speech = await context.decodeAudioData(bytes.buffer);
  const destination = context.createMediaStreamDestination();
  const probe = { contextState: context.state, durationSec: speech.duration, started: false, ended: false, endedAt: 0, stopPressed: false };
  window.__dictation = probe;

  navigator.mediaDevices.getUserMedia = async () => {
    const source = context.createBufferSource();
    source.buffer = speech;
    source.connect(destination);
    source.onended = () => { probe.ended = true; probe.endedAt = Date.now(); };
    source.start(context.currentTime + 1.0);
    probe.started = true;
    probe.contextState = context.state;
    return destination.stream;
  };

  const NativeWebSocket = window.WebSocket;
  window.__dictationSockets = [];
  window.WebSocket = class extends NativeWebSocket {
    constructor(url, protocols) {
      super(url, protocols);
      if (!String(url).includes('/ws/stt/stream')) return;
      const record = { messages: [], closeCode: null };
      window.__dictationSockets.push(record);
      this.addEventListener('message', (event) => {
        if (typeof event.data !== 'string') return;
        try { record.messages.push(JSON.parse(event.data)); } catch (_) { record.messages.push({ type: 'unparsed' }); }
      });
      this.addEventListener('close', (event) => { record.closeCode = event.code; });
    }
  };
  return { ok: true, contextState: context.state, durationSec: speech.duration };
})()"""

_PRESS_MIC_JS = f"""(() => {{
  const mic = {_MIC_BUTTON_FINDER};
  if (!mic || !/^(Voice input|语音输入)/.test((mic.getAttribute('aria-label') || '').trim())) return {{ ok: false, err: 'idle-mic-button-missing' }};
  mic.click();
  return {{ ok: true }};
}})()"""

# Placeholders, not an f-string: the script is mostly braces.
_DICTATION_PROGRESS_JS = """(() => {
  const probe = window.__dictation;
  // A reload (the dev server hot-reloads) wipes the microphone this test installed: report it instead of waiting it out.
  if (!probe) return { ready: true, err: 'the page lost the installed microphone, most likely by reloading' };
  const input = document.querySelector('textarea[data-chat-input]');
  const mic = __MIC_BUTTON_FINDER__;
  const label = mic ? (mic.getAttribute('aria-label') || '').trim() : null;
  // The page stops by itself after two seconds of silence; the press only covers a page that stopped checking for silence.
  if (/^(Stop recording|停止录音)/.test(label || '') && probe.ended && Date.now() - probe.endedAt > 5000 && !probe.stopPressed) {
    probe.stopPressed = true;
    mic.click();
  }
  const text = input ? input.value : null;
  const heard = (text || '').toLowerCase();
  const sockets = window.__dictationSockets || [];
  const wordsIn = __WORDS__.every((word) => heard.includes(word));
  const socketsClosed = sockets.length > 0 && sockets.every((s) => s.closeCode !== null);
  if (socketsClosed && !probe.closedAt) probe.closedAt = Date.now();
  // A dictation the server ended badly, or ended cleanly without the words reaching the composer, will not produce them
  // later: report it now instead of after the whole wait.
  const endedBadly = sockets.some((s) => s.closeCode !== null && s.closeCode !== __CLEAN_CLOSE__);
  const gaveUp = socketsClosed && !wordsIn && Date.now() - probe.closedAt > 5000;
  return {
    ready: endedBadly || gaveUp || (wordsIn && (!__REQUIRE_CLOSED__ || socketsClosed)),
    text, label, sockets, bodyLength: (document.body?.innerText || '').length,
    microphone: { contextState: probe.contextState, started: probe.started, ended: probe.ended, stopPressed: probe.stopPressed },
  };
})()"""


def _dictation_progress_js(*, require_closed: bool) -> str:
    """The composer text, the mic button and the dictation sockets; ready once the spoken words are in the composer."""
    return (
        _DICTATION_PROGRESS_JS.replace("__MIC_BUTTON_FINDER__", _MIC_BUTTON_FINDER)
        .replace("__WORDS__", json.dumps(list(SPOKEN_WORDS)))
        .replace("__CLEAN_CLOSE__", str(_CLEAN_CLOSE))
        .replace("__REQUIRE_CLOSED__", "true" if require_closed else "false")
    )


def _wait_for_dictation(client: ChromeMcpClient, page: McpPage, *, require_closed: bool, timeout_sec: float) -> dict[str, object]:
    """Wait for the dictation to land, or for the page to report that it cannot.

    No ``page_url``: the wait must never reload the page, which would wipe the microphone this test installed.
    """
    state = wait_for_state(client, page, _dictation_progress_js(require_closed=require_closed), timeout_sec=timeout_sec)
    assert "err" not in state, state
    return state


def _wait_for(condition: Callable[[], bool], failure: str, *, timeout_sec: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1.0)
    pytest.fail(failure)


def _saved_voice_settings(api_url: str) -> dict[str, object]:
    stored = http_json("GET", f"{api_url}/api/v1/config/voice")
    assert isinstance(stored, dict), stored
    value = stored.get("value")
    return value if isinstance(value, dict) else {}


def _save_voice_settings_on_the_page(client: ChromeMcpClient, page: McpPage, api_url: str, voice_url: str) -> None:
    """Switch speech-to-text on and pick Local Whisper as a user does; the page saves its settings by itself."""
    wait_for_state(client, page, _VOICE_PANEL_JS, timeout_sec=90.0, page_url=voice_url)
    switched = client.evaluate(page, _SWITCH_STT_ON_JS, timeout_sec=15.0)
    assert isinstance(switched, dict) and switched.get("ok") is True, switched
    wait_for_state(client, page, _PICK_LOCAL_PROVIDER_JS, timeout_sec=60.0)
    _wait_for(
        lambda: _saved_voice_settings(api_url).get("sttProvider") == "local",
        "the voice settings page never saved Local Whisper as the speech-to-text provider",
    )
    saved = _saved_voice_settings(api_url)
    assert saved.get("sttEnabled") is True, saved


def _upload_speech(client: ChromeMcpClient, page: McpPage, wav: bytes) -> None:
    """Hand the page the speech in chunks small enough for one evaluate call each."""
    encoded = base64.b64encode(wav).decode("ascii")
    started = client.evaluate(page, _START_UPLOAD_JS, timeout_sec=15.0)
    assert isinstance(started, dict) and started.get("ok") is True, started
    for start in range(0, len(encoded), _UPLOAD_CHUNK_CHARS):
        chunk = json.dumps(encoded[start : start + _UPLOAD_CHUNK_CHARS])
        pushed = client.evaluate(
            page, f"(() => {{ window.__dictationWav.push({chunk}); return {{ ok: true }}; }})()", timeout_sec=30.0
        )
        assert isinstance(pushed, dict) and pushed.get("ok") is True, pushed


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dictation_turns_speech_into_composer_text_after_voice_is_set_up_in_settings(tmp_path: Path) -> None:
    wav = synthesize_speech(tmp_path / "speech.wav")
    if wav is None:
        pytest.skip("no system speech synthesizer (`say`) to produce real speech")
    if not whisper_base_model_cached():
        pytest.skip("the faster-whisper `base` model is not cached; the test does not download it")

    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url().rstrip("/")
    prepare_e2e_ui_session(api_url)
    http_json("POST", f"{api_url}/api/v1/features/voice_interaction/toggle", {"enabled": True})
    voice_url = f"{ui_url}{_VOICE_ROUTE}"
    chat_url = f"{ui_url}{_CHAT_PATH}"

    warm_ui_route(_CHAT_PATH)
    with open_settings_subroute(_VOICE_ROUTE, timeout_ms=_PAGE_TIMEOUT_MS) as (client, page):
        _save_voice_settings_on_the_page(client, page, api_url, voice_url)

        ensure_chat_route(client, page, target_url=chat_url, timeout_ms=_PAGE_TIMEOUT_MS)
        wait_for_state(client, page, _COMPOSER_JS, timeout_sec=90.0, page_url=chat_url)
        _upload_speech(client, page, wav)
        # A person at the machine has clicked by now; the orchestrator's evaluate has not, and without a gesture Chrome keeps
        # every audio context of the page suspended: the speech would never play and the page could not hear it.
        grant_user_activation(page)
        installed = client.evaluate(page, _INSTALL_MICROPHONE_JS, timeout_sec=60.0)
        assert isinstance(installed, dict) and installed.get("ok") is True, installed
        assert installed.get("contextState") == "running", (
            f"the page's audio engine is not running, so the speech would not play: {installed}"
        )

        pressed = client.evaluate(page, _PRESS_MIC_JS, timeout_sec=15.0)
        assert isinstance(pressed, dict) and pressed.get("ok") is True, pressed
        _wait_for_dictation(client, page, require_closed=False, timeout_sec=_DICTATION_TIMEOUT_SEC)
        done = _wait_for_dictation(client, page, require_closed=True, timeout_sec=60.0)

    heard = str(done["text"]).lower()
    assert all(word in heard for word in SPOKEN_WORDS), f"the spoken words never reached the composer: {done}"
    sockets = done["sockets"]
    assert isinstance(sockets, list) and len(sockets) == 1, f"one dictation opens one socket: {done}"
    assert sockets[0]["closeCode"] == _CLEAN_CLOSE, (
        f"the server must end a dictation with a clean close, or the page downgrades its next one: {done}"
    )
    final = [m for m in sockets[0]["messages"] if m.get("type") == "transcript" and m.get("is_final") is True]
    assert len(final) == 1 and final[0]["text"] == done["text"], (
        f"the composer must hold the one final transcript the server sent: {done}"
    )
