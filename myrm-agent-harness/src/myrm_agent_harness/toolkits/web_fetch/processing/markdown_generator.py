"""Markdown generator

Converts HTML to Markdown and, when a content filter is configured, also
produces the filtered ("fit") variant that keeps only the main content.

[INPUT]
- processing.html_to_markdown::HTML2Markdown (POS: HTML-to-Markdown converter) [lazy]

[OUTPUT]
- MarkdownResult: raw and fit Markdown variants
- ContentFilter: content filter protocol
- MarkdownGenerator: HTML → Markdown generator with an optional content filter

[POS]
Markdown generation stage of ContentPipeline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MarkdownResult:
    """Result of one HTML → Markdown conversion."""

    raw_markdown: str = ""
    fit_markdown: str = ""


@runtime_checkable
class ContentFilter(Protocol):
    """Content filter protocol."""

    def filter_content(self, html: str) -> list[str]: ...


class MarkdownGenerator:
    """HTML → Markdown generator with an optional content filter."""

    def __init__(
        self,
        content_filter: ContentFilter | None = None,
        options: dict[str, object] | None = None,
    ):
        self.content_filter = content_filter
        self.options = options or {}

    def generate_markdown(
        self,
        input_html: str,
        base_url: str = "",
        html2text_options: dict[str, object] | None = None,
        content_filter: ContentFilter | None = None,
    ) -> MarkdownResult:
        from .html_to_markdown import HTML2Markdown

        try:
            h = HTML2Markdown(baseurl=base_url)
            default_options: dict[str, object] = {
                "body_width": 0,
                "ignore_emphasis": False,
                "ignore_links": False,
                "ignore_images": False,
                "protect_links": False,
                "single_line_break": False,
                "mark_code": False,
                "escape_snob": False,
                "skip_internal_links": True,
            }

            if html2text_options:
                default_options.update(html2text_options)
            elif self.options:
                default_options.update(self.options)

            h.update_params(**default_options)

            if not input_html:
                input_html = ""

            try:
                raw_markdown = h.handle(input_html)
            except Exception:
                logger.warning("HTML-to-Markdown conversion failed for base_url=%s", base_url, exc_info=True)
                raw_markdown = ""

            raw_markdown = raw_markdown.replace("    ```", "```")

            fit_markdown: str = ""
            active_filter = content_filter or self.content_filter
            if active_filter:
                try:
                    filtered_segments = active_filter.filter_content(input_html)
                    filtered_html = "\n".join(f"<div>{s}</div>" for s in filtered_segments)
                    fit_markdown = h.handle(filtered_html)
                except Exception:
                    logger.warning("Fit Markdown generation failed for base_url=%s", base_url, exc_info=True)
                    fit_markdown = ""

            return MarkdownResult(
                raw_markdown=raw_markdown or "",
                fit_markdown=fit_markdown or "",
            )
        except Exception:
            logger.warning("Markdown generation setup failed for base_url=%s", base_url, exc_info=True)
            return MarkdownResult()
