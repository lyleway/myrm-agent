"""Claude Code's command line — how a turn is resumed, limited, streamed, given images and made to ask the host.

Claude resumes with a ``--resume <id>`` flag, has a native turn limit, takes an attached image as a base64 block in
its stream-json input and hands its permission prompts to the host (``--permission-prompt-tool``). The runtime
applies these to a command that is Claude Code and spawns any other command as configured.

[INPUT]
- toolkits.acp.auth._profiles::profile_for (POS: Authentication profile registry; resolves a command to its backend)
- toolkits.acp.runtime.image_blocks::ImageBlock (POS: Image input shared by CliRuntime and AcpRuntime)

[OUTPUT]
- is_claude: whether a command is Claude Code
- ENV_DEFAULTS: the environment a headless Claude turn needs
- is_session_id: whether a string has the shape of a Claude session id
- is_resuming / with_resume: the configured args already resume / the args continuing a session
- with_max_turns / with_partial_messages: Claude's own turn limit and text-as-written streaming
- with_image_blocks: the args and stdin prompt carrying a turn's images
- with_permission_bridge: the args sending Claude's permission prompts to the host

[POS]
Launch knowledge of CliRuntime about the one CLI it knows. Claude is the only agent whose own command line is run
here: the others speak a protocol of their own (ACP, the Codex app server), so a command that is not Claude Code
is a plain CLI.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType

from myrm_agent_harness.toolkits.acp.auth._profiles import profile_for
from myrm_agent_harness.toolkits.acp.runtime.image_blocks import ImageBlock

# Without it a turn stays open until every background task the model started has ended, so a task that never ends
# (a dev server) hangs the turn until the timeout. The turn then ends when the model is done. The host's and the
# configured environment win over it.
ENV_DEFAULTS: Mapping[str, str] = MappingProxyType({"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"})

# Claude identifies a conversation by a canonical hyphenated UUID. A resume id comes from a store the CLI did not
# write, so an id that does not look like one is never replayed.
_SESSION_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def is_claude(command: str | None) -> bool:
    """Whether ``command`` is Claude Code (resolved like its auth profile)."""
    profile = profile_for(command) if command else None
    return profile is not None and profile.backend == "claude"


def is_session_id(value: str) -> bool:
    """Whether ``value`` has the shape of a Claude session id."""
    return _SESSION_ID.fullmatch(value) is not None


def is_resuming(args: list[str]) -> bool:
    """Whether the configured args already pick the conversation themselves."""
    return "--resume" in args


def with_resume(args: list[str], session_id: str) -> list[str] | None:
    """``args`` continuing ``session_id``, or None when the id cannot be a Claude session."""
    return [*args, "--resume", session_id] if is_session_id(session_id) else None


def with_max_turns(args: list[str], limit: int) -> list[str]:
    """``args`` with Claude's own turn limit, unchanged when there is no positive limit or the args set one."""
    if limit <= 0 or "--max-turns" in args:
        return args
    return [*args, "--max-turns", str(limit)]


def with_partial_messages(args: list[str]) -> list[str]:
    """``args`` asking for text as it is written; that exists in stream-json output only."""
    if "--include-partial-messages" in args or "stream-json" not in args:
        return args
    return [*args, "--include-partial-messages"]


def with_image_blocks(args: list[str], prompt: str, blocks: Sequence[ImageBlock]) -> tuple[list[str], str]:
    """Claude takes images as base64 blocks of one user message in ``--input-format stream-json``.

    The message is the stdin line: the images first, then the prompt as its text block (left out when blank).
    Only a turn with images switches the input format; a plain turn keeps the plain-text prompt. An ``@`` file
    mention is not used: the CLI silently drops a mentioned image above 256 KiB.
    """
    if not blocks:
        return args, prompt
    content: list[dict[str, object]] = [
        {"type": "image", "source": {"type": "base64", "media_type": block.media_type, "data": block.data}}
        for block in blocks
    ]
    if prompt.strip():
        content.append({"type": "text", "text": prompt})
    message = {"type": "user", "message": {"role": "user", "content": content}}
    flag = [] if "--input-format" in args else ["--input-format", "stream-json"]
    return [*args, *flag], json.dumps(message)


def with_permission_bridge(args: list[str], tool: str, config_path: str) -> list[str]:
    """``--permission-prompt-tool`` names the MCP tool to ask; ``--mcp-config`` connects Claude to it."""
    return [*args, "--permission-prompt-tool", tool, "--mcp-config", config_path]
