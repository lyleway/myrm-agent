"""The source of an edit with a mask keeps its resolution, because a mask fits only an image of its own size.

[INPUT]
- app.ai_agents.media_tools.image_agent_tool::create_image_generation_tool (POS: the ``edit`` action under test)
- tests.unit.ai_agents.media_tools.conftest::library (POS: a real media library on a throwaway database and local storage)

[OUTPUT]
Fails when the source of a masked edit is shrunk or left sideways while its mask keeps the original size, when an edit
without a mask stops downsizing, when the mask is altered, or when a source over the engine's limit reaches the provider.

[POS]
tests/unit/ai_agents/media_tools/test_masked_edit_source.py; the media library, the reference resolver, the validator and
the result formatting are real, only the call to the image provider is replaced.
"""

from __future__ import annotations

import io
import json
import os
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from myrm_agent_harness.toolkits.llms.image.generator import ImageGenerator
from myrm_agent_harness.toolkits.llms.image.models import ImageGenerationConfig, ImageResult
from PIL import Image

from app.ai_agents.media_tools.image_agent_tool import create_image_generation_tool
from app.core.media.reference import media_reference
from tests.support.media_library_env import MediaLibraryEnv

_MODEL = "gpt-image-2.6-sunburst"
_EDITED = ImageResult(url=None, b64_json=None, revised_prompt=None, model=_MODEL, persisted_ref="media://media_edited")
_EXIF_ORIENTATION = 0x0112


@pytest.fixture
def provider_edit() -> Iterator[AsyncMock]:
    """The call that would reach the image provider: it records the image and the mask it is given."""
    with patch.object(ImageGenerator, "edit", AsyncMock(return_value=_EDITED)) as edit:
        yield edit


def _jpeg(size: tuple[int, int], *, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", size, (200, 30, 30))
    buffer = io.BytesIO()
    if orientation is None:
        image.save(buffer, format="JPEG")
    else:
        exif = image.getexif()
        exif[_EXIF_ORIENTATION] = orientation
        image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _mask(size: tuple[int, int]) -> bytes:
    """A mask the way providers want it: opaque where the image stays, transparent where it may change."""
    image = Image.new("RGBA", size, (0, 0, 0, 255))
    image.paste((0, 0, 0, 0), (0, 0, size[0] // 2, size[1] // 2))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _noisy_png(size: tuple[int, int]) -> bytes:
    """A PNG that cannot be compressed, so its byte size is that of its pixels."""
    buffer = io.BytesIO()
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buffer, format="PNG")
    return buffer.getvalue()


def _size(data: bytes | None) -> tuple[int, int]:
    assert data is not None, "there is no image"
    with Image.open(io.BytesIO(data)) as image:
        return image.size


async def _edit(library: MediaLibraryEnv, source: bytes, *, mask: bytes | None = None) -> dict[str, object]:
    arguments: dict[str, object] = {
        "action": "edit",
        "prompt": "make the sky blue",
        "image_url": media_reference((await library.store(source, content_type="image/jpeg")).id),
    }
    if mask is not None:
        arguments["mask_url"] = media_reference((await library.store(mask, content_type="image/png")).id)
    answer: dict[str, object] = json.loads(
        await create_image_generation_tool(ImageGenerationConfig(model=_MODEL)).ainvoke(arguments)
    )
    return answer


def _sent(provider_edit: AsyncMock) -> tuple[bytes, bytes | None]:
    """The image and the mask the provider call received."""
    call = provider_edit.await_args
    assert call is not None, "the provider was never called"
    return call.args[0], call.kwargs["mask"]


class TestAMaskedEdit:
    async def test_the_source_reaches_the_provider_as_it_was_stored_beside_a_mask_of_its_size(
        self, library: MediaLibraryEnv, provider_edit: AsyncMock
    ) -> None:
        source, mask = _jpeg((3000, 2000)), _mask((3000, 2000))

        answer = await _edit(library, source, mask=mask)

        assert answer["images"] == [{"url": "media://media_edited", "mime_type": "image/png"}]
        image_sent, mask_sent = _sent(provider_edit)
        assert image_sent == source
        assert _size(image_sent) == _size(mask_sent) == (3000, 2000)

    async def test_a_rotated_source_is_turned_upright_and_keeps_its_resolution(
        self, library: MediaLibraryEnv, provider_edit: AsyncMock
    ) -> None:
        await _edit(library, _jpeg((4000, 3000), orientation=6), mask=_mask((3000, 4000)))

        image_sent, mask_sent = _sent(provider_edit)
        assert _size(image_sent) == _size(mask_sent) == (3000, 4000)

    async def test_the_mask_reaches_the_provider_as_it_was_stored(
        self, library: MediaLibraryEnv, provider_edit: AsyncMock
    ) -> None:
        mask = _mask((3000, 2000))

        await _edit(library, _jpeg((3000, 2000)), mask=mask)

        assert _sent(provider_edit)[1] == mask

    async def test_a_source_over_the_limit_of_the_engine_is_an_explicit_error(
        self, library: MediaLibraryEnv, provider_edit: AsyncMock
    ) -> None:
        source = _noisy_png((2200, 1600))

        answer = await _edit(library, source, mask=_mask((2200, 1600)))

        assert str(answer["error"]).endswith("MB exceeds limit of 10MB")
        provider_edit.assert_not_awaited()


class TestAnEditWithoutAMask:
    async def test_the_source_is_still_downsized(self, library: MediaLibraryEnv, provider_edit: AsyncMock) -> None:
        source = _jpeg((3000, 2000))

        await _edit(library, source)

        image_sent, mask_sent = _sent(provider_edit)
        assert max(_size(image_sent)) <= 2048
        assert mask_sent is None

    async def test_a_source_the_engine_would_refuse_is_shrunk_until_it_fits(
        self, library: MediaLibraryEnv, provider_edit: AsyncMock
    ) -> None:
        answer = await _edit(library, _noisy_png((2200, 1600)))

        assert "error" not in answer
        assert len(_sent(provider_edit)[0]) < 4 * 1024 * 1024
