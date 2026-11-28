"""The known agents: one DriverSpec each, and the lookups that find one by backend name or command.

[INPUT]
- toolkits.acp.drivers.spec::DriverSpec, LoginStrategy, PermissionFlags, Transport (POS: The shape of a known agent's contract)

[OUTPUT]
- driver_for: Resolve a DriverSpec from a backend name or command (a name that merely contains a backend's counts).
- command_driver: Resolve the DriverSpec of a command that is exactly a known agent's executable.
- effective_transport: The transport an agent really runs over: a known agent's contract wins over a stored choice.
- all_drivers, known_backends: Every registered spec, and their names.
- cli_launch_args: Baseline non-interactive launch args for a backend driven as a CLI process.

[POS]
The data of the contract of every known agent. An agent is added by one entry in ``_SPECS``: login, install, launch,
permissions and the settings screens all read it from there. Entries are checked as the module is imported, so two
agents that would be found by the same name fail then and not at the first start.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping

from myrm_agent_harness.toolkits.acp.drivers.spec import DriverSpec, LoginStrategy, PermissionFlags, Transport

# The flags gemini and qwen name for the approval policy: the one that picks it, and the ones that skip asking.
_APPROVAL_MODE_FLAGS = PermissionFlags(
    names=frozenset({"-y", "--yolo", "--approval-mode"}),
    value_names=frozenset({"--approval-mode"}),
)

# Known agents, in the order they are detected and listed. Credential locations and home-override vars below reflect
# each CLI's documented behaviour.
_SPECS: tuple[DriverSpec, ...] = (
    DriverSpec(
        backend="claude",
        title="Claude Code",
        default_name="claude-code",
        description="Full-stack coding agent powered by Anthropic Claude",
        npm_package="@anthropic-ai/claude-code",
        home_env="CLAUDE_CONFIG_DIR",
        home_dir=".claude",
        credential_files=(".credentials.json",),
        login_strategy=LoginStrategy.TERMINAL,
        login_args=("auth", "login", "--claudeai"),
        logout_args=("auth", "logout"),
        api_key_env=("ANTHROPIC_API_KEY",),
        login_probe=("auth", "status"),
        launch_args=("-p", "--output-format", "stream-json", "--verbose"),
        # ``--permission-prompt-tool`` is how a headless turn asks the host (``cli.claude.with_permission_bridge``).
        permission_flags=PermissionFlags(
            names=frozenset(
                {"--permission-mode", "--dangerously-skip-permissions", "--full-auto", "--permission-prompt-tool"}
            ),
            value_names=frozenset({"--permission-mode", "--permission-prompt-tool"}),
        ),
        # ``--permission-mode`` accepts default / acceptEdits / plan / auto / dontAsk / bypassPermissions. ``default`` is
        # the mode that asks before anything beyond a read; a headless run has nobody to ask, so it either hands the
        # question to the host through ``--permission-prompt-tool`` or refuses. Both flag and value are separate argv
        # entries, because some CLI parsers accept only the spaced form.
        permission_mode_args=(
            ("safe", ("--permission-mode", "default")),
            ("allow_all", ("--permission-mode", "bypassPermissions")),
        ),
    ),
    DriverSpec(
        backend="codex",
        title="Codex CLI",
        default_name="codex-cli",
        description="OpenAI Codex CLI coding agent (requires Responses API provider)",
        npm_package="@openai/codex",
        home_env="CODEX_HOME",
        home_dir=".codex",
        credential_files=("auth.json",),
        login_strategy=LoginStrategy.BROWSER_OAUTH,
        login_args=("login",),
        remote_login_args=("login", "--device-auth"),
        logout_args=("logout",),
        # The app server reads no key from its environment; the runtime hands the first of these the host injected to
        # the server as a login that is never written to disk.
        api_key_env=("OPENAI_API_KEY", "CODEX_API_KEY"),
        login_probe=("login", "status"),
        transport="app_server",
        launch_args=("app-server",),
    ),
    DriverSpec(
        backend="gemini",
        title="Gemini CLI",
        default_name="gemini-cli",
        description="Google Gemini-powered coding agent",
        npm_package="@google/gemini-cli",
        home_env="GEMINI_CLI_HOME",
        home_dir=".gemini",
        # The legacy file, and the encrypted file the CLI moves it into when it is told to keep its login in a file.
        credential_files=("oauth_creds.json", "gemini-credentials.json"),
        login_strategy=LoginStrategy.TERMINAL,
        login_args=(),
        logout_args=None,
        api_key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        transport="acp",
        launch_args=("--acp", "--approval-mode", "default"),
        acp_ask_mode="default",
        home_env_is_base=True,
        isolation_env=(("GEMINI_FORCE_FILE_STORAGE", "true"),),
        permission_flags=_APPROVAL_MODE_FLAGS,
        # The CLI connects to no MCP server in a folder it has not been told to trust, and the app cannot make that
        # decision for the user.
        mcp_needs_trusted_folder=True,
    ),
    DriverSpec(
        backend="qwen",
        title="Qwen Code",
        default_name="qwen-code",
        description="Alibaba Qwen-powered coding agent",
        npm_package="@qwen-code/qwen-code",
        home_env="QWEN_HOME",
        home_dir=".qwen",
        credential_files=("oauth_creds.json",),
        login_strategy=LoginStrategy.TERMINAL,
        login_args=(),
        logout_args=None,
        api_key_env=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        transport="acp",
        launch_args=("--acp", "--approval-mode", "default"),
        acp_ask_mode="default",
        permission_flags=_APPROVAL_MODE_FLAGS,
    ),
)


def _normalize(token: str) -> str:
    """Reduce a command path or agent name to a bare lowercase backend key."""
    base = os.path.basename(token.strip()).lower()
    return base.split(".", 1)[0]  # drop extensions like .cmd / .exe


def _resolve(registry: Mapping[str, DriverSpec], backend_or_command: str) -> DriverSpec | None:
    """The spec of the executable's name, else of the first backend whose name the command merely carries."""
    if not backend_or_command:
        return None
    key = _normalize(backend_or_command)
    direct = registry.get(key)
    if direct is not None:
        return direct
    return next((spec for name, spec in registry.items() if name in key), None)


def _index(specs: Iterable[DriverSpec]) -> dict[str, DriverSpec]:
    """The specs by backend name, or ``ValueError`` when two would be found by the same name.

    A backend, and the name an agent starts with, must belong to one agent: the second would be unreachable, or an
    agent added from the first would be taken for the other (a name that contains a backend's name is found by it).
    """
    registry: dict[str, DriverSpec] = {}
    for spec in specs:
        if spec.backend in registry:
            msg = f"Two agents are registered as {spec.backend!r}"
            raise ValueError(msg)
        registry[spec.backend] = spec
    added_as: dict[str, str] = {}
    for spec in registry.values():
        if spec.default_name in added_as:
            msg = f"{added_as[spec.default_name]!r} and {spec.backend!r} are both added as {spec.default_name!r}"
            raise ValueError(msg)
        added_as[spec.default_name] = spec.backend
        if _resolve(registry, spec.default_name) is not spec:
            msg = f"An agent added as {spec.default_name!r} would be taken for another agent than {spec.backend!r}"
            raise ValueError(msg)
    return registry


_REGISTRY: dict[str, DriverSpec] = _index(_SPECS)


def driver_for(backend_or_command: str) -> DriverSpec | None:
    """Resolve a DriverSpec from a backend name or executable command.

    Matches the executable basename first (``/usr/bin/codex`` → ``codex``), then
    falls back to a substring scan so configured names like ``codex-cli`` or
    ``claude-code`` still resolve.
    """
    return _resolve(_REGISTRY, backend_or_command)


def command_driver(command: str | None) -> DriverSpec | None:
    """The spec of a command that is exactly a known agent's executable (``/usr/bin/gemini``, ``gemini.cmd``).

    ``driver_for`` also accepts a name that merely contains a backend's (``claude-code``), which suits resolving the
    login of an agent by its configured name. How an agent is started and driven is decided only for the real
    executable: an adapter or wrapper that happens to carry an agent's name is a command of its own.
    """
    if not command:
        return None
    return _REGISTRY.get(_normalize(command))


def effective_transport[Configured: str](command: str | None, configured: Configured) -> Configured | Transport:
    """The transport ``command`` really runs over: a known agent's contract wins over the choice stored for it.

    Any other command keeps ``configured`` as it is, a value this module does not recognise included, so the layer that
    stored it can still report it.
    """
    spec = command_driver(command)
    return spec.transport if spec is not None else configured


def all_drivers() -> tuple[DriverSpec, ...]:
    """Every registered spec, in the order agents are detected and listed."""
    return tuple(_REGISTRY.values())


def known_backends() -> tuple[str, ...]:
    """Names of all registered backends."""
    return tuple(_REGISTRY)


def cli_launch_args(backend_or_command: str) -> list[str]:
    """Baseline non-interactive launch args of a backend driven as a CLI process, or an empty list.

    Business layers call this instead of hard-coding per-CLI arguments, so all CLI
    invocation knowledge stays in this registry and cannot drift across layers. An agent driven over ACP or an app
    server has no baseline to store: its launch args are resolved when it starts.
    """
    spec = driver_for(backend_or_command)
    return list(spec.launch_args) if spec is not None and spec.transport == "cli" else []
