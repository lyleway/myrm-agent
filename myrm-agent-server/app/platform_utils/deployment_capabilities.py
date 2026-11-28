"""Deployment capability registry — semantic flags derived once at startup.

[INPUT]
- app.config.deploy_mode::DeployMode, get_deploy_mode, is_local_mode, is_webui_remote_mode (POS: 部署模式与运行方式枚举与判断)

[OUTPUT]
- DeploymentCapabilities: 部署能力语义标志数据类（含托管沙箱不安装也不运行的外部 Agent 名单）
- get_deployment_capabilities: 单例缓存获取当前进程部署能力标志

[POS]
部署能力语义注册表。启动时一次性计算并缓存当前部署环境支持的语义能力位。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from app.config.deploy_mode import DeployMode, get_deploy_mode, is_local_mode, is_webui_remote_mode

# The delegated agents a managed sandbox does not run, named as their contract names them (``DriverSpec.backend``).
# Where the machine is the user's own (desktop, local WebUI, a WebUI exposed from the user's machine) nothing is blocked.
#
#   claude  Anthropic asks for a commercial agreement before Claude Code is run on behalf of others. Its terms let an end
#           user use their own subscription in an unmodified binary, so blocking it here is the conservative default:
#           once the terms are settled for this product, deleting the name below is the whole change.
#   codex   OpenAI's terms do not allow hosting or commercial use of ``codex app-server``.
#   gemini, qwen   hosting terms not verified: neither blocked nor cleared by this table.
#
# The rule lives in the server, not in the harness: the harness runs any agent it is told to, and which of them a
# deployment may offer is a decision of whoever operates it.
_HOSTED_BLOCKED_EXTERNAL_BACKENDS: frozenset[str] = frozenset({"claude", "codex"})


@dataclass(frozen=True, slots=True)
class DeploymentCapabilities:
    """What this server instance is allowed to do in the current deploy mode.

    ``allows_subscription_login`` is true only where the machine belongs to the user (desktop, local WebUI, and a
    WebUI the user exposes remotely from their own machine). A delegated CLI's subscription login leaves the user's
    own credential on the host, which the subscription terms allow only on a machine the user controls; a managed
    sandbox runs delegated CLIs on an API key instead. No environment variable overrides it.

    ``blocked_external_backends`` names the delegated agents this deployment neither installs nor runs, whatever way
    they are authenticated; like ``allows_subscription_login`` it has no environment override. It has a default, and
    so must any field added after it, so a snapshot built without it stays valid.
    """

    allows_local_skills: bool
    allows_subscription_login: bool
    requires_api_key_auth: bool
    uses_platform_budget: bool
    validates_mcp_response_size: bool
    uses_config_encryption: bool
    requires_strict_ws_auth: bool
    uses_cp_entitlements: bool
    trust_cp_proxy_identity: bool
    enables_auth_audit: bool
    default_metrics_enabled: bool
    runs_sandbox_startup_validation: bool
    skips_webui_model_preflight: bool
    is_sandbox_instance: bool
    blocked_external_backends: frozenset[str] = frozenset()


@lru_cache(maxsize=1)
def get_deployment_capabilities() -> DeploymentCapabilities:
    """Build cached capability snapshot for the current process."""
    mode = get_deploy_mode()
    sandbox = mode == DeployMode.SANDBOX
    local = is_local_mode()
    remote = is_webui_remote_mode()

    env_allow_local = os.getenv("MYRM_ALLOW_LOCAL_SKILLS")
    if env_allow_local is not None:
        allows_local = env_allow_local.strip().lower() in {"1", "true", "yes", "on"}
    else:
        allows_local = local or sandbox

    return DeploymentCapabilities(
        allows_local_skills=allows_local,
        allows_subscription_login=local,
        requires_api_key_auth=sandbox or remote,
        uses_platform_budget=sandbox,
        validates_mcp_response_size=sandbox,
        uses_config_encryption=sandbox,
        requires_strict_ws_auth=sandbox,
        uses_cp_entitlements=sandbox,
        trust_cp_proxy_identity=sandbox,
        enables_auth_audit=sandbox or remote,
        default_metrics_enabled=not (local or sandbox),
        runs_sandbox_startup_validation=sandbox,
        skips_webui_model_preflight=sandbox or not local,
        is_sandbox_instance=sandbox,
        blocked_external_backends=_HOSTED_BLOCKED_EXTERNAL_BACKENDS if sandbox else frozenset(),
    )


def _reset_capabilities_cache_for_testing() -> None:
    get_deployment_capabilities.cache_clear()


__all__ = [
    "DeploymentCapabilities",
    "get_deployment_capabilities",
    "_reset_capabilities_cache_for_testing",
]
