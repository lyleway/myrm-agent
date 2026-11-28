"""Wiki on the /mcp endpoint: whether an agent profile has it, and the tool bundle built from its vaults.

[INPUT]
- myrm_agent_harness.toolkits.wiki::WikiCompiler, WikiConfig, WikiQueryEngine, WikiStructure, create_wiki_agent_tools (POS: The wiki engines and their agent-facing tools) [lazy]
- app.services.agent.profile.profile_resolver::get_agent_profile_resolver, resolve_builtin_tool_flags (POS: Which built-in tools an agent profile enables) [lazy]
- app.services.wiki.vault::resolve_agent_wiki_vault_path, resolve_shared_wiki_vault_labels, resolve_shared_wiki_vault_paths (POS: Where an agent's and its shared contexts' wiki vaults live) [lazy]
- app.services.memory.shared_context.shared_context::resolve_shared_context_ids (POS: The shared contexts an agent belongs to) [lazy]
- app.services.agent.platform_config::load_platform_llm (POS: The platform's language model) [lazy]

[OUTPUT]
- wiki_boundary_enabled_for_agent: whether the agent profile enables wiki (the memory-to-wiki write boundary follows it)
- wiki_tools_for_agent: the wiki tool bundle of an agent (its query never files an answer back when the caller is read only), or None when wiki is not enabled for it

[POS]
The wiki half of the /mcp endpoint, split from ``endpoint`` so that module only routes and authenticates. The bundle is
built per request from the same vault resolution and engine factories as the in-process agent, so an external caller
sees exactly the scope the agent itself would.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


async def wiki_boundary_enabled_for_agent(agent_id: str) -> bool:
    from app.services.agent.profile.profile_resolver import (
        get_agent_profile_resolver,
        resolve_builtin_tool_flags,
    )

    resolved = await get_agent_profile_resolver().resolve(agent_id)
    if resolved is None:
        return False
    flags = resolve_builtin_tool_flags(resolved.enabled_builtin_tools)
    return bool(flags["enable_wiki"])


async def wiki_tools_for_agent(agent_id: str, *, wiki_enabled: bool, read_only: bool = False) -> dict[str, BaseTool] | None:
    """Build the wiki tool bundle for an agent (None when wiki is not enabled).

    A ``read_only`` caller gets a query that never files its answer back into the vault: the filed note carries the
    caller's question verbatim, which would be a write.

    Uses the same vault resolution functions and engine factories as the
    in-process agent path (factory.py wiki_base_dir wiring) so scope isolation
    is identical for external MCP callers. Construction failures degrade to
    None (tools hidden for that request) instead of failing the request.
    """
    if not wiki_enabled:
        return None
    try:
        from myrm_agent_harness.toolkits.wiki import (
            WikiCompiler,
            WikiConfig,
            WikiQueryEngine,
            WikiStructure,
            create_wiki_agent_tools,
        )
        from myrm_agent_harness.toolkits.wiki.retrieval.indexer import WikiIndexer

        from app.services.agent.platform_config import load_platform_llm
        from app.services.memory.shared_context.shared_context import (
            resolve_shared_context_ids,
        )
        from app.services.wiki.vault import (
            resolve_agent_wiki_vault_path,
            resolve_shared_wiki_vault_labels,
            resolve_shared_wiki_vault_paths,
        )

        shared_context_ids = await resolve_shared_context_ids(agent_id=agent_id)
        structure = WikiStructure(
            resolve_agent_wiki_vault_path(agent_id),
            public_dirs=[str(p) for p in resolve_shared_wiki_vault_paths(shared_context_ids)],
            public_dir_labels=resolve_shared_wiki_vault_labels(shared_context_ids),
        )
        structure.ensure_structure()
        config = WikiConfig()
        llm = await load_platform_llm()
        # Same vault, same naming system: route MCP-ingested concepts through
        # the same four-dimension extract prompt the in-process memory→wiki
        # archive path uses, so concepts ingested from either surface converge
        # on identical names (cross-document mention aggregation depends on
        # it) instead of forking the vault taxonomy per surface.
        from myrm_agent_harness.toolkits.wiki.core.config import WikiCompileConfig

        from app.services.wiki.daily_review.prompts import FOUR_DIMENSION_EXTRACT_PROMPT

        # Shared indexer so compilation writes the same FTS5 index the query
        # engine reads — mirrors the in-process agent path (memory_to_wiki
        # indexer wiring). Without it, MCP ingest compiles concepts but never
        # indexes them, so queries keep refusing with "no verified basis".
        indexer = WikiIndexer(structure, config)
        compiler = WikiCompiler(
            llm,
            structure,
            config,
            compile_config=WikiCompileConfig(
                extract_concepts_prompt_template=FOUR_DIMENSION_EXTRACT_PROMPT,
            ),
            indexer=indexer,
        )
        query_engine = WikiQueryEngine(llm, structure, config, indexer=indexer)
        tools = create_wiki_agent_tools(
            compiler,
            query_engine,
            structure,
            wiki_scope_id=agent_id,
            archive_queries=not read_only,
        )
        return {t.name: t for t in tools}
    except Exception as exc:
        logger.warning(
            "Wiki tools unavailable for MCP agent=%s: %s",
            agent_id,
            exc,
        )
        return None
