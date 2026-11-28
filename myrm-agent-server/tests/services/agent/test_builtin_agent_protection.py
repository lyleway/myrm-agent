"""Tests for the protection of built-in agents against deletion and against losing their built-in flag.

Tests:
1. Built-in agents cannot be deleted via AgentService; regular agents can
2. Updating a built-in agent cannot clear is_built_in, while its other fields stay editable
3. Non-built-in agents can have is_built_in set; updating a missing agent returns None
"""

import pytest
from sqlalchemy.orm import sessionmaker

from app.database.models import Agent
from app.services.agent.builtin_specs.builtin_initializer import (
    _BUILTIN_AGENTS,
    initialize_builtin_agents,
)


@pytest.mark.asyncio
async def test_delete_builtin_agent_raises_permission_error(test_db: sessionmaker):
    """Deleting a built-in agent should raise PermissionError."""
    await initialize_builtin_agents()

    from app.services.agent.agent_service import AgentService

    with pytest.raises(PermissionError, match="cannot be deleted"):
        await AgentService.delete_agent(_BUILTIN_AGENTS[0].id)


@pytest.mark.asyncio
async def test_delete_regular_agent_succeeds(test_db: sessionmaker):
    """Deleting a non-built-in agent should work normally."""
    async with test_db() as session:
        session.add(
            Agent(
                id="user-agent-123",
                name="Test Agent",
                description="A test agent",
                is_built_in=False,
                skill_ids=[],
                mcp_servers=[],
                subagent_ids=[],
                model_config={},
            )
        )
        await session.commit()

    from app.services.agent.agent_service import AgentService

    success = await AgentService.delete_agent("user-agent-123")
    assert success is True


@pytest.mark.asyncio
async def test_update_cannot_remove_builtin_flag(test_db: sessionmaker):
    """Updating a built-in agent must not allow changing is_built_in to False."""
    await initialize_builtin_agents()

    from app.database.dto import AgentUpdate
    from app.services.agent.agent_service import AgentService

    agent_id = _BUILTIN_AGENTS[0].id

    result = await AgentService.update_agent(agent_id, AgentUpdate(is_built_in=False))
    assert result is not None
    assert result.profile.built_in is True


@pytest.mark.asyncio
async def test_update_regular_agent_can_set_builtin_flag(test_db: sessionmaker):
    """Non-built-in agents CAN have is_built_in changed (e.g., promoted to built-in)."""
    async with test_db() as session:
        session.add(
            Agent(
                id="regular-agent-1",
                name="Regular Agent",
                description="A regular agent",
                is_built_in=False,
                skill_ids=[],
                mcp_servers=[],
                subagent_ids=[],
                model_config={},
            )
        )
        await session.commit()

    from app.database.dto import AgentUpdate
    from app.services.agent.agent_service import AgentService

    result = await AgentService.update_agent("regular-agent-1", AgentUpdate(is_built_in=True))
    assert result is not None
    assert result.profile.built_in is True


@pytest.mark.asyncio
async def test_update_builtin_agent_other_fields_allowed(test_db: sessionmaker):
    """Built-in agents can still have their other fields updated (e.g., name, system_prompt)."""
    await initialize_builtin_agents()

    from app.database.dto import AgentUpdate
    from app.services.agent.agent_service import AgentService

    agent_id = _BUILTIN_AGENTS[0].id

    result = await AgentService.update_agent(
        agent_id,
        AgentUpdate(
            name="Custom General",
            description="User-customized general assistant",
            system_prompt="Custom prompt",
            skill_ids=["skill-1"],
            enabled_builtin_tools=["web_search"],
            max_iterations=50,
            subagent_ids=["sub-1"],
            mcp_ids=["mcp-1"],
            security_overrides={"sandbox": True},
            workspace_policy="INHERIT_REQUESTER",
        ),
    )
    assert result is not None
    assert result.profile.display_name == "Custom General"
    assert result.profile.description == "User-customized general assistant"
    assert result.profile.built_in is True
    assert result.profile.system_prompt == "Custom prompt"
    assert result.profile.max_iterations == 50


@pytest.mark.asyncio
async def test_update_nonexistent_agent_returns_none(test_db: sessionmaker):
    """Updating a non-existent agent should return None."""
    from app.database.dto import AgentUpdate
    from app.services.agent.agent_service import AgentService

    result = await AgentService.update_agent("nonexistent-id", AgentUpdate(name="Ghost"))
    assert result is None
