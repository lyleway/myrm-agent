"""Request text sanitizer middleware.

Strips control characters and invalid Unicode from inbound user text:
1. Query parameters (all methods)
2. JSON bodies (POST/PUT/PATCH; ``application/json`` and ``application/*+json``)
3. URL-encoded form bodies (POST/PUT/PATCH)

Only the harness ``strip_control_chars`` primitive runs here. Markup such as
``<think>`` or ``<function_calls>`` is user content (a pasted parser snippet, a
system prompt) and passes through unchanged; the tag filters of
``sanitize_text`` belong to the model-output pipeline.

Pure ASGI: the sanitized body is served on ``receive`` (``app.middleware.asgi_body``), so every layer below
reads it, and the caller's scope is never modified.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from urllib.parse import parse_qs, urlencode

from myrm_agent_harness.utils.text_sanitizer import strip_control_chars
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.middleware.asgi_body import METHODS_WITH_BODY, describe_replaced_body, read_request_body, replay_request_body

logger = logging.getLogger(__name__)

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]

_FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"


def _media_type(content_type: str) -> str:
    """Return the lower-cased media type without parameters such as ``charset``."""
    return content_type.split(";", 1)[0].strip().lower()


def _is_json_media_type(media_type: str) -> bool:
    """Mirror FastAPI's JSON body detection: ``application/json`` and ``application/*+json``."""
    return media_type == "application/json" or (media_type.startswith("application/") and media_type.endswith("+json"))


def _sanitize_json(value: JsonValue) -> JsonValue:
    """Strip control characters from every string value, at any nesting depth."""
    if isinstance(value, str):
        return strip_control_chars(value)
    if isinstance(value, list):
        return [_sanitize_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_json(item) for key, item in value.items()}
    return value


def _sanitize_urlencoded(raw: bytes) -> bytes:
    """Strip control characters from the values of a URL-encoded query string or form body."""
    parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    cleaned = {key: [strip_control_chars(value) for value in values] for key, values in parsed.items()}
    return urlencode(cleaned, doseq=True).encode("utf-8")


def _sanitize_json_body(raw: bytes) -> bytes:
    """The body with every string cleaned; bodies that are not JSON containers pass through as they are."""
    if not raw:
        return raw
    try:
        data: JsonValue = json.loads(raw)
        if not isinstance(data, dict | list):
            return raw
        return json.dumps(_sanitize_json(data)).encode("utf-8")
    except (ValueError, RecursionError) as exc:
        # Malformed, over-long integer or too deeply nested: the route's own validation reports it.
        logger.warning("Skipping JSON body sanitization: %s", exc)
        return raw


def _sanitize_form_body(raw: bytes) -> bytes:
    return _sanitize_urlencoded(raw) if raw else raw


def _body_sanitizer(media_type: str) -> Callable[[bytes], bytes] | None:
    if _is_json_media_type(media_type):
        return _sanitize_json_body
    if media_type == _FORM_MEDIA_TYPE:
        return _sanitize_form_body
    return None


class TextSanitizerMiddleware:
    """Strip control characters from query strings and JSON / form bodies before routing.

    Defense in depth against corrupted input that would break JSON
    serialization, database writes or WebSocket frames. Removes C0/C1 control
    characters, unpaired surrogates, U+FFFD and model control tokens
    (``<|…|>``); everything else, tags included, is left as the user wrote it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        query_string = scope.get("query_string", b"")
        if query_string:
            scope = {**scope, "query_string": _sanitize_urlencoded(query_string)}

        sanitize = _body_sanitizer(_media_type(Headers(scope=scope).get("content-type", "")))
        if sanitize is not None and scope["method"] in METHODS_WITH_BODY:
            raw = await read_request_body(receive)
            cleaned = sanitize(raw)
            if cleaned != raw:
                scope = describe_replaced_body(scope, cleaned)
            receive = replay_request_body(cleaned, receive)

        await self.app(scope, receive, send)
