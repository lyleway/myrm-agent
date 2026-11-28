"""Chrome LIVE E2E: the media a model generates or edits is a card in the chat, on the page that sent the turn and after a reload.

The user types a request for a picture, a video or a spoken sentence into the composer of a chat whose agent can use the media tool,
and presses send. The model calls the tool and the reply gets the result as a card: while the page that sent the turn is still open
(the card does not wait for a reload), after a reload mid-generation and after a reload of the finished turn, from what the chat
stored. A generation the provider refuses ends as a failed card, never as a broken picture or video, and a refused speech request
leaves no audio player behind.

The media gateway is the only stand-in (a loopback server the image and speech gateway settings point at); the model, the tool, the
task queue, the media library and the page are the product, and every card is checked against the bytes the gateway sent. The video
provider is the real one, called with a key it refuses.

Formal run::

    MYRM_E2E_ISOLATED_RUNTIME_ID=<id> ./myrm-dev test -m chrome_e2e \\
      myrm-agent/myrm-agent-server/tests/e2e/test_generated_media_cards_live_chrome_e2e.py::<node>
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.chrome_mcp_e2e import get_e2e_api_url, http_json
from tests.support.chrome_media_cards import (
    MediaScene,
    assert_cards_show_the_provider_images,
    audio_card_js,
    cards_js,
    failed_card_js,
    fetch_bytes,
    gateway_of,
    no_media_js,
    shown_audios,
)
from tests.support.chrome_skill_hooks_live_e2e import new_agent_chat, setup_auto_approving_agent
from tests.support.e2e_runtime_guard import E2EResourceLedger
from tests.support.media_gateway_stub import REFUSAL_MESSAGE, MediaGatewayStub, png_bytes, serve_media_gateway

_IMAGE_MODEL = "gpt-image-2.6-sunburst"
_IMAGE_TOOLS = ("image_generation",)
_IMAGE_SYSTEM_PROMPT = (
    "You make pictures with the image tool. When the user asks for a picture, call the image tool once for it, "
    "then answer in one short sentence. If the tool reports a failure, say so in one short sentence."
)
_GENERATE_PROMPT = "Please draw a small plain red square for me."
_REGENERATE_PROMPT = "Nice. Now draw another small square, this time plain blue."
_EDIT_PROMPT = "Please edit photo.png in my workspace with the image tool, so that its red turns blue."
_IMAGES_PER_REQUEST = 3
_SEVERAL_PROMPT = (
    f"Please draw {_IMAGES_PER_REQUEST} small plain red squares for me, "
    f"all in one image tool call (set its n to {_IMAGES_PER_REQUEST})."
)
_WORKSPACE_SEED = "/api/v1/chats/test/seed-file-edit-batch-workspace"
_SOURCE_SIZE = (640, 480)

# A real provider and model of the catalog with a key the provider refuses: the task is queued, then fails.
_VIDEO_SELECTION: dict[str, object] = {
    "provider": "volcengine",
    "model": "seedance-2-0",
    "apiKey": "invalid-key-the-provider-refuses",
    "timeoutSeconds": 30,
    "maxRetries": 0,
}
_VIDEO_TOOLS = ("video_generation",)
_VIDEO_SYSTEM_PROMPT = (
    "You make videos with the video tool. When the user asks for a video, call the video tool once for it, "
    "then answer in one short sentence. If the tool reports a failure, say so in one short sentence."
)
_VIDEO_PROMPT = "Please make a short video of a red square for me."

# The speech provider is elevenlabs, reached through the gateway of the agent; the voice row of the settings names it.
_SPEECH_SELECTION: dict[str, object] = {
    "ttsProvider": "elevenlabs",
    "ttsModel": "stub-speech-model",
    "ttsVoice": "stub-voice",
    "ttsApiKey": "stub-speech-key",
}
_SPEECH_TOOLS = ("tts",)
_SPEECH_SYSTEM_PROMPT = (
    "You read text aloud with the speech tool. When the user asks for something read out loud, call the speech tool once "
    "with that text, then answer in one short sentence. If the tool reports a failure, say so in one short sentence."
)
_SPEECH_PROMPT = "Please read this sentence out loud for me: The quick brown fox jumps over the lazy dog."

# Long enough for the page to be refreshed and attached again while the gateway still holds the request.
_SLOW_GATEWAY_SEC = 12.0


def _image_scene(tmp_path: Path, ledger: E2EResourceLedger, stub: MediaGatewayStub, prefix: str) -> MediaScene:
    agent_id = setup_auto_approving_agent(ledger, _IMAGE_SYSTEM_PROMPT, _IMAGE_TOOLS)
    selection: dict[str, object] = {"imageGeneration": {"model": _IMAGE_MODEL, "gatewayConfig": gateway_of(stub)}}
    return MediaScene(new_agent_chat(tmp_path, ledger, prefix, agent_id), get_e2e_api_url(), "personalSettings", selection, stub)


def _video_scene(tmp_path: Path, ledger: E2EResourceLedger, prefix: str) -> MediaScene:
    agent_id = setup_auto_approving_agent(ledger, _VIDEO_SYSTEM_PROMPT, _VIDEO_TOOLS)
    selection: dict[str, object] = {"videoGeneration": _VIDEO_SELECTION}
    return MediaScene(new_agent_chat(tmp_path, ledger, prefix, agent_id), get_e2e_api_url(), "personalSettings", selection)


def _speech_scene(tmp_path: Path, ledger: E2EResourceLedger, stub: MediaGatewayStub, prefix: str) -> MediaScene:
    """Speech reaches its provider through the gateway of the agent profile, so the agent itself is pointed at the stub."""
    agent_id = setup_auto_approving_agent(ledger, _SPEECH_SYSTEM_PROMPT, _SPEECH_TOOLS)
    api_url = get_e2e_api_url()
    http_json("PUT", f"{api_url}/api/v1/user-agents/{agent_id}", {"tool_gateway_config": gateway_of(stub)})
    return MediaScene(new_agent_chat(tmp_path, ledger, prefix, agent_id), api_url, "voice", _SPEECH_SELECTION, stub)


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_generated_image_is_a_card_on_the_open_page_and_after_a_reload(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    with serve_media_gateway() as stub:
        scene = _image_scene(tmp_path, e2e_resource_ledger, stub, "imgcard")
        with scene.model_picked():
            first = scene.send(_GENERATE_PROMPT, answers=1, ready_js=cards_js(1))
            assert stub.responses, "the provider never got a generation request: the model did not use the image tool"
            assert_cards_show_the_provider_images(stub, first, "first request")
            assert len(scene.stored_media()) == 1, scene.account()

            second = scene.send(_REGENERATE_PROMPT, answers=2, ready_js=cards_js(2))
            assert_cards_show_the_provider_images(stub, second, "second request")
            assert len(scene.stored_media()) == 2, f"each request leaves one card in the stored chat\n{scene.account()}"


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_an_edited_image_is_a_card_on_the_open_page_and_after_a_reload(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    with serve_media_gateway() as stub:
        scene = _image_scene(tmp_path, e2e_resource_ledger, stub, "imgedit")
        seeded = http_json("POST", f"{scene.api_url}{_WORKSPACE_SEED}?chat_id={scene.probe.chat_id}")
        assert isinstance(seeded, dict) and seeded.get("file_path"), seeded
        (Path(str(seeded["file_path"])).parent / "photo.png").write_bytes(png_bytes(_SOURCE_SIZE))
        with scene.model_picked():
            shown = scene.send(_EDIT_PROMPT, answers=1, ready_js=cards_js(1))
            assert stub.edits, f"the provider got no edit request: the model did not edit the image\n{scene.account()}"
            assert_cards_show_the_provider_images(stub, shown, "edit")
            assert len(scene.stored_media()) == 1, scene.account()


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_each_image_of_one_request_for_several_is_its_own_card(tmp_path: Path, e2e_resource_ledger: E2EResourceLedger) -> None:
    with serve_media_gateway() as stub:
        scene = _image_scene(tmp_path, e2e_resource_ledger, stub, "imgmany")
        with scene.model_picked():
            shown = scene.send(_SEVERAL_PROMPT, answers=1, ready_js=cards_js(_IMAGES_PER_REQUEST))
            assert len(stub.responses) == _IMAGES_PER_REQUEST, (
                f"the provider did not get one request per image\n{scene.account()}"
            )
            assert_cards_show_the_provider_images(stub, shown, "several images")
            assert len(scene.stored_media()) == _IMAGES_PER_REQUEST, scene.account()


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_refused_generation_ends_as_a_failed_card_and_no_broken_picture(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    with serve_media_gateway() as stub:
        stub.fail_status = 500
        scene = _image_scene(tmp_path, e2e_resource_ledger, stub, "imgfail")
        with scene.model_picked():
            shown = scene.send(_GENERATE_PROMPT, answers=1, ready_js=failed_card_js(REFUSAL_MESSAGE))
            for where, state in zip(("open page", "after the reload"), shown, strict=True):
                assert state["images"] == [], f"{where}: a refused generation must not show a picture: {state}"
            assert stub.requests and not stub.responses, "the provider was never asked, or it answered although it refuses"
            assert len(scene.stored_media()) >= 1, f"the task the model started is a card of the reply\n{scene.account()}"


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_video_the_provider_refuses_ends_as_a_failed_card_and_no_broken_video(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    scene = _video_scene(tmp_path, e2e_resource_ledger, "vidfail")
    with scene.model_picked():
        shown = scene.send(_VIDEO_PROMPT, answers=1, ready_js=failed_card_js(""))
        for where, state in zip(("open page", "after the reload"), shown, strict=True):
            assert state["images"] == [] and state["videos"] == [], f"{where}: a refused video must not show media: {state}"
        kinds = [entry.get("kind") for entry in scene.stored_media()]
        assert kinds == ["video"], f"the video task the model started is the one card of the reply: {kinds}\n{scene.account()}"


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_spoken_sentence_is_a_playable_audio_card_on_the_open_page_and_after_a_reload(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    with serve_media_gateway() as stub:
        scene = _speech_scene(tmp_path, e2e_resource_ledger, stub, "speech")
        with scene.model_picked():
            shown = scene.send(_SPEECH_PROMPT, answers=1, ready_js=audio_card_js())
            assert len(stub.speeches) == 1, f"the gateway did not get exactly one speech request\n{scene.account()}"
            assert stub.speeches[0].authorization == "Bearer stub-gateway-token", stub.speeches[0]
            assert "quick brown fox" in str(stub.speeches[0].fields.get("text")), stub.speeches[0]
            sources = {str(audio["src"]) for state in shown for audio in shown_audios(state)}
            assert len(sources) == 1, f"the reload shows another clip than the open page did: {sources}"
            assert fetch_bytes(sources.pop()) == stub.responses[0], "the audio card must play the very clip the gateway sent"
            kinds = [entry.get("kind") for entry in scene.stored_media()]
            assert kinds == ["tts"], f"the clip the model had spoken is the one card of the reply: {kinds}\n{scene.account()}"


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_refused_speech_request_leaves_no_audio_player_and_no_stored_card(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    with serve_media_gateway() as stub:
        stub.fail_status = 500
        scene = _speech_scene(tmp_path, e2e_resource_ledger, stub, "speechfail")
        with scene.model_picked():
            shown = scene.send(_SPEECH_PROMPT, answers=1, ready_js=no_media_js())
            for where, state in zip(("open page", "after the reload"), shown, strict=True):
                assert state["audios"] == [], f"{where}: a refused speech request must not show an audio player: {state}"
            assert stub.speeches and not stub.responses, "the gateway was never asked, or it answered although it refuses"
            assert scene.stored_media() == [], f"a speech request that failed registers no card\n{scene.account()}"


@pytest.mark.chrome_e2e(execution_mode="PRIVATE", access_scope="NAMESPACE_WRITE", workload="LIVE", private_reason="live_shpoib")
@pytest.mark.e2e_search_policy("empty")
@pytest.mark.integration
@pytest.mark.timeout(600)
def test_a_page_refreshed_while_the_image_is_drawn_still_gets_its_card(
    tmp_path: Path, e2e_resource_ledger: E2EResourceLedger
) -> None:
    with serve_media_gateway() as stub:
        stub.delay_sec = _SLOW_GATEWAY_SEC
        scene = _image_scene(tmp_path, e2e_resource_ledger, stub, "imgreload")
        with scene.model_picked():
            shown = scene.send(_GENERATE_PROMPT, answers=1, ready_js=cards_js(1), reload_when_asked=True)
            assert len(stub.responses) == 1, f"the run did not go on without the page that started it\n{scene.account()}"
            assert_cards_show_the_provider_images(stub, shown, "refreshed while drawing")
            assert len(scene.stored_media()) == 1, scene.account()
