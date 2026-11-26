"""Wire-encodability of the messages of one request: no message string that UTF-8 cannot encode is left in them.

Python hands a file name or output that was not valid UTF-8 back as text holding lone surrogates (``os.fsdecode`` maps
an undecodable byte to ``U+DC80``..``U+DCFF``). Such a string cannot be encoded, so the HTTP client raises while it
builds the request body and the provider SDK reports an opaque server error. The conversation history is re-sent on every
model call, so one such string breaks every later call of that conversation, whatever the model is asked.
Only the messages are rewritten: the tool declarations and the other request parameters join the request after this
step and are not touched.

[INPUT]
- utils.text_sanitizer::encodable_text (POS: repairs a text that only has to become UTF-8 encodable, replacing each lone surrogate with ``U+FFFD``)

[OUTPUT]
- replace_lone_surrogates: rewrite every unencodable string value of the wire messages of one request (nested in dicts and lists) to ``U+FFFD``, in place

[POS]
Last pure step of the message funnel of ``TransportChatModel``, run before the outbound guard so a policy sees the text that is
actually sent. A string that is already encodable is left as the same object, so the request bytes (and with them the
provider prompt cache) do not change for any ordinary history. The tool-result guard applies the same replacement
(``encodable_text``) where a result enters the conversation, so everything downstream of that point (hashing,
persisting, logging) handles the text the funnel will later send.
"""

from __future__ import annotations

from myrm_agent_harness.utils.text_sanitizer import encodable_text

type _Container = dict[str, object] | list[object]


def _clean(item: object, pending: list[_Container]) -> object:
    """The encodable form of a string; any other value is returned as it is, a container after queueing it for its own pass."""
    if isinstance(item, str):
        return encodable_text(item)
    if isinstance(item, dict | list):
        pending.append(item)
    return item


def replace_lone_surrogates(message_dicts: list[dict[str, object]]) -> None:
    """Make every string value of the wire messages encodable as UTF-8, replacing each lone surrogate with ``U+FFFD``.

    The walk keeps its own stack, so how deeply a value is nested is not bounded by the interpreter's, and a container
    reachable twice (or from itself) is visited once. Dict keys and tuples are not visited.
    """
    pending: list[_Container] = list(message_dicts)
    seen: set[int] = set()
    while pending:
        container = pending.pop()
        if id(container) in seen:
            continue
        seen.add(id(container))
        if isinstance(container, dict):
            for key, item in container.items():
                cleaned = _clean(item, pending)
                if cleaned is not item:
                    container[key] = cleaned
        else:
            for index, item in enumerate(container):
                cleaned = _clean(item, pending)
                if cleaned is not item:
                    container[index] = cleaned
