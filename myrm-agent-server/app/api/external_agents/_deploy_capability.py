"""Deployment capability gate for the external-agent subscription login endpoints.

A delegated CLI's subscription login leaves the user's own credential on the host, which the subscription terms allow
only on a machine the user controls. Every entry point that starts such a login or stores such a credential shares
this gate, so a managed sandbox fails closed and the user is told to use an API key instead.

[INPUT]
- app.platform_utils.deployment_capabilities::get_deployment_capabilities (POS: 部署能力语义注册表。启动时一次性计算并缓存当前部署环境支持的语义能力位。)

[OUTPUT]
- require_subscription_login_capability: Raise 403 when the deployment does not allow subscription login.

[POS]
Deployment capability gate for the external-agent auth API — fails closed when allows_subscription_login is False.
"""

from fastapi import HTTPException

from app.platform_utils.deployment_capabilities import get_deployment_capabilities


def require_subscription_login_capability() -> None:
    """Raise 403 when the deployment does not allow subscription login."""
    if not get_deployment_capabilities().allows_subscription_login:
        raise HTTPException(
            status_code=403,
            detail="Subscription login is not available in hosted deployments. Configure an API key instead.",
        )
