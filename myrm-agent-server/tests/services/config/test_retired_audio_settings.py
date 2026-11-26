"""Stored audio settings that name a retired option are moved to a working one, once, without touching anything else.

[INPUT]
app.services.config.retired_audio_settings::migrate_retired_audio_settings
app.services.config.service::ConfigService/_encrypt_if_sensitive

[OUTPUT]
That the retired OpenAI text-to-speech provider (voice row and web read-aloud) and the ``whisper-1`` speech-to-text model
pin are rewritten together with their provider-only fields, that every other setting and every other model survives,
that a second run writes nothing, and that a row sealed under a lost key is left alone.

[POS]
Real ORM tables on an in-memory database with a local-mode encryption service, read back through the real ``ConfigService``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import ConfigAuditLog, UserConfig
from app.services.config import service
from app.services.config.encryption import ConfigEncryptionService
from app.services.config.retired_audio_settings import migrate_retired_audio_settings
from app.services.config.service import ConfigService

_RETIRED_VOICE: dict[str, object] = {
    "ttsMode": "always",
    "ttsProvider": "openai",
    "ttsModel": "tts-1",
    "ttsVoice": "nova",
    "ttsApiKey": "sk-tts-secret",
    "ttsBaseUrl": "http://localhost:8880/v1",
    "ttsSpeed": 1.2,
    "sttEnabled": True,
    "sttProvider": "openai",
    "sttApiKey": "sk-stt-secret",
}


def _local_encryption() -> ConfigEncryptionService:
    return ConfigEncryptionService(encryption_key=os.urandom(32), is_sandbox=False, is_local=True)


@pytest.fixture
async def sessions(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(UserConfig.__table__.create)
        await connection.run_sync(ConfigAuditLog.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    encryption = _local_encryption()
    monkeypatch.setattr(service, "get_session_factory", lambda: factory)
    monkeypatch.setattr(service, "get_encryption_service", lambda: encryption)
    yield factory
    await engine.dispose()


async def _store(sessions: async_sessionmaker[AsyncSession], key: str, value: dict[str, object]) -> None:
    stored, encrypted = service._encrypt_if_sensitive(key, value)
    db_value = {"_cipher": stored} if encrypted else value
    async with sessions() as db:
        db.add(
            UserConfig(
                id=f"id-{key}", config_key=key, config_value=db_value, is_encrypted=encrypted, version="1_0", last_device_id="d"
            )
        )
        await db.commit()


async def _raw_row(sessions: async_sessionmaker[AsyncSession], key: str) -> UserConfig:
    async with sessions() as db:
        row = await db.get(UserConfig, f"id-{key}")
    assert row is not None
    return row


async def _stored_value(key: str) -> dict[str, object]:
    record = await ConfigService().get(key)
    assert record is not None
    return record.value


async def _stored_version(key: str) -> str:
    record = await ConfigService().get(key)
    assert record is not None
    return record.version


async def test_the_retired_tts_provider_becomes_edge_and_loses_only_its_own_fields(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _store(sessions, "voice", _RETIRED_VOICE)

    assert await migrate_retired_audio_settings() == 1

    assert await _stored_value("voice") == {
        "ttsMode": "always",
        "ttsProvider": "edge",
        "ttsSpeed": 1.2,
        "sttEnabled": True,
        "sttProvider": "openai",
        "sttApiKey": "sk-stt-secret",
    }


@pytest.mark.parametrize("stt_provider", ["openai", "groq", "deepgram", "xai", "local"])
async def test_the_whisper_1_pin_is_dropped_for_every_provider(
    sessions: async_sessionmaker[AsyncSession], stt_provider: str
) -> None:
    await _store(sessions, "voice", {"sttProvider": stt_provider, "sttModel": "whisper-1", "sttLanguage": "en"})

    assert await migrate_retired_audio_settings() == 1

    assert await _stored_value("voice") == {"sttProvider": stt_provider, "sttLanguage": "en"}


@pytest.mark.parametrize("typed_model", ["gpt-4o-transcribe", "whisper-large-v3", "nova-3", ""])
async def test_a_model_the_user_chose_is_kept_and_the_row_is_not_written(
    sessions: async_sessionmaker[AsyncSession], typed_model: str
) -> None:
    await _store(sessions, "voice", {"sttProvider": "openai", "sttModel": typed_model})
    version = await _stored_version("voice")

    assert await migrate_retired_audio_settings() == 0

    assert await _stored_value("voice") == {"sttProvider": "openai", "sttModel": typed_model}
    assert await _stored_version("voice") == version


async def test_one_voice_row_with_both_retired_values_is_written_once(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _store(sessions, "voice", {**_RETIRED_VOICE, "sttModel": "whisper-1"})
    version = await _stored_version("voice")

    assert await migrate_retired_audio_settings() == 1

    migrated = await _stored_value("voice")
    assert migrated["ttsProvider"] == "edge"
    assert "sttModel" not in migrated
    assert await _stored_version("voice") != version


async def test_the_voice_row_stays_sealed_after_the_rewrite(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _store(sessions, "voice", _RETIRED_VOICE)

    await migrate_retired_audio_settings()

    row = await _raw_row(sessions, "voice")
    assert row.is_encrypted
    assert "sk-stt-secret" not in str(row.config_value)


async def test_web_read_aloud_falls_back_to_the_browser_voice(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _store(
        sessions,
        "personalSettings",
        {"timezone": "Asia/Shanghai", "webTtsProvider": "openai", "enableCompletionSound": False},
    )

    assert await migrate_retired_audio_settings() == 1

    assert await _stored_value("personalSettings") == {
        "timezone": "Asia/Shanghai",
        "webTtsProvider": "browser",
        "enableCompletionSound": False,
    }


@pytest.mark.parametrize("provider", ["browser", "elevenlabs", "edge"])
async def test_a_read_aloud_provider_that_still_exists_is_not_touched(
    sessions: async_sessionmaker[AsyncSession], provider: str
) -> None:
    await _store(sessions, "personalSettings", {"webTtsProvider": provider})

    assert await migrate_retired_audio_settings() == 0

    assert await _stored_value("personalSettings") == {"webTtsProvider": provider}


async def test_a_second_run_writes_nothing(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _store(sessions, "voice", {**_RETIRED_VOICE, "sttModel": "whisper-1"})
    await _store(sessions, "personalSettings", {"webTtsProvider": "openai"})
    assert await migrate_retired_audio_settings() == 2
    versions = (await _stored_version("voice"), await _stored_version("personalSettings"))

    assert await migrate_retired_audio_settings() == 0

    assert (await _stored_version("voice"), await _stored_version("personalSettings")) == versions


async def test_a_server_without_audio_settings_has_nothing_to_move(sessions: async_sessionmaker[AsyncSession]) -> None:
    assert await migrate_retired_audio_settings() == 0


async def test_a_row_sealed_under_a_lost_key_is_left_alone(
    sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    await _store(sessions, "voice", _RETIRED_VOICE)
    replacement = _local_encryption()
    monkeypatch.setattr(service, "get_encryption_service", lambda: replacement)
    before = (await _raw_row(sessions, "voice")).config_value

    assert await migrate_retired_audio_settings() == 0

    row = await _raw_row(sessions, "voice")
    assert row.config_value == before
    assert row.version == "1_0"
