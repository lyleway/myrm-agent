"""Speech for the real WebUI without a person at the microphone: the stand-in microphone and the voice settings a user makes.

Dictation and the voice session are driven the way a user drives them: voice is set up on the settings page, then the chat page's
buttons are pressed. The one thing nobody can do at the machine is speak, so the page's ``getUserMedia`` hands the page a stream
that plays real speech (a system voice reading a sentence, see ``spoken_audio``). Everything behind the page is the product's: the
sockets, the settings route, the local faster-whisper model and the database.

Chrome keeps every audio context suspended until the page has had a user gesture, and the orchestrator's evaluate carries none, so
installing the microphone first grants the page one (``grant_user_activation``); without it the speech would never play.

Page globals the installed microphone leaves for the tests' progress probes:

- ``__microphone``: what the stand-in did (``microphoneRequests``, ``plays``, ``ended``, ``endedAt``, ``contextState``);
- ``__speakAgain(delaySec)``: play the speech once more into the stream of the latest microphone request (a second utterance
  of one voice session);
- ``__voiceSockets``: one record per dictation or voice session socket (``path``, ``messages`` received, ``sent`` text frames,
  ``binaryFrames`` sent, ``closeCode``); every sent text frame carries ``audioFramesBefore``, the audio frames sent since the
  text frame before it, which tells an ``end_utterance`` that closes speech from one that is only the heartbeat of a quiet session;
- ``__toastLog``: the text of every toast the page showed, in order;
- ``__uploadAnswers``: how the server answered each ``/stt/transcribe`` upload the page made (``status``, ``body``); the page
  says the same for every 400, so this is the only place the reason of a refusal shows.
"""

from __future__ import annotations

import base64
import io
import json
import math
import struct
import time
import wave
from collections.abc import Callable

import pytest

from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, grant_user_activation, http_json, wait_for_state

VOICE_SETTINGS_ROUTE = "/settings/channels?sub=voice"

_UPLOAD_CHUNK_CHARS = 32_000

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

# The voice settings are Radix selects. Radix closes an open select when its window blurs or resizes (the shared E2E Chrome
# moves focus between tabs), so one poll opens the list and picks from it without yielding for long, and the next poll opens
# it again if it closed in between. Placeholders, not an f-string: the script is mostly braces.
_PICK_OPTION_JS = """(async () => {
  const trigger = document.getElementById(__TRIGGER_ID__);
  if (!trigger) return { ready: false, why: 'select not rendered yet' };
  const findOption = () => Array.from(document.querySelectorAll('[role="listbox"] [role="option"]'))
    .find((el) => __OPTION_PATTERN__.test(el.textContent || ''));
  let option = findOption();
  if (!option && trigger.getAttribute('aria-expanded') !== 'true') {
    trigger.click();
    for (let waited = 0; waited < 1000 && !option; waited += 50) {
      await new Promise((resolve) => setTimeout(resolve, 50));
      option = findOption();
    }
  }
  if (!option) {
    return {
      ready: false,
      why: 'option not rendered yet',
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

# The dictation mic button's aria-label names its state: idle, recording, transcribing (English and Chinese catalogs).
MIC_BUTTON_FINDER = (
    "Array.from(document.querySelectorAll('button')).find("
    "(b) => /^(Voice input|Stop recording|Transcribing|语音输入|停止录音|正在转录)/.test((b.getAttribute('aria-label') || '').trim()))"
)

COMPOSER_READY_JS = f"""(() => {{
  const input = document.querySelector('textarea[data-chat-input]');
  const mic = {MIC_BUTTON_FINDER};
  return {{ ready: Boolean(input) && Boolean(mic), hasInput: Boolean(input), hasMic: Boolean(mic) }};
}})()"""

PRESS_MIC_JS = f"""(() => {{
  const mic = {MIC_BUTTON_FINDER};
  if (!mic || !/^(Voice input|语音输入)/.test((mic.getAttribute('aria-label') || '').trim())) return {{ ok: false, err: 'idle-mic-button-missing' }};
  mic.click();
  return {{ ok: true }};
}})()"""

# The stream is silent for the first second (the page calibrates its silence threshold on it) and then plays the speech, once
# per ``getUserMedia`` call. The socket spy records how the server answers and what the page sends, the fetch spy how the
# server answers each upload.
_INSTALL_MICROPHONE_JS = """(async () => {
  const wavBase64 = (window.__spokenWav || []).join('');
  if (!wavBase64) return { ok: false, err: 'no-audio-uploaded' };
  const binary = atob(wavBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);

  const context = new AudioContext();
  await Promise.race([context.resume(), new Promise((resolve) => setTimeout(resolve, 3000))]);
  const speech = await context.decodeAudioData(bytes.buffer);
  // A page stops the tracks of a stream it is done with, and a stopped track never plays again: every request gets a stream
  // of its own, as with a real microphone, and the speech is played into the latest one.
  let destination = null;
  const probe = {
    contextState: context.state, durationSec: speech.duration, microphoneRequests: 0, plays: 0, ended: false, endedAt: 0, stopPressed: false,
  };
  window.__microphone = probe;

  window.__speakAgain = (delaySec) => {
    const source = context.createBufferSource();
    source.buffer = speech;
    source.connect(destination);
    probe.ended = false;
    probe.stopPressed = false;
    source.onended = () => { probe.ended = true; probe.endedAt = Date.now(); };
    source.start(context.currentTime + delaySec);
    probe.plays += 1;
    probe.contextState = context.state;
  };
  navigator.mediaDevices.getUserMedia = async () => {
    probe.microphoneRequests += 1;
    destination = context.createMediaStreamDestination();
    window.__speakAgain(1.0);
    return destination.stream;
  };

  const NativeWebSocket = window.WebSocket;
  window.__voiceSockets = [];
  window.WebSocket = class extends NativeWebSocket {
    constructor(url, protocols) {
      super(url, protocols);
      // The page reaches the backend under /api/v1 (the API base carries the prefix), so a route is the end of the path.
      const pathname = new URL(String(url), location.href).pathname;
      const path = ['/ws/stt/stream', '/ws/voice/session'].find((route) => pathname.endsWith(route));
      if (!path) return;
      const record = { path, messages: [], sent: [], binaryFrames: 0, closeCode: null };
      window.__voiceSockets.push(record);
      this.addEventListener('message', (event) => {
        if (typeof event.data !== 'string') return;
        try { record.messages.push(JSON.parse(event.data)); } catch (_) { record.messages.push({ type: 'unparsed' }); }
      });
      this.addEventListener('close', (event) => { record.closeCode = event.code; });
      const nativeSend = this.send;
      let audioSinceLastText = 0;
      this.send = (data) => {
        if (typeof data === 'string') {
          let frame;
          try { frame = JSON.parse(data); } catch (_) { frame = { type: 'unparsed' }; }
          record.sent.push({ ...frame, audioFramesBefore: audioSinceLastText });
          audioSinceLastText = 0;
        } else {
          record.binaryFrames += 1;
          audioSinceLastText += 1;
        }
        return nativeSend.call(this, data);
      };
    }
  };

  // The copy of the response is taken before the page can read the body; the record exists the moment the answer arrives.
  const nativeFetch = window.fetch;
  window.__uploadAnswers = [];
  window.fetch = async (input, init) => {
    const response = await nativeFetch.call(window, input, init);
    if (String(input instanceof Request ? input.url : input).includes('/stt/transcribe')) {
      const answer = { status: response.status, body: '' };
      window.__uploadAnswers.push(answer);
      response.clone().text().then((text) => { answer.body = text; });
    }
    return response;
  };

  // A toast is gone after a few seconds: keep what every one of them said, so a slow poll cannot miss it.
  window.__toastLog = [];
  const loggedToasts = new Set();
  const logToasts = () => document.querySelectorAll('[data-sonner-toast]').forEach((el) => {
    const text = (el.textContent || '').trim();
    if (text && !loggedToasts.has(text)) { loggedToasts.add(text); window.__toastLog.push(text); }
  });
  new MutationObserver(logToasts).observe(document.body, { childList: true, subtree: true, characterData: true });
  logToasts();
  return { ok: true, contextState: context.state, durationSec: speech.duration };
})()"""

_UPLOAD_ANSWERS_JS = "(() => ({ answers: window.__uploadAnswers || null }))()"


def tone_wav(seconds: float = 3.0) -> bytes:
    """A mono 16-bit WAV of a loud sine tone: sound for the page's silence detection, no speech for a model to find in it."""
    rate = 16_000
    frames = b"".join(
        struct.pack("<h", int(16_000 * math.sin(2 * math.pi * 440.0 * i / rate))) for i in range(int(seconds * rate))
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(frames)
    return buffer.getvalue()


def upload_speech(client: ChromeMcpClient, page: McpPage, wav: bytes) -> None:
    """Hand the page the speech in chunks small enough for one evaluate call each."""
    encoded = base64.b64encode(wav).decode("ascii")
    started = client.evaluate(page, "(() => { window.__spokenWav = []; return { ok: true }; })()", timeout_sec=15.0)
    assert isinstance(started, dict) and started.get("ok") is True, started
    for start in range(0, len(encoded), _UPLOAD_CHUNK_CHARS):
        chunk = json.dumps(encoded[start : start + _UPLOAD_CHUNK_CHARS])
        pushed = client.evaluate(
            page, f"(() => {{ window.__spokenWav.push({chunk}); return {{ ok: true }}; }})()", timeout_sec=30.0
        )
        assert isinstance(pushed, dict) and pushed.get("ok") is True, pushed


def install_speaking_microphone(client: ChromeMcpClient, page: McpPage, wav: bytes) -> None:
    """Make the page's microphone play ``wav`` (once per ``getUserMedia`` call) and spy on its voice sockets."""
    upload_speech(client, page, wav)
    # A person at the machine has clicked by now; the orchestrator's evaluate has not, and without a gesture Chrome keeps
    # every audio context of the page suspended: the speech would never play and the page could not hear it.
    grant_user_activation(page)
    installed = client.evaluate(page, _INSTALL_MICROPHONE_JS, timeout_sec=60.0)
    assert isinstance(installed, dict) and installed.get("ok") is True, installed
    assert installed.get("contextState") == "running", (
        f"the page's audio engine is not running, so the speech would not play: {installed}"
    )


def upload_answers(client: ChromeMcpClient, page: McpPage) -> list[dict[str, object]]:
    """How the server answered every ``/stt/transcribe`` upload this page made, in order (``status`` and ``body``)."""
    result = client.evaluate(page, _UPLOAD_ANSWERS_JS, timeout_sec=15.0)
    answers = result.get("answers") if isinstance(result, dict) else None
    assert isinstance(answers, list), result
    return answers


def wait_until(condition: Callable[[], bool], failure: str, *, timeout_sec: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1.0)
    pytest.fail(failure)


def saved_voice_settings(api_url: str) -> dict[str, object]:
    stored = http_json("GET", f"{api_url}/api/v1/config/voice")
    assert isinstance(stored, dict), stored
    value = stored.get("value")
    return value if isinstance(value, dict) else {}


def _pick_option_js(trigger_id: str, option_pattern: str) -> str:
    """The script that picks the option whose text matches ``option_pattern`` (a JS regex literal) in the select ``trigger_id``."""
    return _PICK_OPTION_JS.replace("__TRIGGER_ID__", json.dumps(trigger_id)).replace("__OPTION_PATTERN__", option_pattern)


def save_local_whisper_on_the_page(client: ChromeMcpClient, page: McpPage, api_url: str, voice_url: str) -> None:
    """Switch speech-to-text on and pick Local Whisper as a user does; the page saves its settings by itself."""
    wait_for_state(client, page, _VOICE_PANEL_JS, timeout_sec=90.0, page_url=voice_url)
    switched = client.evaluate(page, _SWITCH_STT_ON_JS, timeout_sec=15.0)
    assert isinstance(switched, dict) and switched.get("ok") is True, switched
    wait_for_state(client, page, _pick_option_js("voice-stt-provider", "/Local Whisper/"), timeout_sec=60.0)
    wait_until(
        lambda: saved_voice_settings(api_url).get("sttProvider") == "local",
        "the voice settings page never saved Local Whisper as the speech-to-text provider",
    )
    saved = saved_voice_settings(api_url)
    assert saved.get("sttEnabled") is True, saved


_MODE_SAVED_ON_DEVICE_JS = "(() => ({ ready: localStorage.getItem('voiceSessionMode') === 'agent_bridge' }))()"


def choose_agent_bridge_mode_on_the_page(client: ChromeMcpClient, page: McpPage) -> None:
    """Pick the Agent Bridge session mode on the voice settings page as a user does.

    The mode is a device setting (localStorage) and a chosen mode outranks the Preferences page's Agent Bridge switch, so
    choosing it here leaves no doubt which way the chat's voice button starts a session, whatever this browser kept before.
    """
    wait_for_state(client, page, _pick_option_js("voice-session-mode", "/Agent Bridge|Agent 桥接/"), timeout_sec=60.0)
    wait_for_state(client, page, _MODE_SAVED_ON_DEVICE_JS, timeout_sec=15.0)
