"""The local whisper model's lifecycle: idle unloading, explicit unloading and the missing-dependency error."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.channels.voice.stt.local import LOCAL_STT_INSTALL_HINT, _LocalWhisperManager
from tests.channels.voice.stt.stt_testkit import stt_config

_CPU_CONFIG = stt_config("local", local_model="tiny", local_device="cpu", local_compute_type="int8")
_UNLOADED = {"model_size": "", "device": "", "compute_type": ""}


@pytest.fixture(autouse=True)
def _without_torch() -> Iterator[None]:
    """Unloading probes torch for a GPU cache; importing the real package would cost seconds and depend on the machine."""
    with patch.dict("sys.modules", {"torch": None}):
        yield


async def _loaded_manager(idle_unload_seconds: float = 60.0) -> _LocalWhisperManager:
    manager = _LocalWhisperManager(idle_unload_seconds=idle_unload_seconds)
    with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=lambda *a, **kw: MagicMock())}):
        await manager.get_model(_CPU_CONFIG)
    return manager


class TestIdleUnloading:
    async def test_a_zero_idle_timeout_never_schedules_an_unload(self) -> None:
        manager = _LocalWhisperManager(idle_unload_seconds=0)

        manager._schedule_unload()

        assert manager._unload_timer is None

    async def test_scheduling_again_replaces_the_pending_unload(self) -> None:
        manager = _LocalWhisperManager(idle_unload_seconds=60)

        manager._schedule_unload()
        first = manager._unload_timer
        manager._schedule_unload()
        second = manager._unload_timer
        manager.unload()

        assert first is not None
        assert first.cancelled()
        assert second is not None
        assert second is not first

    async def test_an_idle_model_is_unloaded_when_the_timer_fires(self) -> None:
        manager = await _loaded_manager(idle_unload_seconds=0.001)

        manager._schedule_unload()
        await asyncio.sleep(0.01)

        assert not manager.is_loaded
        assert manager._unload_timer is None

    async def test_loading_a_model_cancels_a_pending_unload(self) -> None:
        manager = await _loaded_manager()
        manager._schedule_unload()
        pending = manager._unload_timer

        await manager.get_model(_CPU_CONFIG)

        assert pending is not None
        assert pending.cancelled()
        assert manager._unload_timer is None
        manager.unload()


class TestUnload:
    async def test_the_model_and_its_settings_are_released(self) -> None:
        manager = await _loaded_manager()
        manager._schedule_unload()
        timer = manager._unload_timer

        manager.unload()

        assert not manager.is_loaded
        assert manager.current_config == _UNLOADED
        assert timer is not None
        assert timer.cancelled()
        assert manager._unload_timer is None

    @pytest.mark.parametrize("cuda_present", [True, False], ids=["cuda", "cpu only"])
    async def test_the_gpu_cache_is_emptied_only_when_cuda_is_present(self, cuda_present: bool) -> None:
        manager = await _loaded_manager()
        torch = MagicMock()
        torch.cuda.is_available.return_value = cuda_present

        with patch.dict("sys.modules", {"torch": torch}):
            manager.unload()

        assert torch.cuda.empty_cache.called is cuda_present

    async def test_unloading_without_torch_still_releases_the_model(self) -> None:
        manager = await _loaded_manager()

        with patch.dict("sys.modules", {"torch": None}):
            manager.unload()

        assert not manager.is_loaded

    def test_unloading_a_manager_that_holds_nothing_changes_nothing(self) -> None:
        manager = _LocalWhisperManager()

        manager.unload()

        assert not manager.is_loaded
        assert manager.current_config == _UNLOADED


class TestMissingDependency:
    async def test_loading_without_faster_whisper_names_the_install_command(self) -> None:
        manager = _LocalWhisperManager()

        with patch.dict("sys.modules", {"faster_whisper": None}):
            with pytest.raises(ImportError, match=re.escape(LOCAL_STT_INSTALL_HINT)):
                await manager.get_model(_CPU_CONFIG)

        assert not manager.is_loaded
