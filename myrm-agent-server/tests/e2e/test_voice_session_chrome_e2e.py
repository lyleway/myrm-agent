"""Chrome E2E: a voice session in Agent Bridge mode hears every utterance of the user, with Local Whisper.

Real business flow through the WebUI; the one stand-in is the microphone (see ``tests.support.voice_input_chrome``). The backend
is private and fresh, so there is no model provider: the session is only asked to hear, which is all voice setup promises.

    turn on Voice Interaction -> Settings > Channels > Voice: speech-to-text on, Local Whisper, session mode Agent Bridge
    -> Settings > Preferences: switch on the voice session
    -> open the chat page, start the voice session and let the microphone speak twice
    -> the server answers each utterance with the text it heard (``stt_final``).

Local Whisper has no live stream: the server collects the audio of one utterance and transcribes it only when the page says the
utterance has ended (``end_utterance``). So this test is also the proof that the page does say it: a page that never does gets
no text, however well the server works.

Formal run::

    ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_voice_session_chrome_e2e.py::<test_name>
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
    navigate_mcp_page,
    open_settings_subroute,
    prepare_e2e_ui_session,
    wait_for_state,
    warm_ui_route,
)
from tests.support.spoken_audio import SPOKEN_WORDS, synthesize_speech, whisper_base_model_cached
from tests.support.voice_input_chrome import (
    VOICE_SETTINGS_ROUTE,
    choose_agent_bridge_mode_on_the_page,
    install_speaking_microphone,
    save_local_whisper_on_the_page,
)

_CHAT_PATH = "/"
_PREFERENCES_ROUTE = "/settings/preferences"
_PAGE_TIMEOUT_MS = 120_000
# Whisper loads on first use and the host may be loaded: the first utterance gets this long to come back as text.
_FIRST_UTTERANCE_TIMEOUT_SEC = 300.0
_NEXT_UTTERANCE_TIMEOUT_SEC = 120.0
# The page ends an empty utterance after ten quiet seconds; the rest is room for a loaded host.
_QUIET_SESSION_TIMEOUT_SEC = 45.0

_SESSION_BUTTON_FINDER = (
    "Array.from(document.querySelectorAll('button')).find("
    "(b) => /^(Start voice session|Stop voice session|开始语音会话|结束语音会话)/.test((b.getAttribute('aria-label') || '').trim()))"
)

# The switch is a row of the Preferences page, labelled by its title (English and Chinese catalogs).
_SESSION_SWITCH_FINDER = """(() => Array.from(document.querySelectorAll('[role="switch"]')).find((el) => {
  const title = document.getElementById(el.getAttribute('aria-labelledby') || '');
  return title !== null && /^(Voice session mode|语音会话模式)$/.test((title.textContent || '').trim());
}) || null)"""

_PREFERENCES_READY_JS = f"(() => ({{ ready: ({_SESSION_SWITCH_FINDER})() !== null }}))()"

_TURN_ON_JS = f"""(() => {{
  const toggle = ({_SESSION_SWITCH_FINDER})();
  if (toggle === null) return {{ ok: false, err: 'voice session switch missing' }};
  if (toggle.getAttribute('aria-checked') !== 'true') toggle.click();
  return {{ ok: true }};
}})()"""

# The page keeps the switch on this device (localStorage), which is what the chat's voice button reads.
_SAVED_ON_DEVICE_JS = "(() => ({ ready: localStorage.getItem('voiceSessionEnabled') === 'true' }))()"

# Ready once the voice button offers to start a session: the state before a session and the one a stopped session returns to.
_SESSION_IDLE_BUTTON_READY_JS = f"""(() => {{
  const button = {_SESSION_BUTTON_FINDER};
  const label = button ? (button.getAttribute('aria-label') || '').trim() : null;
  return {{ ready: /^(Start voice session|开始语音会话)/.test(label || ''), label }};
}})()"""

_PRESS_SESSION_BUTTON_JS = f"""(() => {{
  const button = {_SESSION_BUTTON_FINDER};
  if (!button) return {{ ok: false, err: 'session-button-missing' }};
  window.__sessionPressedAt = Date.now();
  button.click();
  return {{ ok: true }};
}})()"""

_SPEAK_AGAIN_JS = "(() => { window.__speakAgain(0.5); return { ok: true }; })()"

# Placeholders, not an f-string: the script is mostly braces.
_SESSION_PROGRESS_JS = """(() => {
  const probe = window.__microphone;
  // A reload (the dev server hot-reloads) wipes the microphone this test installed: report it instead of waiting it out.
  if (!probe) return { ready: true, err: 'the page lost the installed microphone, most likely by reloading' };
  const opened = window.__voiceSockets || [];
  const sockets = opened.filter((s) => s.path === '/ws/voice/session');
  const socket = sockets[0] || null;
  const finals = socket ? socket.messages.filter((m) => m.type === 'stt_final').map((m) => m.text) : [];
  // Only an end_utterance that follows audio closes speech; one that follows none is the heartbeat of a quiet session.
  const endUtterances = socket ? socket.sent.filter((m) => m.type === 'end_utterance') : [];
  const ends = endUtterances.filter((m) => m.audioFramesBefore > 0).length;
  const heartbeats = endUtterances.length - ends;
  const button = __SESSION_BUTTON_FINDER__;
  // The voice button opens the session socket the moment it is pressed: without one the session ran some other way.
  const noSocket = socket === null && Date.now() - (window.__sessionPressedAt || Date.now()) > 20000;
  // A page that never says an utterance has ended gets no text, however long it waits: report it as soon as it is clear.
  const neverEnded = probe.ended && Date.now() - probe.endedAt > 20000 && ends < probe.plays;
  const closed = socket !== null && socket.closeCode !== null;
  return {
    ready: (finals.length >= __FINALS__ && heartbeats >= __HEARTBEATS__) || noSocket || neverEnded || closed || sockets.length > 1,
    finals, ends, heartbeats, noSocket, neverEnded, closed, closeCode: socket ? socket.closeCode : null, socketCount: sockets.length,
    serverFrames: socket ? socket.messages.map((m) => m.type) : [], binaryFrames: socket ? socket.binaryFrames : 0,
    opened: opened.map((s) => s.path), mode: localStorage.getItem('voiceSessionMode'),
    label: button ? (button.getAttribute('aria-label') || '').trim() : null,
    toasts: window.__toastLog || [], bodyLength: (document.body?.innerText || '').length,
    microphone: { contextState: probe.contextState, requests: probe.microphoneRequests, plays: probe.plays, ended: probe.ended },
  };
})()"""


def _session_progress_js(*, finals: int, heartbeats: int) -> str:
    return (
        _SESSION_PROGRESS_JS.replace("__SESSION_BUTTON_FINDER__", _SESSION_BUTTON_FINDER)
        .replace("__FINALS__", str(finals))
        .replace("__HEARTBEATS__", str(heartbeats))
    )


def _wait_for_utterances(
    client: ChromeMcpClient, page: McpPage, *, finals: int, timeout_sec: float, heartbeats: int = 0
) -> dict[str, object]:
    """Wait until the session has answered ``finals`` utterances and the page has sent ``heartbeats`` heartbeats, or it cannot.

    No ``page_url``: the wait must never reload the page, which would wipe the microphone this test installed.
    """
    state = wait_for_state(client, page, _session_progress_js(finals=finals, heartbeats=heartbeats), timeout_sec=timeout_sec)
    assert "err" not in state, state
    assert state["noSocket"] is False, f"pressing the voice button did not open the voice session socket: {state}"
    assert state["neverEnded"] is False, (
        f"the page never told the server an utterance had ended, so Local Whisper was never asked to transcribe it: {state}"
    )
    assert state["closed"] is False and state["socketCount"] == 1, f"the voice session socket must stay open: {state}"
    return state


def _count(state: dict[str, object], key: str) -> int:
    value = state[key]
    assert isinstance(value, int), state
    return value


def _turn_on_voice_session(client: ChromeMcpClient, page: McpPage, preferences_url: str) -> None:
    wait_for_state(client, page, _PREFERENCES_READY_JS, timeout_sec=90.0, page_url=preferences_url)
    turned_on = client.evaluate(page, _TURN_ON_JS, timeout_sec=15.0)
    assert isinstance(turned_on, dict) and turned_on.get("ok") is True, turned_on
    wait_for_state(client, page, _SAVED_ON_DEVICE_JS, timeout_sec=30.0)


def _press_session_button(client: ChromeMcpClient, page: McpPage) -> None:
    pressed = client.evaluate(page, _PRESS_SESSION_BUTTON_JS, timeout_sec=15.0)
    assert isinstance(pressed, dict) and pressed.get("ok") is True, pressed


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_voice_session_in_agent_bridge_mode_transcribes_every_utterance_with_local_whisper(tmp_path: Path) -> None:
    wav = synthesize_speech(tmp_path / "speech.wav")
    if wav is None:
        pytest.skip("no system speech synthesizer (`say`) to produce real speech")
    if not whisper_base_model_cached():
        pytest.skip("the faster-whisper `base` model is not cached; the test does not download it")

    api_url = get_e2e_api_url()
    ui_url = get_e2e_ui_url().rstrip("/")
    prepare_e2e_ui_session(api_url)
    http_json("POST", f"{api_url}/api/v1/features/voice_interaction/toggle", {"enabled": True})
    voice_url = f"{ui_url}{VOICE_SETTINGS_ROUTE}"
    preferences_url = f"{ui_url}{_PREFERENCES_ROUTE}"
    chat_url = f"{ui_url}{_CHAT_PATH}"

    warm_ui_route(_CHAT_PATH)
    with open_settings_subroute(VOICE_SETTINGS_ROUTE, timeout_ms=_PAGE_TIMEOUT_MS) as (client, page):
        save_local_whisper_on_the_page(client, page, api_url, voice_url)
        choose_agent_bridge_mode_on_the_page(client, page)
        navigate_mcp_page(client, page, preferences_url)
        _turn_on_voice_session(client, page, preferences_url)

        ensure_chat_route(client, page, target_url=chat_url, timeout_ms=_PAGE_TIMEOUT_MS)
        wait_for_state(client, page, _SESSION_IDLE_BUTTON_READY_JS, timeout_sec=90.0, page_url=chat_url)
        install_speaking_microphone(client, page, wav)

        _press_session_button(client, page)
        first = _wait_for_utterances(client, page, finals=1, timeout_sec=_FIRST_UTTERANCE_TIMEOUT_SEC)

        spoke = client.evaluate(page, _SPEAK_AGAIN_JS, timeout_sec=15.0)
        assert isinstance(spoke, dict) and spoke.get("ok") is True, spoke
        second = _wait_for_utterances(client, page, finals=2, timeout_sec=_NEXT_UTTERANCE_TIMEOUT_SEC)

        # Nobody speaks now: the page rolls its recording over after ten quiet seconds and ends the empty utterance.
        quiet = _wait_for_utterances(
            client, page, finals=2, heartbeats=_count(second, "heartbeats") + 1, timeout_sec=_QUIET_SESSION_TIMEOUT_SEC
        )

        _press_session_button(client, page)
        # Stopping the session puts the voice button back to offering a session.
        wait_for_state(client, page, _SESSION_IDLE_BUTTON_READY_JS, timeout_sec=30.0)

    assert "info" in first["serverFrames"], f"Local Whisper has no live stream, and the server says so first: {first}"
    finals = second["finals"]
    assert isinstance(finals, list) and len(finals) == 2, f"each utterance comes back as one text: {second}"
    for index, text in enumerate(finals, start=1):
        assert all(word in str(text).lower() for word in SPOKEN_WORDS), f"utterance {index} was not heard: {json.dumps(second)}"
    assert second["ends"] == 2, f"the page ends each utterance once: {second}"
    binary_frames = _count(second, "binaryFrames")
    assert binary_frames > 0, f"the page sent the audio of the microphone: {second}"
    # A quiet session stays open and says so with the empty utterance alone: no audio goes up and no text comes back.
    assert _count(quiet, "binaryFrames") == binary_frames, f"silence must not be uploaded: {quiet}"
    assert quiet["finals"] == second["finals"], f"silence must not be transcribed: {quiet}"
