"""FetchEngine L4 escalation and platform fast-path helpers.

[POS]
Mixin: remote fetch escalation after local L1 HTTP failure; bilibili cookie loader.
A remote provider follows redirects on its own servers, so the network blocklist can only be judged on the URL it
reports back; a provider that reports a listed host ends the fetch for every provider, since the redirect chain
belongs to the URL, not to the provider.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langchain_core.documents import Document

from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError

from ..escalation.content_validator import is_valid_escalation_content
from ..fetchers.protocols import FetcherType, FetchResult
from ..fetchers.target_guard import assert_not_blocklisted

if TYPE_CHECKING:
    from ..escalation.protocols import FetchEscalationProvider
    from ..fetchers.http_fetcher import HttpFetcher
    from ..processing.pipeline import ContentPipeline

logger = logging.getLogger(__name__)


class FetchEngineEscalationMixin:
    if TYPE_CHECKING:
        _http_fetcher: HttpFetcher
        _escalation_providers: list[FetchEscalationProvider] | None
        _network_blocklist: tuple[str, ...]
        _pipeline: ContentPipeline

    async def _load_bilibili_cookies(self) -> dict[str, str] | None:
        """Load bilibili.com cookies from SessionVault for subtitle API access."""
        vault = self._http_fetcher._session_vault
        if not vault:
            return None
        try:
            entry = await vault.load("bilibili.com")
            if not entry or not entry.storage_state or "cookies" not in entry.storage_state:
                return None
            return {c["name"]: c["value"] for c in entry.storage_state["cookies"] if "name" in c and "value" in c}
        except Exception:
            return None

    async def _try_escalation(self, url: str) -> tuple[Document | None, FetchResult | None]:
        """Try injected remote providers after local L1 HTTP failure."""
        from ..escalation.context import get_bound_escalation_providers

        providers = get_bound_escalation_providers() or self._escalation_providers
        if not providers:
            return None, None

        from ..escalation.metrics import web_fetch_escalation_metrics

        web_fetch_escalation_metrics.record_triggered()

        for provider in providers:
            try:
                escalation_result = await provider.fetch_url(url)
            except Exception as exc:
                logger.warning(
                    "Escalation provider %s failed for %s: %s",
                    provider.provider_id,
                    url,
                    exc,
                )
                continue

            if escalation_result is None or not escalation_result.content.strip():
                continue

            valid_content, reject_reason = is_valid_escalation_content(
                escalation_result.content,
                title=escalation_result.title,
            )
            if not valid_content:
                logger.info(
                    "Escalation provider %s content rejected for %s (%s)",
                    provider.provider_id,
                    url,
                    reject_reason,
                )
                continue

            try:
                assert_not_blocklisted(escalation_result.url or url, self._network_blocklist)
            except SSRFSecurityError:
                web_fetch_escalation_metrics.record_failure()
                raise

            doc: Document | None
            if escalation_result.is_markdown:
                doc = Document(
                    page_content=escalation_result.content,
                    metadata={
                        "url": escalation_result.url or url,
                        "title": escalation_result.title,
                        "escalation_provider": provider.provider_id,
                    },
                )
            else:
                remote_fetch = FetchResult(
                    html=escalation_result.content,
                    url=escalation_result.url or url,
                    fetcher_type=FetcherType.HTTP,
                )
                doc = await self._pipeline.aprocess(remote_fetch)
                if doc is None:
                    continue
                doc.metadata["escalation_provider"] = provider.provider_id

            web_fetch_escalation_metrics.record_success()
            logger.info("Escalation provider %s succeeded for %s", provider.provider_id, url)
            return doc, None

        web_fetch_escalation_metrics.record_failure()
        return None, None
