"""Where the files a package.json declares live on disk.

[INPUT]
- (stdlib: pathlib)

[OUTPUT]
- collect_export_targets(): the relative file paths of a (conditional, nested) ``exports`` value, in document order
- resolve_artifact_file(): the file a declared entry resolves to under Node.js extension and ``index`` conventions

[POS]
Path logic of the entry-point artifact check. It creates no findings, so the audit module that decides what is a
finding stays about policy and this one about how Node.js resolves a declared path.
"""

from __future__ import annotations

from pathlib import Path


def collect_export_targets(val: object) -> list[str]:
    """Collect the relative file paths of a (conditional, nested) ``exports`` value, in document order.

    Iterative: the manifest decides how deeply the value nests, and recursion stops at the interpreter's limit.
    """
    targets: list[str] = []
    pending: list[object] = [val]
    while pending:
        node = pending.pop()
        if isinstance(node, str):
            if node.strip():
                targets.append(node.strip())
        elif isinstance(node, dict):
            pending.extend(reversed(node.values()))
        elif isinstance(node, list):
            pending.extend(reversed(node))
    return targets


def _is_file(path: Path) -> bool:
    """``Path.is_file`` that also answers False for a name the file system rejects (too long)."""
    try:
        return path.is_file()
    except OSError:
        return False


def resolve_artifact_file(base_dir: Path, clean_entry: str) -> Path | None:
    """Resolve physical file respecting Node.js extension and directory index conventions."""
    direct = base_dir / clean_entry
    if _is_file(direct):
        return direct

    extensions = (".js", ".cjs", ".mjs", ".json")
    for ext in extensions:
        candidate = base_dir / f"{clean_entry}{ext}"
        if _is_file(candidate):
            return candidate

    for ext in extensions:
        candidate = base_dir / clean_entry / f"index{ext}"
        if _is_file(candidate):
            return candidate

    return None
