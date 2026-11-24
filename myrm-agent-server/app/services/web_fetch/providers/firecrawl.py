"""Firecrawl v2 scrape remote fetch provider.

[INPUT]
- myrm_agent_harness.toolkits.web_fetch.escalation.protocols::EscalationFetchResult (POS: Fetch result type)
- myrm_agent_harness.core.security.guards.ssrf::async_validate_url_for_ssrf (POS: SSRF guard)
- myrm_agent_harness.core.security.http.secure_fetch::secure_request (POS: SSRF-pinned HTTP client)
- app.services.web_fetch.providers.endpoint_policy::validate_firecrawl_endpoint, resolve_and_validate_endpoint_ips (POS: L4 endpoint policy SSOT)

[OUTPUT]
- FirecrawlEscalationProvider: Fetch via Firecrawl v2 scrape API (keyless free tier or API key).

[POS]
Firecrawl v2 scrape remote fetch provider implementing FetchEscalationProvider protocol.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

import httpx
from myrm_agent_harness.core.security.guards.ssrf import SSRFSecurityError, async_validate_url_for_ssrf
from myrm_agent_harness.core.security.http.secure_fetch import secure_request
from myrm_agent_harness.toolkits.web_fetch.escalation.protocols import EscalationFetchResult

from app.services.web_fetch.providers.endpoint_policy import (
    EndpointPolicyError,
    resolve_and_validate_endpoint_ips,
    validate_firecrawl_endpoint,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(90.0, connect=15.0)


def _build_pinned_url(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, object]]:
    """Pin an endpoint URL to a pre-validated IP address with Host and TLS SNI headers."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    port_part = f":{parsed.port}" if parsed.port else ""
    ip_host = f"[{ip}]" if ":" in ip else ip
    pinned_url = urlunsplit((parsed.scheme, f"{ip_host}{port_part}", parsed.path, parsed.query, ""))
    host_header = f"{host}{port_part}"
    extensions: dict[str, object] = {"sni_hostname": host} if parsed.scheme.lower() == "https" else {}
    return pinned_url, {"Host": host_header}, extensions


class FirecrawlEscalationProvider:
    """Fetch via Firecrawl v2 scrape API (keyless free tier or API key)."""

    provider_id = "firecrawl"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_base: str | None = None,
        network_isolation_confirmed: bool = False,
    ) -> None:
        self._api_key = (api_key or "").strip() or None
        self._network_isolation_confirmed = network_isolation_confirmed
        base = validate_firecrawl_endpoint(
            api_base,
            network_isolation_confirmed=network_isolation_confirmed,
        )
        self._scrape_url = f"{base}/v2/scrape"

    async def fetch_url(self, url: str, *, max_chars: int = 0) -> EscalationFetchResult | None:
        ssrf = await async_validate_url_for_ssrf(url)
        if not ssrf.safe:
            logger.warning("Firecrawl escalation blocked (SSRF): %s — %s", url, ssrf.error)
            return None

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        payload = {"url": url, "formats": ["markdown"]}

        try:
            validated_ips = await resolve_and_validate_endpoint_ips(
                self._scrape_url,
                network_isolation_confirmed=self._network_isolation_confirmed,
            )
        except EndpointPolicyError as exc:
            logger.warning("Firecrawl endpoint blocked by policy for %s: %s", url, exc)
            return None

        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=False) as client:
                if self._network_isolation_confirmed:
                    pinned_url, host_headers, extensions = _build_pinned_url(self._scrape_url, validated_ips[0])
                    request = client.build_request(
                        "POST",
                        pinned_url,
                        json=payload,
                        headers={**headers, **host_headers},
                        extensions=extensions,
                    )
                    response = await client.send(request)
                else:
                    response = await secure_request(
                        client,
                        "POST",
                        self._scrape_url,
                        json=payload,
                        headers=headers,
                        timeout=_DEFAULT_TIMEOUT,
                        enable_ssrf_shield=True,
                    )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, SSRFSecurityError) as exc:
            logger.warning("Firecrawl escalation HTTP error for %s: %s", url, exc)
            return None
        except ValueError as exc:
            logger.warning("Firecrawl escalation invalid JSON for %s: %s", url, exc)
            return None

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None

        content = str(data.get("markdown") or data.get("content") or "").strip()
        if not content:
            return None

        metadata = data.get("metadata")
        title = ""
        if isinstance(metadata, dict):
            title = str(metadata.get("title") or "")

        if max_chars > 0 and len(content) > max_chars:
            content = content[:max_chars]

        return EscalationFetchResult(
            url=str(data.get("url") or url),
            content=content,
            title=title,
            provider_id=self.provider_id,
            is_markdown=True,
        )
