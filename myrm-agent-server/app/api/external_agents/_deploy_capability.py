"""Deployment capability gates for the external-agent endpoints.

A delegated CLI's subscription login leaves the user's own credential on the host, which the subscription terms allow
only on a machine the user controls. Every entry point that starts such a login, or hands the user the login to run,
shares the first gate, so a managed sandbox fails closed and the user is told to use an API key instead. The second
gate refuses to install an agent the deployment does not run at all.

[INPUT]
- app.platform_utils.deployment_capabilities::get_deployment_capabilities (POS: 部署能力语义注册表。启动时一次性计算并缓存当前部署环境支持的语义能力位。)
- app.services.external_agents.deployment_policy::blocked_backend (POS: The known agent a command names when this deployment does not run it)

[OUTPUT]
- require_subscription_login_capability: Raise 403 when the deployment does not allow subscription login.
- require_backend_allowed: Raise 403 when the deployment does not run the agent named by the ``backend`` path parameter.

[POS]
Deployment capability gates for the external-agent API — fail closed when allows_subscription_login is False or the
agent is on the deployment's blocked list.
"""

from fastapi import HTTPException

from app.platform_utils.deployment_capabilities import get_deployment_capabilities
from app.services.external_agents.deployment_policy import blocked_backend


def require_subscription_login_capability() -> None:
    """Raise 403 when the deployment does not allow subscription login."""
    if not get_deployment_capabilities().allows_subscription_login:
        raise HTTPException(
            status_code=403,
            detail="Subscription login is not available in hosted deployments. Configure an API key instead.",
        )


def require_backend_allowed(backend: str) -> None:
    """Raise 403 when the deployment does not run the agent ``backend`` names."""
    blocked = blocked_backend(backend)
    if blocked is not None:
        raise HTTPException(status_code=403, detail=f"{blocked} is not available in hosted deployments.")
