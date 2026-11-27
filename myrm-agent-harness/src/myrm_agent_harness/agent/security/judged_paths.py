"""The files a file tool call is judged on and the files it opens.

A verdict judges every spelling the tool may open and errs wide, so a respelling can only tighten it. A grant covers
the files the tool opens and no spelling it does not open.

[INPUT]
- core.security.path::find_existing_unicode_path (POS: the spelling a read opens in place of a typed one that names no file)
- core.security.tool_registry::host_file_paths (POS: the host files a call names, in every form a reader could open them)
- core.security.tool_registry::typed_file_args, names_host_file, strip_read_range (POS: the text a file tool opens, whether it names a host file, and the file a read opens for a path with a line range)

[OUTPUT]
- judged_file_paths(permission, tool_input, workspace_root) -> list[str]: the typed host files, then the spelling a read opens in place of each
- opened_file_paths(permission, tool_input, workspace_root) -> list[str]: the one host file each typed path opens

[POS]
``judged_file_paths`` is the target set of the permission engine's rule matching and zone check for file tools: a superset of the files the tool can open, so a respelling can only tighten a verdict. ``opened_file_paths`` is the target set of the directory grants an approval plans and opens, so that what an approval opens is what the tool opens.
"""

from __future__ import annotations

from myrm_agent_harness.core.security.path import find_existing_unicode_path
from myrm_agent_harness.core.security.tool_registry import (
    host_file_paths,
    names_host_file,
    strip_read_range,
    typed_file_args,
)


def judged_file_paths(permission: str, tool_input: dict[str, object], workspace_root: str | None) -> list[str]:
    """The host files a call names and, for a read, the spelling it opens in place of one that names no file.

    ``file_read`` opens the respelling of a typed path that names nothing (``find_existing_unicode_path``), so rules
    and zones must judge that file too: a quote, a backtick or a decomposed accent would otherwise be enough to read a
    file the typed text keeps out of reach. Every other file tool opens exactly the path it is given.
    """
    paths = host_file_paths(tool_input)
    if permission != "file_read":
        return paths
    respelled = (find_existing_unicode_path(path, workspace_root) for path in paths)
    return list(dict.fromkeys([*paths, *filter(None, respelled)]))


def opened_file_paths(permission: str, tool_input: dict[str, object], workspace_root: str | None) -> list[str]:
    """The host file each path argument opens, where ``judged_file_paths`` lists every spelling it might.

    A grant must not follow a spelling the tool does not open: the trimmed spelling of a folder named ``"d "`` names
    nothing, and a grant that falls back to its parent would open the folder above. A read opens its path without a
    line range and, when that names nothing, the respelling of it; every other file tool opens exactly the text it
    is given.
    """
    opened = (
        _opened_path(permission, typed, workspace_root)
        for typed in typed_file_args(tool_input)
        if names_host_file(typed)
    )
    return list(dict.fromkeys(opened))


def _opened_path(permission: str, typed: str, workspace_root: str | None) -> str:
    if permission != "file_read":
        return typed
    named = strip_read_range(typed)
    return find_existing_unicode_path(named, workspace_root) or named
