"""Package name and ecosystem extraction for MCP stdio OSV malware checks.

Parses package-runner and Python-interpreter CLI arguments into ``(package_name, ecosystem)``
pairs suitable for ``https://api.osv.dev/v1/query``. Local script paths, inline ``-c`` code,
and local filesystem targets return ``None`` so private paths are never sent to OSV.

[INPUT]
- collections.abc::Sequence (POS: Python 抽象集合类型标准库)
- re::re (POS: Python 正则表达式标准库)

[OUTPUT]
- extract_osv_package_info: pure CLI parser returning ``(package_name, ecosystem)`` or ``None``

[POS]
Pure helper used by ``toolkits/mcp/security.py::check_osv_malware``. Zero I/O and no
runtime dependencies.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_PYTHON_CMD_RE = re.compile(r"^python(?:3(?:\.\d+)?)?$")
_PIP_CMD_RE = re.compile(r"^pip(?:3(?:\.\d+)?)?$")
_PYPI_SPEC_SPLIT_RE = re.compile(r"[\[=<>!~;@]")

_NPM_COMMANDS: frozenset[str] = frozenset({"npx", "npm", "yarn", "pnpm"})
_NPM_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "npm": frozenset({"exec", "x", "install", "i", "dlx"}),
    "yarn": frozenset({"dlx", "exec", "add"}),
    "pnpm": frozenset({"dlx", "exec", "add"}),
}
_NPM_LOCAL_SCRIPT_SUBCOMMANDS: frozenset[str] = frozenset({"run", "run-script", "start", "test"})
_NPM_PKG_FLAGS: frozenset[str] = frozenset({"-p", "--package"})
_NPM_VALUE_FLAGS: frozenset[str] = frozenset({"-c", "--call", "--prefix", "--userconfig", "--cache"})

_PIP_SUBCOMMANDS: frozenset[str] = frozenset({"install", "download", "wheel"})
_PIP_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-r",
        "--requirement",
        "-c",
        "--constraint",
        "-i",
        "--index-url",
        "--extra-index-url",
        "-f",
        "--find-links",
        "-t",
        "--target",
        "--python-version",
        "--platform",
        "--implementation",
        "--abi",
        "-e",
        "--editable",
    }
)
_UVX_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "--python",
        "--with",
        "--with-editable",
        "--with-requirements",
        "--index-url",
        "--extra-index-url",
        "--index",
        "--default-index",
    }
)


def _normalize_command_name(command: str) -> str:
    base = command.strip().lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    for suffix in (".exe", ".cmd", ".bat"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _is_local_target(token: str) -> bool:
    return (
        token in {".", ".."}
        or "\\" in token
        or token.startswith(("/", "./", "../", "~", "file:", "http://", "https://", "git+"))
    )


def _strip_npm_version(spec: str) -> str:
    if spec.startswith("@"):
        slash = spec.find("/", 1)
        if slash <= 1:
            return spec
        ver_at = spec.find("@", slash + 1)
        return spec[:ver_at] if ver_at > slash + 1 else spec
    ver_at = spec.find("@")
    return spec[:ver_at] if ver_at > 0 else spec


def _strip_pypi_spec(spec: str) -> str | None:
    if _is_local_target(spec):
        return None
    name = _PYPI_SPEC_SPLIT_RE.split(spec, maxsplit=1)[0].strip()
    return name or None


def _normalize_python_module_package(module: str) -> str | None:
    cleaned = module.strip()
    if not cleaned or cleaned.startswith(".") or _is_local_target(cleaned) or "/" in cleaned:
        return None
    top_level = cleaned.split(".", 1)[0]
    return top_level or None


def _extract_python_module(args: Sequence[str]) -> str | None:
    """Parse Python interpreter CLI options; return the PyPI package only for ``-m <module>``."""
    skip_next = False
    take_module = False
    for raw in args:
        token = raw.strip()
        if not token:
            continue
        if take_module:
            return _normalize_python_module_package(token)
        if skip_next:
            skip_next = False
            continue
        if token in {"--", "-"}:
            return None
        if token.startswith("--"):
            if token == "--check-hash-based-pycs":
                skip_next = True
            continue
        if not token.startswith("-"):
            # Positional arg is a local script path; stop scanning so script flags like `-m` are ignored.
            return None
        flags = token[1:]
        for idx, flag in enumerate(flags):
            if flag == "m":
                attached = flags[idx + 1 :]
                if attached:
                    return _normalize_python_module_package(attached)
                take_module = True
                break
            if flag == "c":
                return None
            if flag in {"W", "X"}:
                if idx + 1 == len(flags):
                    skip_next = True
                break
    return None


def _scan_package_arg(
    args: Sequence[str],
    *,
    subcommands: frozenset[str],
    pkg_flags: frozenset[str],
    value_flags: frozenset[str],
) -> str | None:
    skip_next = False
    take_next = False
    after_dashdash = False
    subcommand_pending = bool(subcommands)
    for raw in args:
        token = raw.strip()
        if not token:
            continue
        if take_next:
            return token
        if skip_next:
            skip_next = False
            continue
        if not after_dashdash:
            if token == "--":
                after_dashdash = True
                subcommand_pending = False
                continue
            if token in pkg_flags:
                take_next = True
                continue
            flag, sep, value = token.partition("=")
            if sep and flag in pkg_flags:
                return value.strip() or None
            if token in value_flags:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
        if subcommand_pending and token.lower() in subcommands:
            subcommand_pending = False
            continue
        return token
    return None


def extract_osv_package_info(
    command: str,
    args: Sequence[str] | None,
) -> tuple[str, str] | None:
    """Extract ``(package_name, ecosystem)`` from an MCP stdio launch command and args.

    Returns ``None`` when the launch runs a local script, inline code, or an unknown command.
    """
    if not args:
        return None
    cmd_base = _normalize_command_name(command)

    if _PYTHON_CMD_RE.match(cmd_base):
        module = _extract_python_module(args)
        return (module, "PyPI") if module else None

    if cmd_base in _NPM_COMMANDS:
        spec = _scan_package_arg(
            args,
            subcommands=_NPM_SUBCOMMANDS.get(cmd_base, frozenset()),
            pkg_flags=_NPM_PKG_FLAGS,
            value_flags=_NPM_VALUE_FLAGS,
        )
        if not spec or _is_local_target(spec):
            return None
        if cmd_base != "npx" and spec.lower() in _NPM_LOCAL_SCRIPT_SUBCOMMANDS:
            return None
        pkg = _strip_npm_version(spec)
        return (pkg, "npm") if pkg else None

    if _PIP_CMD_RE.match(cmd_base):
        spec = _scan_package_arg(
            args,
            subcommands=_PIP_SUBCOMMANDS,
            pkg_flags=frozenset(),
            value_flags=_PIP_VALUE_FLAGS,
        )
    elif cmd_base == "uvx":
        spec = _scan_package_arg(
            args,
            subcommands=frozenset(),
            pkg_flags=frozenset({"--from"}),
            value_flags=_UVX_VALUE_FLAGS,
        )
    elif cmd_base == "pipx":
        spec = _scan_package_arg(
            args,
            subcommands=frozenset({"run", "install"}),
            pkg_flags=frozenset({"--spec"}),
            value_flags=_UVX_VALUE_FLAGS,
        )
    else:
        return None

    pypi_pkg = _strip_pypi_spec(spec) if spec else None
    return (pypi_pkg, "PyPI") if pypi_pkg else None


__all__ = ["extract_osv_package_info"]
