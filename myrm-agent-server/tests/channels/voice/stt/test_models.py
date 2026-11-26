"""The model an empty setting resolves to, and the language field each model family takes."""

from __future__ import annotations

import pytest

from app.channels.voice.stt import default_stt_model
from app.channels.voice.stt.models import language_fields


class TestDefaultSttModel:
    @pytest.mark.parametrize(
        ("provider", "expected"),
        [("openai", "gpt-transcribe"), ("groq", "whisper-large-v3"), ("deepgram", "nova-3"), ("some-vendor", "gpt-transcribe")],
    )
    def test_each_provider_runs_its_own_default(self, provider: str, expected: str) -> None:
        assert default_stt_model(provider) == expected

    def test_a_custom_endpoint_gets_the_name_openai_compatible_servers_recognise(self) -> None:
        assert default_stt_model("openai", custom_endpoint=True) == "whisper-1"

    @pytest.mark.parametrize(("provider", "expected"), [("groq", "whisper-large-v3"), ("deepgram", "nova-3")])
    def test_a_vendors_own_default_does_not_depend_on_the_endpoint(self, provider: str, expected: str) -> None:
        assert default_stt_model(provider, custom_endpoint=True) == expected


class TestLanguageFields:
    @pytest.mark.parametrize("model", ["gpt-transcribe", "gpt-transcribe-mini"])
    def test_the_gpt_transcribe_family_takes_a_lower_case_language_list(self, model: str) -> None:
        assert language_fields(model, "ZH-CN") == {"languages[]": ["zh-cn"]}

    @pytest.mark.parametrize("model", ["whisper-1", "whisper-large-v3", "gpt-4o-transcribe", "nova-3"])
    def test_every_other_model_takes_the_single_language_field_as_typed(self, model: str) -> None:
        assert language_fields(model, "zh") == {"language": "zh"}

    @pytest.mark.parametrize("model", ["gpt-transcribe", "whisper-1"])
    @pytest.mark.parametrize("language", [None, ""])
    def test_no_language_sends_no_field(self, model: str, language: str | None) -> None:
        assert language_fields(model, language) == {}
