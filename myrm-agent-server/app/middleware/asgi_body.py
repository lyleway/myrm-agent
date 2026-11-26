"""Pure-ASGI primitives for middlewares that rewrite a request body.

[INPUT]
- starlette.types (POS: ASGI scope, receive and message types)
- starlette.requests::ClientDisconnect (POS: the client left before the body ended)

[OUTPUT]
- METHODS_WITH_BODY: the HTTP methods whose body a rewriting middleware looks at
- read_request_body: collect the whole request body from ``receive``
- replay_request_body: a ``receive`` that serves a replacement body, then defers to the real one
- describe_replaced_body: a scope copy whose headers describe a replacement body

[POS]
Shared by every middleware that changes what a request says (E2EE decryption, text sanitizing).
Starlette's BaseHTTPMiddleware keeps the body it reads on the request object and hands that copy to the layers
below, so a replacement written onto the request afterwards never reaches them. ``receive`` is the ASGI
contract itself: a body replaced there is the body every layer below reads, whatever kind of middleware it is.
"""

from __future__ import annotations

from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope

METHODS_WITH_BODY = frozenset({"POST", "PUT", "PATCH"})

# The two headers that describe how a body is framed; a replacement body has its own length and no chunking.
_FRAMING_HEADERS = frozenset({b"content-length", b"transfer-encoding"})


async def read_request_body(receive: Receive) -> bytes:
    """Collect every chunk of the request body.

    A client that leaves before the end raises ``ClientDisconnect``, as ``Request.body`` does; the layer that
    answers for disconnects (``MaxBodySizeMiddleware`` in this server's stack) is the one to end the request.
    """
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            raise ClientDisconnect
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            return b"".join(chunks)


def replay_request_body(body: bytes, receive: Receive) -> Receive:
    """Return a ``receive`` that serves ``body`` as the whole request body, then defers to ``receive``.

    The original ``receive`` has already delivered the end of the real body, so all it has left is the disconnect.
    Deferring to it keeps ``Request.is_disconnected`` and the disconnect watchers of streaming responses truthful.
    """
    served = False

    async def replay() -> Message:
        nonlocal served
        if served:
            return await receive()
        served = True
        return {"type": "http.request", "body": body, "more_body": False}

    return replay


def describe_replaced_body(scope: Scope, body: bytes, *, content_type: str | None = None) -> Scope:
    """Return a copy of ``scope`` whose headers describe ``body``; the caller's scope is left as it was.

    ``content_type`` replaces the content type when the rewrite changed what the body is (ciphertext to JSON).
    """
    dropped = _FRAMING_HEADERS | ({b"content-type"} if content_type is not None else frozenset())
    headers = [(name, value) for name, value in scope["headers"] if name.lower() not in dropped]
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    if content_type is not None:
        headers.append((b"content-type", content_type.encode("latin-1")))
    return {**scope, "headers": headers}
