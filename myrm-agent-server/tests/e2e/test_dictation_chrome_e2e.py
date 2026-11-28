"""Chrome E2E: speaking into the composer's mic button becomes text in the composer.

Real business flow through the WebUI; the one stand-in is the microphone (see ``tests.support.voice_input_chrome``). The backend
is private and fresh, so nobody has a model provider yet. Two users are under test:

* The user who sets voice up first: turn on Voice Interaction (the experimental feature that shows the mic button), open
  Settings > Channels > Voice, switch speech-to-text on and pick Local Whisper (the page saves the settings itself), then
  dictate twice into the same composer. The first dictation streams over ``/ws/stt/stream``, whose server tells the page that
  Local Whisper transcribes in one request after the audio; the second therefore goes through the upload route
  ``/stt/transcribe`` and lands after the first sentence.
* The user who has not set voice up: the mic button tells them so instead of failing silently, and the second press learns
  from the upload route's answer that the server cannot help and falls back to the browser's own recognition.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_dictation_chrome_e2e.py::<test_name>
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.chrome_mcp_e2e import (
    ChromeMcpClient,
    McpPage,
    ensure_chat_route,
    get_e2e_api_url,
    get_e2e_ui_url,
    http_json,
    open_mcp_page,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.spoken_audio import SPOKEN_WORDS, synthesize_speech, whisper_base_model_cached
from tests.support.voice_input_chrome import (
    COMPOSER_READY_JS,
    MIC_BUTTON_FINDER,
    PRESS_MIC_JS,
    VOICE_SETTINGS_ROUTE,
    install_speaking_microphone,
    save_local_whisper_on_the_page,
    tone_wav,
    upload_answers,
)

_CHAT_PATH = "/"
_PAGE_TIMEOUT_MS = 120_000
# Whisper loads on first use and the host may be loaded: the transcript gets this long to reach the composer.
_DICTATION_TIMEOUT_SEC = 300.0
_CLEAN_CLOSE = 1000

# Placeholders, not an f-string: the scripts are mostly braces.
_STOP_WHEN_THE_PAGE_FORGOT_JS = """
  // The page stops by itself after two seconds of silence; the press only covers a page that stopped checking for silence.
  if (/^(Stop recording|停止录音)/.test(label || '') && probe.ended && Date.now() - probe.endedAt > 5000 && !probe.stopPressed) {
    probe.stopPressed = true;
    mic.click();
  }"""

_FIRST_DICTATION_PROGRESS_JS = """(() => {
  const probe = window.__microphone;
  // A reload (the dev server hot-reloads) wipes the microphone this test installed: report it instead of waiting it out.
  if (!probe) return { ready: true, err: 'the page lost the installed microphone, most likely by reloading' };
  const input = document.querySelector('textarea[data-chat-input]');
  const mic = __MIC_BUTTON_FINDER__;
  const label = mic ? (mic.getAttribute('aria-label') || '').trim() : null;
__STOP_WHEN_THE_PAGE_FORGOT__
  const text = input ? input.value : null;
  const heard = (text || '').toLowerCase();
  const sockets = window.__voiceSockets || [];
  const wordsIn = __WORDS__.every((word) => heard.includes(word));
  const socketsClosed = sockets.length > 0 && sockets.every((s) => s.closeCode !== null);
  if (socketsClosed && !probe.closedAt) probe.closedAt = Date.now();
  // A dictation the server ended badly, or ended cleanly without the words reaching the composer, will not produce them
  // later: report it now instead of after the whole wait.
  const endedBadly = sockets.some((s) => s.closeCode !== null && s.closeCode !== __CLEAN_CLOSE__);
  const gaveUp = socketsClosed && !wordsIn && Date.now() - probe.closedAt > 5000;
  return {
    ready: endedBadly || gaveUp || (wordsIn && (!__REQUIRE_CLOSED__ || socketsClosed)),
    text, label, sockets, toasts: window.__toastLog || [], bodyLength: (document.body?.innerText || '').length,
    microphone: { contextState: probe.contextState, plays: probe.plays, ended: probe.ended, stopPressed: probe.stopPressed },
  };
})()"""

# The second dictation opens no socket: the words must show up after what the composer already held, and nothing may complain.
_SECOND_DICTATION_PROGRESS_JS = """(() => {
  const probe = window.__microphone;
  if (!probe) return { ready: true, err: 'the page lost the installed microphone, most likely by reloading' };
  const input = document.querySelector('textarea[data-chat-input]');
  const mic = __MIC_BUTTON_FINDER__;
  const label = mic ? (mic.getAttribute('aria-label') || '').trim() : null;
__STOP_WHEN_THE_PAGE_FORGOT__
  const text = input ? input.value : '';
  const dictated = text.slice(__KEPT_LENGTH__).toLowerCase();
  const wordsIn = __WORDS__.every((word) => dictated.includes(word));
  const sockets = window.__voiceSockets || [];
  const complaint = (window.__toastLog || []).slice(__TOASTS_BEFORE__)
    .find((toast) => /transcri|stt|speech|microphone|录音|转录|语音/i.test(toast)) || null;
  return {
    ready: wordsIn || complaint !== null || sockets.length > 1,
    text, label, socketCount: sockets.length, complaint, toasts: window.__toastLog || [],
    bodyLength: (document.body?.innerText || '').length,
    microphone: { contextState: probe.contextState, plays: probe.plays, ended: probe.ended, stopPressed: probe.stopPressed },
  };
})()"""

# Ready once the toast this step expects has been shown and the mic button is idle again (not stuck recording).
_STEP_PROGRESS_JS = """(() => {
  const probe = window.__microphone;
  if (!probe) return { ready: true, err: 'the page lost the installed microphone, most likely by reloading' };
  const mic = __MIC_BUTTON_FINDER__;
  const label = mic ? (mic.getAttribute('aria-label') || '').trim() : null;
  const toasts = window.__toastLog || [];
  const shown = toasts.some((toast) => __EXPECTED_TOAST__.test(toast));
  return {
    ready: shown && /^(Voice input|语音输入)/.test(label || ''),
    label, toasts, sockets: window.__voiceSockets || [], bodyLength: (document.body?.innerText || '').length,
    microphone: { contextState: probe.contextState, plays: probe.plays, ended: probe.ended },
  };
})()"""


def _first_dictation_progress_js(*, require_closed: bool) -> str:
    """The composer text, the mic button and the dictation sockets; ready once the spoken words are in the composer."""
    return (
        _FIRST_DICTATION_PROGRESS_JS.replace("__STOP_WHEN_THE_PAGE_FORGOT__", _STOP_WHEN_THE_PAGE_FORGOT_JS)
        .replace("__MIC_BUTTON_FINDER__", MIC_BUTTON_FINDER)
        .replace("__WORDS__", json.dumps(list(SPOKEN_WORDS)))
        .replace("__CLEAN_CLOSE__", str(_CLEAN_CLOSE))
        .replace("__REQUIRE_CLOSED__", "true" if require_closed else "false")
    )


def _second_dictation_progress_js(*, kept_length: int, toasts_before: int) -> str:
    return (
        _SECOND_DICTATION_PROGRESS_JS.replace("__STOP_WHEN_THE_PAGE_FORGOT__", _STOP_WHEN_THE_PAGE_FORGOT_JS)
        .replace("__MIC_BUTTON_FINDER__", MIC_BUTTON_FINDER)
        .replace("__WORDS__", json.dumps(list(SPOKEN_WORDS)))
        .replace("__KEPT_LENGTH__", str(kept_length))
        .replace("__TOASTS_BEFORE__", str(toasts_before))
    )


def _step_progress_js(expected_toast: str) -> str:
    return _STEP_PROGRESS_JS.replace("__MIC_BUTTON_FINDER__", MIC_BUTTON_FINDER).replace("__EXPECTED_TOAST__", expected_toast)


def _wait_for_progress(client: ChromeMcpClient, page: McpPage, script: str, *, timeout_sec: float) -> dict[str, object]:
    """Wait for the page to report ready, or to report that it cannot.

    No ``page_url``: the wait must never reload the page, which would wipe the microphone this test installed.
    """
    state = wait_for_state(client, page, script, timeout_sec=timeout_sec)
    assert "err" not in state, state
    return state


def _press_mic(client: ChromeMcpClient, page: McpPage) -> None:
    pressed = client.evaluate(page, PRESS_MIC_JS, timeout_sec=15.0)
    assert isinstance(pressed, dict) and pressed.get("ok") is True, pressed


def _session_with_voice_feature(api_url: str) -> None:
    prepare_e2e_ui_session(api_url)
    http_json("POST", f"{api_url}/api/v1/features/voice_interaction/toggle", {"enabled": True})


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dictation_turns_speech_into_composer_text_and_appends_the_next_one(tmp_path: Path) -> None:
    wav = synthesize_speech(tmp_path / "speech.wav")
    if wav is None:
        pytest.skip("no system speech synthesizer (`say`) to produce real speech")
    if not whisper_base_model_cached():
        pytest.skip("the faster-whisper `base` model is not cached; the test does not download it")

    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url().rstrip("/")
    _session_with_voice_feature(api_url)
    voice_url = f"{ui_url}{VOICE_SETTINGS_ROUTE}"
    chat_url = f"{ui_url}{_CHAT_PATH}"

    warm_ui_route(_CHAT_PATH)
    with open_settings_subroute(VOICE_SETTINGS_ROUTE, timeout_ms=_PAGE_TIMEOUT_MS) as (client, page):
        save_local_whisper_on_the_page(client, page, api_url, voice_url)

        ensure_chat_route(client, page, target_url=chat_url, timeout_ms=_PAGE_TIMEOUT_MS)
        wait_for_state(client, page, COMPOSER_READY_JS, timeout_sec=90.0, page_url=chat_url)
        install_speaking_microphone(client, page, wav)

        _press_mic(client, page)
        _wait_for_progress(client, page, _first_dictation_progress_js(require_closed=False), timeout_sec=_DICTATION_TIMEOUT_SEC)
        first = _wait_for_progress(client, page, _first_dictation_progress_js(require_closed=True), timeout_sec=60.0)

        first_text = str(first["text"])
        toasts_before = len(first["toasts"]) if isinstance(first["toasts"], list) else 0
        _press_mic(client, page)
        second = _wait_for_progress(
            client,
            page,
            _second_dictation_progress_js(kept_length=len(first_text), toasts_before=toasts_before),
            timeout_sec=_DICTATION_TIMEOUT_SEC,
        )
        answers = upload_answers(client, page)

    heard = first_text.lower()
    assert all(word in heard for word in SPOKEN_WORDS), f"the spoken words never reached the composer: {first}"
    sockets = first["sockets"]
    assert isinstance(sockets, list) and len(sockets) == 1, f"one dictation opens one socket: {first}"
    assert sockets[0]["closeCode"] == _CLEAN_CLOSE, (
        f"the server must end a dictation with a clean close, or the page downgrades its next one: {first}"
    )
    final = [m for m in sockets[0]["messages"] if m.get("type") == "transcript" and m.get("is_final") is True]
    assert len(final) == 1 and final[0]["text"] == first_text, (
        f"the composer must hold the one final transcript the server sent: {first}"
    )

    assert second.get("complaint") is None, f"the second dictation reported a problem instead of text: {second}"
    assert second["socketCount"] == 1, (
        f"Local Whisper has no live stream, so the second dictation must not open a socket: {second}"
    )
    assert [answer["status"] for answer in answers] == [200], (
        f"the second dictation must go through the upload route exactly once and be accepted, saw {answers}: {second}"
    )
    composer = str(second["text"])
    assert composer.startswith(f"{first_text} "), f"the second dictation must follow the first after one space: {second}"
    assert all(word in composer[len(first_text) :].lower() for word in SPOKEN_WORDS), (
        f"the spoken words never reached the composer the second time: {second}"
    )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_dictation_before_voice_is_set_up_tells_the_user_and_leaves_the_mic_usable() -> None:
    api_url = get_e2e_api_url()
    chat_url = f"{get_e2e_ui_url().rstrip('/')}{_CHAT_PATH}"
    _session_with_voice_feature(api_url)

    warm_ui_route(_CHAT_PATH)
    with open_mcp_page(chat_url, timeout_ms=_PAGE_TIMEOUT_MS) as (client, page):
        ensure_chat_route(client, page, target_url=chat_url, timeout_ms=_PAGE_TIMEOUT_MS)
        wait_for_state(client, page, COMPOSER_READY_JS, timeout_sec=90.0, page_url=chat_url)
        install_speaking_microphone(client, page, tone_wav())

        # No settings were saved: the dictation socket is refused and says why.
        _press_mic(client, page)
        refused = _wait_for_progress(client, page, _step_progress_js("/STT not configured/"), timeout_sec=60.0)

        # The page then asks the upload route, which answers 400: the server cannot help, the browser's own recognition is next.
        _press_mic(client, page)
        fallback = _wait_for_progress(
            client, page, _step_progress_js("/switching to browser speech recognition/"), timeout_sec=90.0
        )
        answers = upload_answers(client, page)

    sockets = refused["sockets"]
    assert isinstance(sockets, list) and len(sockets) == 1, f"the first press opens the dictation socket once: {refused}"
    assert {"type": "error", "message": "STT not configured"} in sockets[0]["messages"], refused
    assert sockets[0]["closeCode"] == 1011, f"a dictation the server cannot serve ends with 1011: {refused}"
    assert [answer["status"] for answer in answers] == [400], (
        f"the second press must ask the upload route exactly once and be refused, saw {answers}: {fallback}"
    )
    # The page words every 400 alike: only the answer's body tells a recording refused for its format from voice not set up.
    assert "STT is not enabled" in str(answers[0]["body"]), (
        f"the upload must be refused because speech-to-text is not set up, not for the recording's format: {answers}"
    )
    assert isinstance(fallback["sockets"], list) and len(fallback["sockets"]) == 1, (
        f"the second press opens no socket: {fallback}"
    )
