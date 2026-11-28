"""External-agent runtime config normalization and fingerprint helpers.

[INPUT]
- myrm_agent_harness.toolkits.acp.core.backend_detector::BackendDetector (POS: CLI backend detection)
- myrm_agent_harness.toolkits.acp.auth::effective_transport, cli_launch_args (POS: The transport and baseline launch args a known agent's contract names; a stored choice cannot override the transport)
- myrm_agent_harness.toolkits.acp.types::RuntimeConfig, BackendType (POS: ACP runtime config contract and the kinds of backend the harness can run)
- myrm_agent_harness.toolkits.acp.auth::is_valid_account_name (POS: Whether a name is one an account can have)
- app.config.deploy_mode::is_local_mode (POS: deploy mode guard)
- app.services.external_agents.accounts::agent_home (POS: The home an external agent's CLI is run in)
- app.platform_utils.deployment_capabilities::get_deployment_capabilities (POS: whether the deployment keeps subscription logins)
- app.services.external_agents.deployment_policy::blocked_backend (POS: The known agent a command names when this deployment does not run it) [lazy]
- app.config.settings::settings (POS: Gateway wall-clock ceiling that bounds a delegated turn)
- app.services.external_agents.memory_grants::memory_server_for (POS: The MCP server entry that gives an external agent the memory of a Myrm agent) [lazy]

[OUTPUT]
- _auth_mode / _permission_mode / _share_memory / _cfg_int / _turn_timeout: Config parsing helpers (the memory of the user is shared with an agent only when its config says ``shareMemory: true``, exactly; a deployment that keeps no subscription logins resolves every auth mode to api_key; an unknown permission mode fails closed to safe; the turn timeout defaults to 30 min and stays under the Gateway ceiling)
- _config_fingerprint: Stable hash aligned with RuntimeConfig-relevant fields (the account an agent runs as included, and whose memory it is given)
- _resolve_external_agent_cfgs: Resolve explicit config or local auto-detect
- _register_backends_on_pool: Register normalized runtime configs into RuntimePool (the directory a turn runs in is the turn's, not the config's; the kinds of backend the harness defines are the ones accepted; an agent the deployment does not run is skipped, and so is left out of the fingerprint; an agent runs in the home of its account, or in the one a deployment without subscription logins gives every agent; an agent that shares memory is handed the read-only memory server of the Myrm agent the pool belongs to)

[POS]
GeneralAgent 外部 Agent 配置归一化与探测装配层。确保 RuntimePool 指纹与注册字段同源，
并把本地自动发现与 RuntimeConfig 组装从执行混入层剥离，降低维护成本。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, get_args

from app.platform_utils.deployment_capabilities import get_deployment_capabilities

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
    from myrm_agent_harness.toolkits.acp.types import AuthMode, BackendType, PermissionMode

logger = logging.getLogger(__name__)

_DEFAULT_TURN_TIMEOUT_SECONDS = 1800
# A delegated turn is cut by the Gateway's wall-clock ceiling anyway; ending it a little earlier lets the
# runtime stop the CLI itself and report a timeout the user can read.
_CEILING_MARGIN_SECONDS = 60


def _auth_mode(cfg: dict[str, object]) -> AuthMode:
    """Resolve the configured auth mode, defaulting to subscription (use login state).

    A deployment that does not allow subscription login runs every delegated CLI on an API key whatever the stored
    config says, so a login left on the host cannot carry delegated work onto someone's subscription.
    """
    if not get_deployment_capabilities().allows_subscription_login:
        return "api_key"
    return "api_key" if cfg.get("authMode") == "api_key" else "subscription"


def _permission_mode(cfg: dict[str, object]) -> PermissionMode:
    """Resolve the configured permission mode; a missing or unknown value fails closed to ``safe``.

    The harness refuses to launch under a mode name it does not know, so the name is validated here instead.
    """
    match cfg.get("permissionMode"):
        case "allow_all":
            return "allow_all"
        case "safe" | None:
            return "safe"
        case other:
            logger.warning("Unknown external agent permissionMode %r; using 'safe'", other)
            return "safe"


def _share_memory(cfg: dict[str, object]) -> bool:
    """Whether the user chose to share their memory with this agent: only an explicit ``true`` counts (a consent)."""
    return cfg.get("shareMemory") is True


def _cfg_int(cfg: dict[str, object], key: str, default: int) -> int:
    """Extract an int value from a config dict with safe fallback."""
    v = cfg.get(key, default)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return default
    return default


def _turn_timeout(cfg: dict[str, object]) -> int:
    """Whole-turn time limit of a delegated CLI turn.

    A missing or non-positive value means the default, which is generous because a CLI turn that edits
    code and runs tests takes minutes; the Gateway's wall-clock ceiling caps it.
    """
    from app.config.settings import settings

    value = _cfg_int(cfg, "timeout", _DEFAULT_TURN_TIMEOUT_SECONDS)
    if value <= 0:
        logger.warning("Invalid external agent timeout %r; using %ds", cfg.get("timeout"), _DEFAULT_TURN_TIMEOUT_SECONDS)
        value = _DEFAULT_TURN_TIMEOUT_SECONDS
    ceiling = int(settings.agent.execution_hard_ceiling) - _CEILING_MARGIN_SECONDS
    return min(value, max(ceiling, 1))


def _normalized_env(cfg: dict[str, object]) -> dict[str, str] | None:
    """Return a stable env mapping used by RuntimeConfig or None."""
    raw_env = cfg.get("env")
    if not isinstance(raw_env, dict):
        return None

    normalized: dict[str, str] = {}
    for raw_key, raw_value in raw_env.items():
        key = raw_key if isinstance(raw_key, str) else str(raw_key)
        normalized[key] = raw_value if isinstance(raw_value, str) else str(raw_value)

    if not normalized:
        return None
    return {key: normalized[key] for key in sorted(normalized)}


def _account(cfg: dict[str, object]) -> str | None:
    """The account the agent runs as, or ``None`` for the CLI's own login; a name no account can have raises ``ValueError``."""
    from myrm_agent_harness.toolkits.acp.auth import is_valid_account_name

    raw = cfg.get("account")
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and is_valid_account_name(raw):
        return raw
    msg = f"invalid account name {raw!r}"
    raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class _NormalizedRuntimeCfg:
    name: str
    backend_type: str
    command: str
    args: list[str]
    env: dict[str, str] | None
    account: str | None
    timeout_seconds: int
    max_response_chars: int
    permission_mode: PermissionMode
    auth_mode: AuthMode
    max_turns: int
    description: str
    share_memory: bool


def _normalize_runtime_cfg(cfg: dict[str, object]) -> _NormalizedRuntimeCfg | None:
    """Normalize one external-agent entry so fingerprint and RuntimeConfig stay aligned."""
    from myrm_agent_harness.toolkits.acp.auth import effective_transport

    from app.services.external_agents.deployment_policy import blocked_backend

    name = cfg.get("name")
    command = cfg.get("command")
    if not name or not command:
        return None
    # An agent the deployment does not run is no agent here: it is neither registered nor part of the fingerprint.
    if blocked_backend(str(command)) is not None:
        return None

    args_raw = cfg.get("args", [])
    args: list[str] = []
    if isinstance(args_raw, (list, tuple)):
        args = [a if isinstance(a, str) else str(a) for a in args_raw]

    try:
        account = _account(cfg)
    except ValueError as exc:
        logger.warning("Skipping external agent '%s': %s", name, exc)
        return None

    return _NormalizedRuntimeCfg(
        name=str(name),
        # A known agent runs over the transport its contract names, whatever was stored for it.
        backend_type=effective_transport(str(command), str(cfg.get("type", "cli"))),
        command=str(command),
        args=args,
        env=_normalized_env(cfg),
        account=account,
        timeout_seconds=_turn_timeout(cfg),
        max_response_chars=_cfg_int(cfg, "maxResponseChars", 50_000),
        permission_mode=_permission_mode(cfg),
        auth_mode=_auth_mode(cfg),
        max_turns=_cfg_int(cfg, "maxTurns", 25),
        description=str(cfg.get("description", "")),
        share_memory=_share_memory(cfg),
    )


def _memory_owner(cfg: _NormalizedRuntimeCfg, memory_agent_id: str | None) -> str | None:
    """The Myrm agent whose memory ``cfg``'s agent is given: none unless the user shared it and the pool has an owner."""
    return memory_agent_id if cfg.share_memory else None


def _config_fingerprint(agent_cfgs: list[dict[str, object]], *, memory_agent_id: str | None = None) -> str:
    """Stable hash of enabled external agent configs for pool invalidation.

    ``memory_agent_id`` is the Myrm agent the pool belongs to: its memory is what an agent that shares memory is given,
    so a pool built for another agent's memory is a different pool.
    """
    normalized: list[dict[str, object]] = []
    for cfg in agent_cfgs:
        if not cfg.get("enabled", True):
            continue
        runtime_cfg = _normalize_runtime_cfg(cfg)
        if runtime_cfg is None:
            continue
        normalized.append(
            {
                "name": runtime_cfg.name,
                "type": runtime_cfg.backend_type,
                "command": runtime_cfg.command,
                "args": runtime_cfg.args,
                "authMode": runtime_cfg.auth_mode,
                "env": runtime_cfg.env,
                "account": runtime_cfg.account,
                "timeout": runtime_cfg.timeout_seconds,
                "maxResponseChars": runtime_cfg.max_response_chars,
                "permissionMode": runtime_cfg.permission_mode,
                "maxTurns": runtime_cfg.max_turns,
                "description": runtime_cfg.description,
                "memory": _memory_owner(runtime_cfg, memory_agent_id),
            }
        )
    normalized.sort(
        key=lambda item: (
            str(item.get("name", "")),
            str(item.get("type", "")),
            str(item.get("command", "")),
            json.dumps(item.get("args", []), ensure_ascii=False, separators=(",", ":")),
        )
    )
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


async def _resolve_external_agent_cfgs(
    external_agents_config: list[dict[str, object]] | None,
) -> list[dict[str, object]] | None:
    agent_cfgs = external_agents_config
    if agent_cfgs:
        return agent_cfgs

    from app.config.deploy_mode import is_local_mode

    if not is_local_mode():
        return None

    try:
        from myrm_agent_harness.toolkits.acp.auth import cli_launch_args, effective_transport
        from myrm_agent_harness.toolkits.acp.core.backend_detector import (
            BackendDetector,
        )

        detector = BackendDetector()
        detected = await detector.detect(include_version=False)
        if not detected:
            return None
        agent_cfgs = [
            {
                "name": d.name,
                "type": effective_transport(d.path, "cli"),
                "command": d.path,
                # Auto-detected backends inherit the fail-closed default so discovery
                # never silently grants more autonomy than the user granted explicitly.
                "args": cli_launch_args(d.name),
                "permissionMode": "safe",
                "enabled": True,
            }
            for d in detected
        ]
        logger.info("Auto-detected %d external agent(s) in local mode", len(agent_cfgs))
        return agent_cfgs
    except Exception as e:
        logger.warning("External agent auto-detection failed (degraded): %s", e)
        return None


def _backend_type(value: str) -> BackendType | None:
    """The kind of backend ``value`` names, if the harness runs it: the harness owns the list, so a new kind needs no edit here."""
    from myrm_agent_harness.toolkits.acp.types import BackendType

    return next((kind for kind in get_args(BackendType) if kind == value), None)


def _register_backends_on_pool(
    pool: RuntimePool, agent_cfgs: list[dict[str, object]], *, memory_agent_id: str | None = None
) -> None:
    """Register every usable entry of ``agent_cfgs`` on ``pool``.

    An agent whose config shares memory is given the read-only memory server of ``memory_agent_id``, with a credential
    that is issued to this pool and ends with it.
    """
    from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

    from app.services.external_agents.accounts import agent_home
    from app.services.external_agents.deployment_policy import blocked_backend
    from app.services.external_agents.memory_grants import memory_server_for

    for cfg in agent_cfgs:
        if not cfg.get("enabled", True):
            continue

        blocked = blocked_backend(str(cfg.get("command") or ""))
        if blocked is not None:
            logger.warning("Skipping external agent '%s': %s is not available in this deployment", cfg.get("name"), blocked)
            continue

        normalized_cfg = _normalize_runtime_cfg(cfg)
        if normalized_cfg is None:
            logger.warning("Skipping external agent with missing name or command: %s", cfg)
            continue

        backend_type = _backend_type(normalized_cfg.backend_type)
        if backend_type is None:
            logger.warning(
                "Skipping external agent '%s': invalid type '%s'",
                normalized_cfg.name,
                normalized_cfg.backend_type,
            )
            continue

        try:
            account_home = agent_home(normalized_cfg.command, normalized_cfg.account)
        except (OSError, ValueError) as exc:
            logger.warning("Skipping external agent '%s': its account home is unusable: %s", normalized_cfg.name, exc)
            continue

        memory_owner = _memory_owner(normalized_cfg, memory_agent_id)
        runtime_cfg = RuntimeConfig(
            backend_type=backend_type,
            command=normalized_cfg.command,
            args=normalized_cfg.args,
            env=normalized_cfg.env,
            account_home=account_home,
            timeout_seconds=normalized_cfg.timeout_seconds,
            max_response_chars=normalized_cfg.max_response_chars,
            permission_mode=normalized_cfg.permission_mode,
            auth_mode=normalized_cfg.auth_mode,
            max_turns=normalized_cfg.max_turns,
            description=normalized_cfg.description,
            mcp_servers=(
                (memory_server_for(pool, agent_id=memory_owner, external_agent=normalized_cfg.name),)
                if memory_owner is not None
                else ()
            ),
        )
        pool.register(normalized_cfg.name, runtime_cfg)
        logger.info("Registered external agent: %s (%s)", normalized_cfg.name, backend_type)
