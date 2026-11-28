"""Web fetch meta-tool URL guards: blocked-hostname decontamination and SSRF."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import myrm_agent_harness.toolkits.web_fetch.web_fetch_agent_tools as mod
from myrm_agent_harness.agent.errors import ToolErrorCategory
from myrm_agent_harness.toolkits.web_fetch.web_fetch_agent_tools import (
    create_web_fetch_tool,
)
from myrm_agent_harness.utils.errors import ToolError

HF_BLOCKLIST = ("huggingface.co", "*.huggingface.co", "hf.co", "*.hf.co")


def _args(urls: list[str], reason: str = "probe") -> dict[str, object]:
    return {"urls": urls, "reason": reason}


@pytest.mark.asyncio
async def test_blocked_hf_host_rejected_before_fetch() -> None:
    tool = create_web_fetch_tool(blocked_hostnames=HF_BLOCKLIST)
    with pytest.raises(ToolError) as exc_info:
        await tool.ainvoke(_args(["https://huggingface.co/datasets/leak"], "decontamination probe"))
    assert exc_info.value.error_code == "BENCHMARK_BLOCKED_HOST"


@pytest.mark.asyncio
async def test_blocked_short_alias_rejected() -> None:
    tool = create_web_fetch_tool(blocked_hostnames=HF_BLOCKLIST)
    with pytest.raises(ToolError):
        await tool.ainvoke(_args(["https://hf.co/models/x"], "decontamination probe"))


@pytest.mark.asyncio
async def test_blocked_subdomain_rejected() -> None:
    tool = create_web_fetch_tool(blocked_hostnames=HF_BLOCKLIST)
    with pytest.raises(ToolError):
        await tool.ainvoke(_args(["https://datasets-server.huggingface.co/rows"], "decontamination probe"))


@pytest.mark.asyncio
async def test_one_blocked_url_among_clean_ones_rejects_the_whole_call() -> None:
    tool = create_web_fetch_tool(blocked_hostnames=HF_BLOCKLIST)
    with patch.object(mod, "_fetch_full_content") as mock_fetch, pytest.raises(ToolError):
        await tool.ainvoke(_args(["https://example.com/a", "https://hf.co/x"]))
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_clean_url_reaches_fetch_stage() -> None:
    """A public non-blocked URL passes the blocklist; the engine handles it."""
    tool = create_web_fetch_tool(blocked_hostnames=HF_BLOCKLIST)
    # Patch the engine entry so the test never touches the network; reaching
    # _fetch_full_content means the guards let the URL through.
    with patch.object(mod, "_fetch_full_content") as mock_fetch:
        mock_fetch.return_value = {"content": "ok", "metadata": {}}
        result = await tool.ainvoke(_args(["https://example.com/article"]))
        assert result.get("content") == "ok"
        mock_fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_private_network_urls_are_refused_unless_allowed() -> None:
    tool = create_web_fetch_tool()
    with pytest.raises(ToolError, match="SSRF protection"):
        await tool.ainvoke(_args(["http://127.0.0.1:8080/admin"]))

    allowed = create_web_fetch_tool(allow_private_networks=True)
    with patch.object(mod, "_fetch_full_content") as mock_fetch:
        mock_fetch.return_value = {"content": "ok", "metadata": {}}
        await allowed.ainvoke(_args(["http://127.0.0.1:8080/admin"]))
    mock_fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_urls_carrying_secrets_are_refused() -> None:
    tool = create_web_fetch_tool()
    secret_url = "https://example.com/callback?api_key=sk-1234567890abcdef1234567890abcdef"
    with pytest.raises(ToolError, match="data exfiltration"):
        await tool.ainvoke(_args([secret_url]))


@pytest.mark.asyncio
async def test_block_error_text() -> None:
    tool = create_web_fetch_tool(blocked_hostnames=HF_BLOCKLIST)
    try:
        await tool.ainvoke(_args(["https://hf.co/x"], ""))
    except ToolError as exc:
        assert "URL blocked" in str(exc)
        assert exc.error_category == ToolErrorCategory.BENCHMARK_BLOCKED.value
    else:  # pragma: no cover
        pytest.fail("expected ToolError")
