"""LangChain/OpenAI chat messages ↔ OpenAI Responses API input translator.

[INPUT]
- hashlib::hashlib (POS: Python 内容哈希标准库)
- json::json (POS: Python JSON 序列化标准库)
- typing::Any (POS: Python 类型标注标准库)

[OUTPUT]
- chat_messages_to_responses_input: messages translated into Responses API `input` items; a system message
    behind the first keeps its position as a `system` item
- extract_responses_instructions: (system instructions, remaining messages) split
- resolve_min_output_tokens: output-token floor for reasoning models

[POS]
Outbound leg of the LLM adapter wire layer. Pure and side-effect free: no I/O and no
vendor SDK calls. Inbound response parsing lives in normalizer.py; call assembly in params.py.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

ResponsesRole = str

MUSE_SPARK_MIN_OUTPUT_TOKENS = 512
_MAX_RESPONSES_CALL_ID_LENGTH = 64


def resolve_min_output_tokens(requested: int | None) -> int:
    """Floor output budget for reasoning models that consume tokens before text."""
    base = requested if requested is not None and requested > 0 else 1024
    return max(MUSE_SPARK_MIN_OUTPUT_TOKENS, base)


def _normalize_role(raw_role: object) -> ResponsesRole:
    role = str(raw_role or "user")
    if role in {"assistant", "system"}:
        return role
    return "user"


def _content_part_type(role: ResponsesRole) -> str:
    return "output_text" if role == "assistant" else "input_text"


def _image_part(part: dict[str, Any]) -> dict[str, str] | None:
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not isinstance(url, str) or not url:
        return None
    mapped = {"type": "input_image", "image_url": url}
    detail = image.get("detail") if isinstance(image, dict) else None
    if isinstance(detail, str):
        mapped["detail"] = detail
    return mapped


def _map_content_part(part: object, role: ResponsesRole) -> dict[str, str]:
    part_type = _content_part_type(role)
    if isinstance(part, str):
        return {"type": part_type, "text": part}
    if isinstance(part, dict):
        ptype = part.get("type")
        text = part.get("text")
        if ptype == "image_url" and role != "assistant" and (image := _image_part(part)) is not None:
            return image
        if ptype == "text" and isinstance(text, str):
            return {"type": part_type, "text": text}
        if ptype == "input_text" and isinstance(text, str):
            mapped = "output_text" if role == "assistant" else "input_text"
            return {"type": mapped, "text": text}
        if ptype == "output_text" and isinstance(text, str):
            return {"type": "output_text", "text": text}
    return {"type": part_type, "text": json.dumps(part, ensure_ascii=False)}


def _content_to_parts(content: object, role: ResponsesRole) -> tuple[list[dict[str, str]], str]:
    if isinstance(content, str):
        return [{"type": _content_part_type(role), "text": content}], content
    if isinstance(content, list):
        parts = [_map_content_part(part, role) for part in content]
        text_type = _content_part_type(role)
        text = "".join(p.get("text", "") for p in parts if p.get("type") == text_type)
        return parts, text
    serialized = json.dumps(content, ensure_ascii=False)
    return [{"type": _content_part_type(role), "text": serialized}], serialized


def _deterministic_call_id(fn_name: str, arguments: str, index: int) -> str:
    digest = hashlib.sha256(f"{fn_name}:{arguments}:{index}".encode()).hexdigest()[:48]
    return f"call_{digest}"


def _clamp_responses_call_id(call_id: str) -> str:
    trimmed = call_id.strip()
    if len(trimmed) <= _MAX_RESPONSES_CALL_ID_LENGTH:
        return trimmed
    return trimmed[:_MAX_RESPONSES_CALL_ID_LENGTH]


def _normalize_tool_arguments(raw_arguments: object) -> str:
    if isinstance(raw_arguments, dict):
        return json.dumps(raw_arguments, ensure_ascii=False)
    if isinstance(raw_arguments, str):
        return raw_arguments.strip() or "{}"
    return str(raw_arguments or "{}")


def _collect_responses_reasoning_items(msg: Mapping[str, object]) -> list[dict[str, Any]]:
    items = msg.get("responses_reasoning_items")
    if not isinstance(items, list):
        return []
    replayed: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            entry = dict(item)
            if "summary" not in entry:
                entry["summary"] = []
            replayed.append(entry)
    return replayed


def _assistant_content_parts(content: object) -> list[dict[str, str]]:
    """Return non-empty assistant content parts, omitting None and blank text."""
    if content is None:
        return []
    parts, _ = _content_to_parts(content, "assistant")
    return [part for part in parts if part.get("type") != "output_text" or part.get("text", "").strip()]


def _collect_function_calls(msg: Mapping[str, object], *, index_offset: int) -> list[dict[str, str]]:
    tool_calls = msg.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    calls: list[dict[str, str]] = []
    for index, tc in enumerate(tool_calls):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        fn_name = fn.get("name")
        if not isinstance(fn_name, str) or not fn_name.strip():
            continue
        arguments = _normalize_tool_arguments(fn.get("arguments", "{}"))
        call_id = tc.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = _deterministic_call_id(fn_name, arguments, index_offset + index)
        calls.append(
            {
                "type": "function_call",
                "call_id": _clamp_responses_call_id(call_id),
                "name": fn_name.strip(),
                "arguments": arguments,
            }
        )
    return calls


def _append_assistant_message(
    result: list[dict[str, Any]],
    msg: Mapping[str, object],
    *,
    content: object,
) -> None:
    assistant_parts = _assistant_content_parts(content)
    reasoning_items = _collect_responses_reasoning_items(msg)
    offset = len(result) + len(reasoning_items) + (1 if assistant_parts else 0)
    function_calls = _collect_function_calls(msg, index_offset=offset)
    if not assistant_parts and not function_calls:
        return
    result.extend(reasoning_items)
    if assistant_parts:
        result.append({"role": "assistant", "content": assistant_parts})
    result.extend(function_calls)


def _append_tool_output(result: list[dict[str, Any]], msg: Mapping[str, object]) -> None:
    raw_tool_call_id = msg.get("tool_call_id")
    if not isinstance(raw_tool_call_id, str) or not raw_tool_call_id.strip():
        return
    tool_content = msg.get("content")
    if isinstance(tool_content, list):
        converted = _content_to_parts(tool_content, "user")[0]
        output_value: object = converted if converted else ""
    else:
        output_value = str(tool_content or "")
    result.append(
        {
            "type": "function_call_output",
            "call_id": _clamp_responses_call_id(raw_tool_call_id),
            "output": output_value,
        }
    )


def chat_messages_to_responses_input(messages: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    """Convert OpenAI-style chat message dicts to Responses API input items.

    The leading system message is promoted to ``instructions`` by ``extract_responses_instructions``. A system
    message anywhere else keeps its position as a ``system`` item: the runtime appends its rules behind the user
    message so the system prompt (and the cache prefix it anchors) stays untouched, and the model has to be sent them.
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "user")
        if role == "tool":
            _append_tool_output(result, msg)
            continue
        if role == "assistant":
            _append_assistant_message(result, msg, content=msg.get("content", ""))
            continue

        normalized_role = _normalize_role(role)
        raw_content = msg.get("content")
        parts, text = _content_to_parts("" if raw_content is None else raw_content, normalized_role)
        if normalized_role == "system" and not text.strip():
            continue
        if parts:
            result.append({"role": normalized_role, "content": parts})
        else:
            result.append({"role": normalized_role, "content": text})
    return result


def extract_responses_instructions(
    messages: Sequence[Mapping[str, object]],
) -> tuple[str | None, list[Mapping[str, object]]]:
    """Promote leading system message to Responses ``instructions`` when present."""
    if not messages:
        return None, []
    first = messages[0]
    if str(first.get("role")) not in {"system", "developer"}:
        return None, list(messages)
    content = first.get("content")
    if isinstance(content, str):
        instructions = content
    elif isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(part, str):
                texts.append(part)
        instructions = "\n".join(texts)
    else:
        instructions = json.dumps(content, ensure_ascii=False)
    return instructions, list(messages[1:])
