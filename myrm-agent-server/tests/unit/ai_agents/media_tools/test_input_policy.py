"""Tests for the reference-resolution policy shared by the media tools and task executors."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from myrm_agent_harness.toolkits.llms.media_inputs import MediaInputError, MediaInputPolicy, resolve_media_inputs
from PIL import Image

from app.ai_agents.media_tools.input_policy import build_media_input_policy, keep_image_size
from app.core.media.reference import MEDIA_SCHEME, read_media_reference

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_EXIF_ORIENTATION = 0x0112


def test_the_policy_reads_media_references_from_the_library_and_downsizes_images() -> None:
    policy = build_media_input_policy()

    assert policy.loaders == {MEDIA_SCHEME: read_media_reference}
    assert policy.image_hook is not None
    assert policy.allowed_roots == ()


def test_the_policy_names_the_workspace_folders_it_may_read() -> None:
    policy = build_media_input_policy(["/work/chat-1", "/work/shared"])

    assert policy.allowed_roots == (Path("/work/chat-1"), Path("/work/shared"))


@pytest.mark.parametrize(("local_mode", "expected"), [(True, True), (False, False)])
def test_only_local_deployments_may_reach_private_networks(local_mode: bool, expected: bool) -> None:
    with patch("app.ai_agents.media_tools.input_policy.is_local_mode", return_value=local_mode):
        assert build_media_input_policy().allow_private_networks is expected


@pytest.mark.asyncio
async def test_a_path_outside_the_workspace_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "secret.png"
    outside.parent.mkdir()
    outside.write_bytes(_PNG)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(MediaInputError, match="image_url"):
        await resolve_media_inputs([str(outside)], "image", build_media_input_policy([str(workspace)]), field_name="image_url")


@pytest.mark.asyncio
async def test_a_missing_library_item_names_the_field() -> None:
    with (
        patch("app.ai_agents.media_tools.input_policy.read_media_reference", AsyncMock(side_effect=LookupError("gone"))),
        pytest.raises(MediaInputError, match="reference_images"),
    ):
        await resolve_media_inputs(["media://media_gone"], "image", build_media_input_policy(), field_name="reference_images")


def _image(
    size: tuple[int, int],
    *,
    mode: str = "RGB",
    color: tuple[int, ...] = (200, 30, 30),
    fmt: str = "JPEG",
    orientation: int | None = None,
) -> bytes:
    image = Image.new(mode, size, color)
    buffer = io.BytesIO()
    if orientation is None:
        image.save(buffer, format=fmt)
    else:
        exif = image.getexif()
        exif[_EXIF_ORIENTATION] = orientation
        image.save(buffer, format=fmt, exif=exif)
    return buffer.getvalue()


def _hook(policy: MediaInputPolicy) -> Callable[[bytes], bytes]:
    assert policy.image_hook is not None
    return policy.image_hook


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


class TestKeepImageSize:
    def test_a_large_image_passes_unchanged_where_the_default_hook_would_shrink_it(self) -> None:
        source = _image((3000, 2000))
        policy = build_media_input_policy()

        assert _hook(keep_image_size(policy))(source) == source
        assert max(_size(_hook(policy)(source))) <= 2048

    def test_a_rotated_image_is_turned_upright_and_keeps_its_resolution(self) -> None:
        source = _image((4000, 3000), orientation=6)

        assert _size(_hook(keep_image_size(build_media_input_policy()))(source)) == (3000, 4000)

    def test_transparency_is_still_flattened_onto_white(self) -> None:
        source = _image((64, 64), mode="RGBA", color=(0, 0, 0, 0), fmt="PNG")

        sanitized = _hook(keep_image_size(build_media_input_policy()))(source)

        with Image.open(io.BytesIO(sanitized)) as image:
            assert image.mode == "RGB"
            assert image.getpixel((0, 0)) == (255, 255, 255)

    def test_only_the_image_hook_differs_from_the_policy_it_derives_from(self) -> None:
        policy = build_media_input_policy(["/work/chat-1"])

        derived = keep_image_size(policy)

        assert derived.image_hook is not policy.image_hook
        assert (derived.allowed_roots, derived.allow_private_networks, derived.loaders) == (
            policy.allowed_roots,
            policy.allow_private_networks,
            policy.loaders,
        )
