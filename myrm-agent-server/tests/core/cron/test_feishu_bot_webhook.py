"""Tests for Feishu/Lark custom bot webhook cron delivery.

The delivery health names a failure from the wording of the error the adapter raises, so the tests that raise one also
assert the category that wording is given.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from myrm_agent_harness.api import ConnectorErrorCategory, classify_connector_error
from myrm_agent_harness.toolkits.cron.types import CronJob, DeliveryConfig, JobResult, JobType, Schedule

from app.core.cron.adapters.feishu_bot_webhook import (
    deliver_feishu_bot_webhook,
    is_feishu_bot_hook_url,
)


def test_is_feishu_bot_hook_url() -> None:
    assert is_feishu_bot_hook_url("https://open.feishu.cn/open-apis/bot/v2/hook/abc")
    assert is_feishu_bot_hook_url("https://open.larksuite.com/open-apis/bot/v2/hook/abc")
    assert not is_feishu_bot_hook_url("https://hooks.slack.com/services/T/B/X")


@pytest.mark.asyncio
async def test_deliver_feishu_bot_webhook_posts_text_payload() -> None:
    job = CronJob(
        id="job-1",
        name="daily",
        job_type=JobType.AGENT,
        prompt="brief",
        schedule=Schedule(kind="cron", expr="0 9 * * *"),
        delivery=DeliveryConfig(
            channel="webhook",
            target="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        ),
    )
    result = JobResult(success=True, output="Hello from cron")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"code": 0, "msg": "success"}
    mock_response.text = '{"code":0}'

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("app.core.cron.adapters.feishu_bot_webhook.httpx.AsyncClient", return_value=mock_client):
        await deliver_feishu_bot_webhook(job, result)

    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.await_args.kwargs
    body = call_kwargs["content"]
    assert '"msg_type": "text"' in body
    assert "Hello from cron" in body


def _client_replying(status_code: int, body: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    response.text = json.dumps(body)

    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


@pytest.mark.parametrize(
    ("status_code", "body", "category"),
    [
        pytest.param(403, {}, ConnectorErrorCategory.AUTH_FAILURE, id="http-403"),
        pytest.param(500, {}, ConnectorErrorCategory.HTTP_SERVER_ERROR, id="http-500"),
        pytest.param(
            200,
            {"code": 19024, "msg": "Key Words Not Found"},
            ConnectorErrorCategory.HTTP_CLIENT_ERROR,
            id="error-code-in-a-200-reply",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_delivery_raises_an_error_the_delivery_health_can_classify(
    status_code: int, body: dict[str, object], category: ConnectorErrorCategory
) -> None:
    job = CronJob(
        id="job-3",
        name="daily",
        job_type=JobType.AGENT,
        prompt="brief",
        schedule=Schedule(kind="cron", expr="0 9 * * *"),
        delivery=DeliveryConfig(
            channel="webhook",
            target="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        ),
    )

    with (
        patch(
            "app.core.cron.adapters.feishu_bot_webhook.httpx.AsyncClient",
            return_value=_client_replying(status_code, body),
        ),
        patch("app.core.cron.adapters.feishu_bot_webhook.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError) as raised,
    ):
        await deliver_feishu_bot_webhook(job, JobResult(success=True, output="report"))

    assert classify_connector_error(raised.value)[0] == category


@pytest.mark.asyncio
async def test_channel_delivery_routes_feishu_hook_via_bot_webhook() -> None:
    from app.core.cron.adapters.channel_delivery import ChannelResultDelivery

    job = CronJob(
        id="job-2",
        name="daily",
        job_type=JobType.AGENT,
        prompt="brief",
        schedule=Schedule(kind="cron", expr="0 9 * * *"),
        delivery=DeliveryConfig(
            channel="webhook",
            target="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        ),
    )
    result = JobResult(success=True, output="ok")

    delivery = ChannelResultDelivery()
    with patch(
        "app.core.cron.adapters.channel_delivery.deliver_feishu_bot_webhook",
        new_callable=AsyncMock,
    ) as mock_deliver:
        await delivery.deliver(job, result)
        mock_deliver.assert_awaited_once_with(job, result)
