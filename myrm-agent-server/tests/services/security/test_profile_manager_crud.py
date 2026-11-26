"""ProfileManager keeps named security profiles in the database (real in-memory SQLite, no mocks).

[INPUT]
- app.services.security.profile_manager (POS: async CRUD service for security profiles)
- app.database.models.security::SecurityProfile (POS: ORM model behind the profiles API)

[OUTPUT]
- Builtin seeding and re-sync, single-active switching (an unknown key leaves the active profile alone),
  custom profile create/update/delete/clone and the guards that protect the builtin profiles.

[POS]
tests/services/security/test_profile_manager_crud.py
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.models import Base
from app.database.models.security import SecurityProfile
from app.services.security import profile_manager
from app.services.security.profile_manager import _BUILTIN_PROFILES, ProfileManager, _serialize_config

BUILTIN_KEYS = ["full_access", "readonly", "workspace"]


@pytest_asyncio.fixture
async def manager(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ProfileManager]:
    engine = create_async_engine("sqlite+aiosqlite:///file:testdb_security_profiles?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(profile_manager, "get_session_factory", lambda: session_factory)
    yield ProfileManager()
    await engine.dispose()


def _builtin_config(profile_key: str) -> dict[str, object]:
    raw = next(entry["config_json"] for entry in _BUILTIN_PROFILES if entry["profile_key"] == profile_key)
    return _serialize_config(cast("dict[str, object]", raw))


@pytest.mark.asyncio
async def test_listing_seeds_the_builtin_profiles_once(manager: ProfileManager) -> None:
    first = await manager.list_all()
    second = await manager.list_all()

    assert [p["profile_key"] for p in first] == BUILTIN_KEYS
    assert all(p["is_builtin"] is True and p["is_active"] is False for p in first)
    assert [p["id"] for p in second] == [p["id"] for p in first]


@pytest.mark.asyncio
async def test_a_drifted_builtin_is_restored_on_the_next_listing(manager: ProfileManager) -> None:
    await manager.list_all()
    async with profile_manager.get_session_factory()() as session:
        await session.execute(
            update(SecurityProfile)
            .where(SecurityProfile.profile_key == "readonly")
            .values(display_name="Drifted", config_json={"permissions": {"*": "allow"}})
        )
        await session.commit()

    restored = {p["profile_key"]: p for p in await manager.list_all()}

    assert restored["readonly"]["display_name"] == "Read Only"
    assert restored["readonly"]["config_json"] == _builtin_config("readonly")


@pytest.mark.asyncio
async def test_get_returns_the_profile_or_none(manager: ProfileManager) -> None:
    await manager.list_all()

    workspace = await manager.get("workspace")

    assert workspace is not None
    assert workspace["display_name"] == "Workspace"
    assert await manager.get("missing") is None


@pytest.mark.asyncio
async def test_activating_a_profile_deactivates_the_others(manager: ProfileManager) -> None:
    await manager.list_all()
    assert await manager.get_active() is None

    activated = await manager.activate("workspace")
    assert activated is not None
    assert activated["is_active"] is True

    await manager.activate("readonly")
    active = await manager.get_active()
    assert active is not None
    assert active["profile_key"] == "readonly"
    assert [p["profile_key"] for p in await manager.list_all() if p["is_active"]] == ["readonly"]


@pytest.mark.asyncio
async def test_activating_an_unknown_profile_keeps_the_active_one(manager: ProfileManager) -> None:
    await manager.list_all()
    await manager.activate("workspace")

    assert await manager.activate("missing") is None

    active = await manager.get_active()
    assert active is not None
    assert active["profile_key"] == "workspace"


@pytest.mark.asyncio
async def test_save_creates_then_updates_a_custom_profile(manager: ProfileManager) -> None:
    created = await manager.save("mine", "Mine", {"permissions": {"shell_exec": "ask"}}, description="first")
    assert created["is_builtin"] is False
    assert created["is_active"] is False

    updated = await manager.save("mine", "Mine v2", {"permissions": {"shell_exec": "deny"}})

    assert updated["id"] == created["id"]
    assert updated["display_name"] == "Mine v2"
    config = cast("dict[str, dict[str, str]]", updated["config_json"])
    assert config["permissions"]["shell_exec"] == "deny"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_key", BUILTIN_KEYS)
async def test_builtin_profiles_cannot_be_overwritten_or_deleted(manager: ProfileManager, profile_key: str) -> None:
    with pytest.raises(ValueError, match="Cannot overwrite builtin profile"):
        await manager.save(profile_key, "Replacement", {})
    with pytest.raises(ValueError, match="Cannot delete builtin profile"):
        await manager.delete(profile_key)


@pytest.mark.asyncio
async def test_delete_removes_a_custom_profile_and_reports_unknown_keys(manager: ProfileManager) -> None:
    await manager.save("temp", "Temp", {})

    assert await manager.delete("temp") is True
    assert await manager.get("temp") is None
    assert await manager.delete("temp") is False


@pytest.mark.asyncio
async def test_clone_copies_the_config_under_a_new_key(manager: ProfileManager) -> None:
    await manager.list_all()

    clone = await manager.clone("readonly", "my-readonly", "My Read Only")

    source = await manager.get("readonly")
    assert source is not None
    assert clone["config_json"] == source["config_json"]
    assert clone["is_builtin"] is False
    assert clone["description"] == "Cloned from 'readonly'"


@pytest.mark.asyncio
async def test_clone_of_an_unknown_source_is_rejected(manager: ProfileManager) -> None:
    with pytest.raises(ValueError, match="'missing' not found"):
        await manager.clone("missing", "copy", "Copy")


@pytest.mark.asyncio
async def test_clone_of_a_profile_without_a_config_object_is_rejected(manager: ProfileManager) -> None:
    async with profile_manager.get_session_factory()() as session:
        session.add(
            SecurityProfile(
                id="broken-id",
                profile_key="broken",
                display_name="Broken",
                config_json=cast("dict[str, object]", []),
                is_builtin=False,
                is_active=False,
            )
        )
        await session.commit()

    with pytest.raises(ValueError, match="has invalid config"):
        await manager.clone("broken", "copy", "Copy")
