"""Chrome LIVE E2E support: a chat whose agent has a media tool, and the page reads that find the cards the tool's results become.

[INPUT]
- tests.support.chrome_mcp_e2e (POS: private SHPOIB backend + CDP page helpers)
- tests.support.chrome_skill_hooks_composer / chrome_skill_hooks_live_e2e (POS: typing a turn into the composer; an empty chat of an agent)
- tests.support.media_gateway_stub (POS: what the stand-in gateway saw and answered)
- cdp_chat.support (POS: the settings rows and the stored messages of the stack under test)

[OUTPUT]
- MediaScene: one chat plus the settings selection of its media tool; ``send()`` types a prompt and reads the cards the page shows once the
  turn is over and again after a reload (optionally refreshed first, while the gateway still holds the request); ``stored_media()`` and
  ``account()`` read what the chat stored, the context a failed wait is read with
- cards_js() / audio_card_js() / failed_card_js() / no_media_js(): page reads that are ready on the card a scenario expects
- assert_cards_show_the_provider_images(): every card image is one the gateway sent, at a size it was asked for, the same live and after the reload
- gateway_of(): the gateway setting that points a media tool at the stand-in
- fetch_bytes() / shown_images() / shown_audios(): the bytes behind a card and the media a page state holds

[POS]
Shared by tests/e2e/test_generated_media_cards_live_chrome_e2e.py: the scenarios (prompts, selections, assertions) live there, the driving of the page here.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_LIB = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from cdp_chat.support import fetch_chat_messages, fetch_config_value, put_config_value  # noqa: E402

from tests.support.chrome_chat_page_observe import reload_the_chat  # noqa: E402
from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage, get_e2e_ui_url, wait_for_state  # noqa: E402
from tests.support.chrome_skill_hooks_composer import drive_chat_turn  # noqa: E402
from tests.support.chrome_skill_hooks_live_e2e import SkillChat  # noqa: E402
from tests.support.chrome_skill_hooks_slash_js import ATTACH_CHAT_PROBE  # noqa: E402
from tests.support.media_gateway_stub import MediaGatewayStub  # noqa: E402

_TURN_WAIT_SEC = 240.0
_CARD_WAIT_SEC = 90.0
_FAILED_CARD = "任务失败|Task Failed"

_AGENT_BOUND_JS = """((agentId) => {
  const bound = window.__myrmChatStore?.getState?.()?.agentConfig?.agentId ?? null;
  return { ready: bound === agentId, bound };
})"""

# The turn is over for the page: it stopped streaming and holds as many answers as the chat has had turns.
_TURN_OVER_JS = """((answers) => {
  const turn = window.__MYRM_E2E_CHAT__?.turnSnapshot?.() ?? {};
  const answered = String(turn.lastAssistantSample ?? '').trim().length > 0;
  return { ready: turn.isStreaming === false && Number(turn.assistantCount ?? 0) >= answers && answered, turn };
})"""

_MEDIA_ELEMENTS_JS = """
  const stored = (element) => (element.currentSrc || element.src).includes('/api/v1/media/');
  const images = Array.from(document.images)
    .filter(stored)
    .map((img) => ({
      src: img.currentSrc || img.src,
      loaded: img.complete && img.naturalWidth > 0,
      width: img.naturalWidth,
      height: img.naturalHeight,
    }));
  const videos = Array.from(document.querySelectorAll('video')).filter(stored).map((video) => video.currentSrc || video.src);
  const audios = Array.from(document.querySelectorAll('audio'))
    .filter(stored)
    .map((audio) => ({ src: audio.currentSrc || audio.src, loaded: audio.readyState >= 1 && audio.duration > 0 }));
  const fullText = String(document.body?.innerText || '');
  const pageText = fullText.slice(-700);"""


def _call(js_function: str, argument: object) -> str:
    return f"({js_function})({json.dumps(argument)})"


def cards_js(distinct_images: int) -> str:
    """Ready once exactly this many distinct media images are on the page and every one of them has loaded."""
    return f"""(() => {{{_MEDIA_ELEMENTS_JS}
  const distinct = new Set(images.map((img) => img.src));
  return {{ ready: distinct.size === {distinct_images} && images.every((img) => img.loaded), images, pageText }};
}})()"""


def audio_card_js() -> str:
    """Ready once exactly one media audio is on the page and it has loaded far enough to report a duration."""
    return f"""(() => {{{_MEDIA_ELEMENTS_JS}
  const distinct = new Set(audios.map((audio) => audio.src));
  return {{ ready: distinct.size === 1 && audios.every((audio) => audio.loaded), audios, pageText }};
}})()"""


def no_media_js() -> str:
    """Ready at once; reports the media the page shows, for a turn that is not expected to produce any."""
    return f"""(() => {{{_MEDIA_ELEMENTS_JS}
  return {{ ready: true, images, videos, audios, pageText }};
}})()"""


def failed_card_js(reason: str) -> str:
    """Ready once the page shows a failed task whose text holds ``reason``; reports the media it shows meanwhile."""
    return f"""(() => {{{_MEDIA_ELEMENTS_JS}
  const failed = new RegExp({json.dumps(_FAILED_CARD)}).test(fullText) && fullText.includes({json.dumps(reason)});
  return {{ ready: failed, images, videos, pageText }};
}})()"""


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - the loopback address of the stack under test
        body: bytes = response.read()
    return body


def shown_audios(state: dict[str, object]) -> list[dict[str, object]]:
    audios = state.get("audios")
    assert isinstance(audios, list), state
    return [audio for audio in audios if isinstance(audio, dict)]


def shown_images(state: dict[str, object]) -> list[dict[str, object]]:
    images = state.get("images")
    assert isinstance(images, list), state
    return [image for image in images if isinstance(image, dict)]


@dataclass
class MediaScene:
    """A chat of the stack under test; ``selection`` is what the settings row ``config_key`` holds for the agent's media tool."""

    probe: SkillChat
    api_url: str
    config_key: str
    selection: dict[str, object]
    stub: MediaGatewayStub | None = None

    @property
    def chat_url(self) -> str:
        return f"{get_e2e_ui_url().rstrip('/')}{self.probe.ui_path}"

    def _holds_selection(self, stored: dict[str, object]) -> bool:
        return all(stored.get(key) == value for key, value in self.selection.items())

    def pick_the_model(self) -> None:
        """The user's media model in the settings; written again when the page's own settings sync replaced it."""
        current = fetch_config_value(self.config_key, api_url=self.api_url)
        if not self._holds_selection(current):
            print(f"E2E_MEDIA_CARDS: {self.config_key} lacks the selection, writing it", flush=True)
            put_config_value(self.config_key, {**current, **self.selection}, api_url=self.api_url)

    @contextmanager
    def model_picked(self) -> Iterator[None]:
        """The stack's own settings are put back afterwards."""
        previous = fetch_config_value(self.config_key, api_url=self.api_url)
        self.pick_the_model()
        try:
            yield
        finally:
            put_config_value(self.config_key, previous, api_url=self.api_url)

    def stored_media(self) -> list[dict[str, object]]:
        """The media entries the chat stored on its replies."""
        entries: list[dict[str, object]] = []
        for message in fetch_chat_messages(self.probe.chat_id, api_url=self.api_url):
            metadata = message.get("metadata")
            found = metadata.get("media_generated") if isinstance(metadata, dict) else None
            if isinstance(found, list):
                entries.extend(entry for entry in found if isinstance(entry, dict))
        return entries

    def account(self) -> str:
        """What the provider saw and what the chat stored: the context a failed wait is read with."""
        replies = [
            (message.get("role"), str(message.get("content"))[:300])
            for message in fetch_chat_messages(self.probe.chat_id, api_url=self.api_url)
        ]
        stored = fetch_config_value(self.config_key, api_url=self.api_url)
        lines = [f"{self.config_key} holds the selection: {self._holds_selection(stored)}"]
        if self.stub is not None:
            asked = [(request.path, request.requested_size) for request in self.stub.requests]
            lines.append(f"provider asked: {asked}, answered {len(self.stub.responses)} time(s)")
        lines.append(f"stored media entries: {json.dumps(self.stored_media(), ensure_ascii=False)[:1200]}")
        lines.append(f"stored messages: {replies}")
        return "\n".join(lines)

    def wait(self, client: ChromeMcpClient, page: McpPage, expression: str, timeout_sec: float) -> dict[str, object]:
        try:
            return wait_for_state(client, page, expression, timeout_sec=timeout_sec, page_url=self.chat_url)
        except AssertionError as exc:
            raise AssertionError(f"{exc}\n{self.account()}") from exc

    def _reload_once_the_gateway_is_asked(self, client: ChromeMcpClient, page: McpPage) -> None:
        """Refresh the page while the gateway still holds the request, as a user does who does not wait for the picture."""
        assert self.stub is not None
        deadline = time.monotonic() + _TURN_WAIT_SEC
        while not self.stub.requests:
            assert time.monotonic() < deadline, f"the model never called the gateway\n{self.account()}"
            time.sleep(0.2)
        assert not self.stub.responses, (
            "the gateway had answered before the page was refreshed: not a refresh while the picture is drawn"
        )
        reload_the_chat(client, page, self.chat_url)
        self.wait(client, page, ATTACH_CHAT_PROBE.format(chat_id_json=json.dumps(self.probe.chat_id)), 90.0)

    def send(self, prompt: str, answers: int, ready_js: str, *, reload_when_asked: bool = False) -> list[dict[str, object]]:
        """Type ``prompt`` into the composer and press send; returns the page state ``ready_js`` reached once the turn was over, then after a reload.

        With ``reload_when_asked`` the page is refreshed first, while the gateway still holds the request.
        """
        seen: list[dict[str, object]] = []

        def agent_bound(client: ChromeMcpClient, page: McpPage) -> None:
            bound = self.wait(client, page, _call(_AGENT_BOUND_JS, self.probe.agent_id), 90.0)
            assert bound.get("ready") is True, bound
            self.pick_the_model()

        def watch(client: ChromeMcpClient, page: McpPage) -> None:
            if reload_when_asked:
                self._reload_once_the_gateway_is_asked(client, page)
            self.wait(client, page, _call(_TURN_OVER_JS, answers), _TURN_WAIT_SEC)
            seen.append(self.wait(client, page, ready_js, _CARD_WAIT_SEC))
            reload_the_chat(client, page, self.chat_url)
            self.wait(client, page, ATTACH_CHAT_PROBE.format(chat_id_json=json.dumps(self.probe.chat_id)), 90.0)
            seen.append(self.wait(client, page, ready_js, _CARD_WAIT_SEC))

        drive_chat_turn(get_e2e_ui_url(), self.probe, prompt, invoke_skill=False, before_send=agent_bound, while_open=watch)
        assert len(seen) == 2, f"the page was left before the cards were read: {seen}\n{self.account()}"
        return seen


def assert_cards_show_the_provider_images(stub: MediaGatewayStub, seen: list[dict[str, object]], where: str) -> None:
    """Every distinct card image is one of the provider's answers, byte for byte, at a size the provider was asked for."""
    asked = {request.requested_size for request in stub.requests}
    for src, image in {str(image["src"]): image for image in shown_images(seen[0])}.items():
        assert fetch_bytes(src) in stub.responses, f"{where}: the card image {src} is not an image the provider sent"
        assert (image["width"], image["height"]) in asked, (
            f"{where}: card image {src} is {image['width']}x{image['height']}, the provider was asked for {sorted(asked)}"
        )
    live, reloaded = (sorted({str(image["src"]) for image in shown_images(state)}) for state in seen)
    assert live == reloaded, f"{where}: the reload shows other images than the open page did: {live} vs {reloaded}"


def gateway_of(stub: MediaGatewayStub) -> dict[str, object]:
    return {"use_gateway": True, "gateway_url": stub.url, "auth_token": "stub-gateway-token"}
