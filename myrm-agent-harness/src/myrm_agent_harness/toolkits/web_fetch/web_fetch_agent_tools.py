"""Web fetch meta-tool

Contains:
1. create_web_fetch_tool: Web fetch tool (reads known URLs in full)

[INPUT]
- toolkits.web_fetch.processing.full_content_delivery::FetchedPage, plan_full_content, all_failed_content (POS: One shared budget for every fetched page) [lazy]
- toolkits.web_fetch.processing.spill::store_full_text, emit_web_fetch_evicted_ref, stored_copy_capacity (POS: Sandbox persistence of the full text) [lazy]
- toolkits.web_fetch.processing.content_sanitize::strip_base64_images_from_markdown (POS: Inline image payload removal) [lazy]
- utils.context_format::wrap_with_external_sources_tag (POS: UNTRUSTED_DATA boundary around fetched content) [lazy]
- core.context_vars::source_indexer_var (POS: the run's source numbering, so the numbers a result prints are the indexes of its cards)
- toolkits.web_fetch._url_gate::validate_urls (POS: canonical spelling, blocklist, SSRF and exfiltration gate before any request)
- toolkits.web_fetch._web_fetch_tool_description::resolve_web_fetch_tool_description
- utils.errors::ToolError

[OUTPUT]
- create_web_fetch_tool: Create web fetch meta-tool

[POS]
Web fetch meta-tool: one input, ``(urls, reason)``, and one behaviour, reading the known URLs in full. The user's
network blocklist is read on every call and handed to the engine that call builds, so a list edited mid-session
applies to the next fetch and the shared engine's caches never hold a page fetched under another list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain.tools import BaseTool, tool
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

    from myrm_agent_harness.toolkits.web_fetch.processing.spill import StoredFullText

from myrm_agent_harness.core.context_vars import source_indexer_var
from myrm_agent_harness.toolkits.web_fetch._url_gate import validate_urls
from myrm_agent_harness.toolkits.web_fetch._web_fetch_tool_description import resolve_web_fetch_tool_description
from myrm_agent_harness.utils.errors import ToolError

_MAX_URLS_PER_FULL_READ = 5
_FULL_READ_URLS_DESCRIPTION = f"Real webpage URLs (up to {_MAX_URLS_PER_FULL_READ}); never invent URLs"
_REASON_DESCRIPTION = "State your reasoning, express key info with minimal tokens, max 100 chars"


class _FullContentInput(BaseModel):
    urls: list[str] = Field(description=_FULL_READ_URLS_DESCRIPTION, min_length=1, max_length=_MAX_URLS_PER_FULL_READ)
    reason: str = Field(description=_REASON_DESCRIPTION)


def create_web_fetch_tool(
    *,
    use_raw_markdown: bool = False,
    allow_private_networks: bool = False,
    blocked_hostnames: tuple[str, ...] | None = None,
    network_blocklist_provider: Callable[[], tuple[str, ...]] | None = None,
    description_locale: str | None = None,
) -> BaseTool:
    """Create web fetch meta-tool: it reads the given pages (``urls``) in full.

    ``blocked_hostnames`` is an optional hostname blocklist (exact or ``*.``
    wildcard patterns). When set, URLs whose host matches a pattern are
    rejected before fetching — a generic content policy hook callers can use
    for benchmark decontamination or domain filtering. None disables it.

    ``network_blocklist_provider`` returns the user's current network blocklist (exact or ``*.``
    wildcard hosts) and is called on every tool call. A listed host is never read: the requested
    URL is refused, and so is a redirect that leads to one (L1 HTTP judges every hop before
    requesting it, and L4 remote escalation judges the final URL reported by the provider).

    ``description_locale`` selects the LLM-facing tool description locale
    (default English).
    """
    tool_description = resolve_web_fetch_tool_description(description_locale)

    @tool("web_fetch_tool", description=tool_description, args_schema=_FullContentInput)
    async def full_content_tool(urls: list[str], reason: str = "") -> dict[str, object]:
        """Read the pages in full."""
        return await _fetch_full_content(
            await validate_urls(
                urls, blocked_hostnames=blocked_hostnames, allow_private_networks=allow_private_networks
            ),
            use_raw_markdown=use_raw_markdown,
            allow_private_networks=allow_private_networks,
            network_blocklist=network_blocklist_provider() if network_blocklist_provider is not None else (),
        )

    return full_content_tool


async def _fetch_full_content(
    urls: list[str],
    *,
    use_raw_markdown: bool = False,
    allow_private_networks: bool = False,
    network_blocklist: tuple[str, ...] = (),
) -> dict[str, object]:
    """Read the pages in full: each keeps its share of one budget and the rest stays one file read away."""
    from myrm_agent_harness.toolkits.web_fetch import FetchEngine, web_fetch_tools
    from myrm_agent_harness.toolkits.web_fetch.processing.content_sanitize import strip_base64_images_from_markdown
    from myrm_agent_harness.toolkits.web_fetch.processing.full_content_delivery import (
        FetchedPage,
        all_failed_content,
        plan_full_content,
    )
    from myrm_agent_harness.toolkits.web_fetch.processing.pipeline import SOURCE_TRUNCATED_KEY
    from myrm_agent_harness.toolkits.web_fetch.processing.spill import (
        emit_web_fetch_evicted_ref,
        store_full_text,
        stored_copy_capacity,
    )
    from myrm_agent_harness.utils.context_format import wrap_with_external_sources_tag

    need_custom_engine = use_raw_markdown or allow_private_networks or bool(network_blocklist)
    engine = (
        FetchEngine(
            use_raw_markdown=use_raw_markdown,
            allow_private_networks=allow_private_networks,
            network_blocklist=network_blocklist,
            session_vault=web_fetch_tools._http_fetcher._session_vault,
        )
        if need_custom_engine
        else web_fetch_tools
    )
    # The model reads a budgeted preview and the stored copy holds the rest, so a long PDF or Office file is parsed up
    # to what that copy can keep; without a store the cut preview is all there is.
    success_results, failed_results = await engine.crawl_many(urls, max_parsed_chars=stored_copy_capacity())

    if not success_results and not failed_results:
        raise ToolError(
            "No results returned from crawl",
            user_hint="The URLs may be unreachable or blocked. Verify the URLs are correct and publicly accessible.",
        )

    failed_urls = [url for url, _ in failed_results]
    failure_reasons: dict[str, tuple[str, str]] = {}
    if hasattr(engine, "get_failure_reason"):
        for failed_url in failed_urls:
            reason = engine.get_failure_reason(failed_url)
            if reason is not None:
                failure_reasons[failed_url] = reason

    if not success_results:
        return _full_content_result(
            all_failed_content(failed_urls, failure_reasons=failure_reasons or None),
            sources=[],
            truncated=False,
            raw_chars=0,
        )

    sources: list[dict[str, object]] = []
    pages: list[FetchedPage] = []
    for url, doc in success_results:
        page_url = doc.metadata.get("url", url)
        title = doc.metadata.get("title", "")
        sources.append({"url": page_url, "title": title})
        pages.append(
            FetchedPage(
                url=str(page_url),
                title=str(title or ""),
                body=strip_base64_images_from_markdown(doc.page_content),
                source_truncated=bool(doc.metadata.get(SOURCE_TRUNCATED_KEY)),
            )
        )
    index_for = source_indexer_var.get()
    delivery = plan_full_content(
        pages,
        failed_urls,
        indices=[index_for(source) for source in sources] if index_for is not None else None,
        failure_reasons=failure_reasons or None,
    )

    stored = None
    if delivery.stored_text is not None:
        stored = await store_full_text(delivery.stored_text, delivery.read_ranges)

    content = wrap_with_external_sources_tag(delivery.visible_text, source="web_fetch")
    # Outside the untrusted boundary: the guide and both notices are ours, not page content.
    if stored is not None:
        content += stored.read_guide
        await emit_web_fetch_evicted_ref(
            stored.evicted_ref,
            stored_chars=stored.stored_chars,
            total_lines=stored.total_lines,
            storage_truncated=stored.storage_truncated,
        )
    content += delivery.source_notice + delivery.failure_notice
    return _full_content_result(
        content,
        sources=sources,
        truncated=delivery.truncated,
        raw_chars=delivery.raw_chars,
        stored=stored,
    )


def _full_content_result(
    content: str,
    *,
    sources: list[dict[str, object]],
    truncated: bool,
    raw_chars: int,
    stored: StoredFullText | None = None,
) -> dict[str, object]:
    return {
        "content": content,
        "metadata": {
            "sources": sources,
            "truncated": truncated,
            "raw_chars": raw_chars,
        },
        "evicted_ref": stored.evicted_ref if stored else None,
        "evicted_stored_chars": stored.stored_chars if stored else None,
        "evicted_total_lines": stored.total_lines if stored else None,
        "evicted_storage_truncated": stored.storage_truncated if stored else False,
    }
