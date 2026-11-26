"""Move stored audio settings off options the product no longer offers.

Two stored values stop being valid: the OpenAI text-to-speech provider (channel replies and web read-aloud), and
``whisper-1`` as the speech-to-text model, which older settings pages wrote into every saved voice row although the user
never chose it. Left in place the first keeps failing on every reply and the second is sent to providers that do not
know the name.

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

# Fields that only made sense for the removed provider: its model, its voice names (Edge rejects them), its key, and the
# OpenAI-compatible endpoint that is now only meaningful as a Voicebox address.
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
