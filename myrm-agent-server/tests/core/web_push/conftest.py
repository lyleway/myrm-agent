"""Fixtures of the Web Push tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.web_push.service import WebPushService


@pytest.fixture
def keyed_service(tmp_path: Path) -> WebPushService:
    """A service holding a real VAPID key pair, generated into a temporary state directory."""
    service = WebPushService()
    with patch("app.core.web_push.vapid_keys._get_vapid_dir", return_value=tmp_path):
        service._ensure_keys()
    return service
