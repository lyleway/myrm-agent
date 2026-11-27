"""Rewrite stored audio settings that hold a retired text-to-speech provider or speech-to-text model.

Two stored values are retired: the OpenAI text-to-speech provider (channel replies and web read-aloud), which has no
synthesis implementation and so fails on every reply, and ``whisper-1`` as the speech-to-text model, a pin that
overrides the per-provider default of ``channels/voice/stt/models.py`` although only OpenAI-compatible servers
recognise that name. The provider is switched to Edge (browser on the web) and the model pin is dropped so that
default applies.

[INPUT]
- app.services.config.service::config_service

[OUTPUT]
- migrate_retired_audio_settings: rewrite the voice and personal-settings rows that still hold a retired value

[POS]
Startup migration. Idempotent: a row without a retired value is not written. A row sealed under a lost key stays
untouched so the user can still restore it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.services.config.service import config_service

logger = logging.getLogger(__name__)

_MIGRATION_DEVICE = "retired-audio-settings"

# Fields tied to the OpenAI provider: its model, its voice names (Edge rejects them), its key, and the base URL (only the
# Voicebox provider reads a custom base URL).
_RETIRED_TTS_FIELDS = ("ttsModel", "ttsVoice", "ttsApiKey", "ttsBaseUrl")

_Migrate = Callable[[dict[str, object]], dict[str, object] | None]


def _migrate_voice(value: dict[str, object]) -> dict[str, object] | None:
    """Return the rewritten voice settings, or None when they hold no retired value."""
    migrated = dict(value)
    if migrated.get("ttsProvider") == "openai":
        migrated["ttsProvider"] = "edge"
        for field in _RETIRED_TTS_FIELDS:
            migrated.pop(field, None)
    if migrated.get("sttModel") == "whisper-1":
        migrated.pop("sttModel")
    return migrated if migrated != value else None


def _migrate_personal_settings(value: dict[str, object]) -> dict[str, object] | None:
    """Return the rewritten personal settings, or None when read-aloud does not name the retired provider."""
    if value.get("webTtsProvider") != "openai":
        return None
    return {**value, "webTtsProvider": "browser"}


async def _rewrite(config_key: str, migrate: _Migrate) -> bool:
    """Apply ``migrate`` to one stored row; True when the row was written."""
    record = await config_service.get(config_key)
    if record is None:
        return False
    if record.unreadable:
        logger.warning(
            "Skipping retired audio settings migration of '%s': the stored row cannot be decrypted with the current key",
            config_key,
        )
        return False
    migrated = migrate(record.value)
    if migrated is None:
        return False
    await config_service.set(
        config_key=config_key,
        value=migrated,
        device_id=_MIGRATION_DEVICE,
        expected_version=record.version,
    )
    logger.info("Moved stored '%s' settings off a retired audio option", config_key)
    return True


async def migrate_retired_audio_settings() -> int:
    """Rewrite every stored row that still holds a retired audio value; returns how many rows were written."""
    rewritten = [
        await _rewrite("voice", _migrate_voice),
        await _rewrite("personalSettings", _migrate_personal_settings),
    ]
    return sum(rewritten)
