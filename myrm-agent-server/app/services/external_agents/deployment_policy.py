"""Which delegated agents this deployment runs.

A command names a delegated agent when it is that agent's executable or carries its name (``claude``,
``/usr/bin/codex``, ``claude-code``): the rule errs towards blocking, because a name that merely resembles a blocked
agent is almost always a wrapper of it. A command that does not carry the name of an agent is outside the rule: the
server cannot tell what an arbitrary program runs.

[INPUT]
- app.platform_utils.deployment_capabilities::get_deployment_capabilities (POS: 部署能力语义注册表。启动时一次性计算并缓存当前部署环境支持的语义能力位。)
- myrm_agent_harness.toolkits.acp.drivers::driver_for (POS: Resolve a DriverSpec from a backend name or command) [lazy]

[OUTPUT]
- blocked_backend: The known agent a command names when this deployment does not run it.

[POS]
Single decision point of the deployment's agent list; the install endpoint, the status rows and the runtime
registration all ask it instead of repeating the rule.
"""

from __future__ import annotations

from app.platform_utils.deployment_capabilities import get_deployment_capabilities


def blocked_backend(command_or_backend: str | None) -> str | None:
    """The known agent ``command_or_backend`` names when this deployment does not run it, else ``None``."""
    blocked = get_deployment_capabilities().blocked_external_backends
    if not blocked or not command_or_backend:
        return None

    from myrm_agent_harness.toolkits.acp.drivers import driver_for

    profile = driver_for(command_or_backend)
    return profile.backend if profile is not None and profile.backend in blocked else None
