"""ASGI middleware: decrypt E2EE mobile remote requests before auth handlers.

[INPUT]
- ASGI http scope with an optional X-E2EE-Session header
- app.middleware.asgi_body (POS: read and replay a request body on the receive channel)

[OUTPUT]
- The pair token and the body of the request in plaintext, for every layer below
- scope state ``e2ee_session_id`` / ``e2ee_session`` / ``e2ee_pair_token`` (read as ``request.state``)

[POS]
Middleware layer. Sits before auth handlers in the ASGI pipeline. It is pure ASGI on purpose: the decrypted body
is served on ``receive``, so the layers below (BaseHTTPMiddleware ones included) and the route read plaintext.
"""

from __future__ import annotations

import json
import logging

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.middleware.asgi_body import METHODS_WITH_BODY, describe_replaced_body, read_request_body, replay_request_body
from app.remote_access.e2ee import (
    E2EE_CONTENT_TYPE,
    E2EE_PAIR_CIPHERTEXT_HEADER,
    E2EE_PAIR_QUERY_PARAM,
    E2EE_SESSION_HEADER,
    E2EECryptoError,
    E2EESession,
    get_e2ee_session_store,
    parse_encrypted_body,
)
from app.remote_access.mobile_gate import is_mobile_remote_control_path, is_mobile_remote_pairing_path

logger = logging.getLogger(__name__)

_PLAINTEXT_CONTENT_TYPE = "application/json"


def _stripped(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _plaintext_of(raw: bytes, content_type: str, session: E2EESession) -> bytes | None:
    """The decrypted body, or None when the body is not an encrypted envelope and must pass through as it is."""
    if not raw:
        return None
    if E2EE_CONTENT_TYPE in content_type:
        return session.decrypt_text(parse_encrypted_body(raw)).encode("utf-8")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("c"), str):
        return session.decrypt_text(payload["c"]).encode("utf-8")
    return None


class E2EEMiddleware:
    """Decrypt pair tokens and JSON bodies for established E2EE mobile sessions."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if not (is_mobile_remote_control_path(path) or is_mobile_remote_pairing_path(path)):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        store = get_e2ee_session_store()
        session = await store.get(_stripped(headers.get(E2EE_SESSION_HEADER)))
        if session is None:
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        state["e2ee_session_id"] = session.session_id
        state["e2ee_session"] = session

        try:
            self._decrypt_pair_token(scope, headers, session)
            scope, receive = await self._decrypt_body(scope, receive, headers, session)
        except E2EECryptoError as exc:
            logger.warning("E2EE decrypt failed for %s: %s", path, exc)
            await JSONResponse(status_code=400, content={"detail": "E2EE decrypt failed"})(scope, receive, send)
            return

        await store.refresh(session.session_id)
        await self.app(scope, receive, send)

    @staticmethod
    def _decrypt_pair_token(scope: Scope, headers: Headers, session: E2EESession) -> None:
        ciphertext = _stripped(headers.get(E2EE_PAIR_CIPHERTEXT_HEADER)) or QueryParams(scope["query_string"]).get(
            E2EE_PAIR_QUERY_PARAM
        )
        if ciphertext:
            scope["state"]["e2ee_pair_token"] = session.decrypt_text(ciphertext)

    @staticmethod
    async def _decrypt_body(scope: Scope, receive: Receive, headers: Headers, session: E2EESession) -> tuple[Scope, Receive]:
        content_type = headers.get("content-type", "")
        carries_body = scope["method"] in METHODS_WITH_BODY and (
            E2EE_CONTENT_TYPE in content_type or "application/json" in content_type
        )
        if not carries_body:
            return scope, receive

        raw = await read_request_body(receive)
        plain = _plaintext_of(raw, content_type, session)
        if plain is None:
            return scope, replay_request_body(raw, receive)
        return describe_replaced_body(scope, plain, content_type=_PLAINTEXT_CONTENT_TYPE), replay_request_body(plain, receive)


__all__ = ["E2EEMiddleware"]
