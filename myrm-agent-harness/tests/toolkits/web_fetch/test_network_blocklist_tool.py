"""The tool hands its creation options and the user's current network blocklist to the read.

The blocklist is read on every call and given to the engine that call builds; the engine is the shared one only
while no list applies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import myrm_agent_harness.toolkits.web_fetch.web_fetch_agent_tools as tool_module
from myrm_agent_harness.toolkits.web_fetch import web_fetch_tools


def _only_read_kwargs(fetch: AsyncMock) -> dict[str, object]:
    """Keyword arguments of the single read the tool started."""
    fetch.assert_awaited_once()
    assert fetch.await_args is not None
    return fetch.await_args.kwargs


@pytest.mark.asyncio
class TestToolWiring:
    async def test_the_provider_is_read_on_every_call(self) -> None:
        lists = iter([("a.example",), ("b.example", "c.example")])
        tool = tool_module.create_web_fetch_tool(
            allow_private_networks=True, network_blocklist_provider=lambda: next(lists)
        )
        args: dict[str, object] = {"urls": ["https://example.com/"], "reason": "probe"}

        with patch.object(tool_module, "_fetch_full_content", new=AsyncMock(return_value={})) as fetch:
            await tool.ainvoke(args)
            await tool.ainvoke(args)

        assert [call.kwargs["network_blocklist"] for call in fetch.await_args_list] == [
            ("a.example",),
            ("b.example", "c.example"),
        ]

    async def test_the_creation_options_reach_the_read(self) -> None:
        tool = tool_module.create_web_fetch_tool(use_raw_markdown=True, allow_private_networks=True)

        with patch.object(tool_module, "_fetch_full_content", new=AsyncMock(return_value={})) as fetch:
            await tool.ainvoke({"urls": ["https://example.com/"], "reason": "probe"})

        read = _only_read_kwargs(fetch)
        assert read["use_raw_markdown"] is True
        assert read["allow_private_networks"] is True

    async def test_without_a_provider_no_list_applies(self) -> None:
        tool = tool_module.create_web_fetch_tool(allow_private_networks=True)

        with patch.object(tool_module, "_fetch_full_content", new=AsyncMock(return_value={})) as fetch:
            await tool.ainvoke({"urls": ["https://example.com/"], "reason": "probe"})

        assert _only_read_kwargs(fetch)["network_blocklist"] == ()

    async def test_a_list_gets_an_engine_of_its_own_and_no_list_the_shared_engine(self) -> None:
        failed = ([], [("https://example.com/", None)])

        with (
            patch("myrm_agent_harness.toolkits.web_fetch.FetchEngine") as engine_class,
            patch.object(web_fetch_tools, "crawl_many", new=AsyncMock(return_value=failed)) as shared,
        ):
            engine_class.return_value.crawl_many = AsyncMock(return_value=failed)
            await tool_module._fetch_full_content(["https://example.com/"], network_blocklist=("a.example",))
            assert engine_class.call_args.kwargs["network_blocklist"] == ("a.example",)
            shared.assert_not_awaited()

            engine_class.reset_mock()
            await tool_module._fetch_full_content(["https://example.com/"])
            engine_class.assert_not_called()
            shared.assert_awaited_once()
