"""A fast turn is a one-shot answer run: it mounts no built-in tool toggle, whatever the saved profile lists.

The only built-in capability on that surface is the read-only file access over spill files and uploads that the
surface itself grants.
"""

from __future__ import annotations

import pytest
from myrm_agent_harness.agent.meta_tools.mount_policy import FileAccessMode
from myrm_agent_harness.toolkits.web_search import SearchServiceConfig

from app.services.agent.params.conversion import ConfigSources, ProfileContext, resolve_tool_surface
from app.services.agent.params.models import AgentRequest


def _sources_with_search() -> ConfigSources:
    """Sources whose search service is already resolved, so surface resolution reloads no configuration."""
    return ConfigSources(
        search_cfg=SearchServiceConfig(search_service="tavily", api_key="test-key"),
        search_is_user_configured=True,
        embedding_cfg=None,
        reranker_cfg=None,
        mcp_configs=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("search_depth", ["normal", "deep"])
async def test_a_fast_turn_mounts_no_builtin_toggle_even_when_the_profile_lists_them(search_depth: str) -> None:
    profile = ProfileContext(enabled_builtin_tools=["web_search", "code_execute", "browser", "cron"])
    request = AgentRequest(
        message_id="m-1",
        chat_id="c-1",
        query="Latest Python release?",
        action_mode="fast",
        search_depth=search_depth,
    )

    surface = await resolve_tool_surface(request, profile, _sources_with_search())

    flags = surface.tool_flags
    assert surface.is_fast
    assert surface.search_depth == search_depth
    assert flags["enable_shell_tools"] is False
    assert flags["enable_browser"] is False
    assert flags["enable_cron_eager"] is False
    assert flags["file_access_mode"] == FileAccessMode.SPILL_AND_UPLOADS
