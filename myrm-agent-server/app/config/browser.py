"""Browser pool configuration for application layer

[INPUT] myrm_agent_harness.toolkits.browser.pool.config::BrowserPoolConfig (POS: 浏览器池配置数据结构)
[INPUT] myrm_agent_harness.toolkits.browser.pool::create_egress_guard (POS: 严格网络策略下浏览器的出口连接守卫，不适用时返回 None)
[INPUT] myrm_agent_harness.toolkits.browser.pool::get_global_browser_pool / peek_global_browser_pool (POS: 全局浏览器池单例，首个创建者的参数即池的配置)
[INPUT] myrm_agent_harness.toolkits.browser.pool.proxy::RoundRobinProxyPool (POS: 浏览器代理池实现)
[INPUT] app.config.settings::settings (POS: 浏览器池容量与预热配置)
[INPUT] app.schemas.config::BrowserCloudProviderConfigValue / BrowserProxyConfigValue (POS: 云浏览器供应商 / 浏览器代理配置模型)
[INPUT] app.services.config.service::config_service (POS: 配置持久化服务)
[INPUT] app.services.extension.bridge::get_extension_bridge (POS: 浏览器扩展 CDP 桥)
[OUTPUT] get_browser_pool_config: 根据部署模式生成 BrowserPoolConfig
[OUTPUT] get_browser_launch_options: 本地/沙箱 fallback 启动参数
[OUTPUT] get_browser_egress_guard: 云端（沙箱）部署的浏览器出口连接守卫，本地模式为 None
[OUTPUT] resolve_cloud_browser_endpoint: 异步解析云浏览器 WS endpoint
[OUTPUT] resolve_browser_proxy_pool: 异步解析浏览器代理池（库配置优先于 MYRM_PROXIES 环境变量）
[OUTPUT] ensure_browser_pool: 唯一的建池入口，返回带完整配方的 GlobalBrowserPool 单例，已存在的池原样返回、绝不替换
[OUTPUT] running_browser_pool: 配置热更新取运行中的池：等进行中的建池结束，绝不建池
[POS] 浏览器池配置工厂。按部署模式（Local/Sandbox）生成 BrowserPoolConfig 预设，支持注入云浏览器 endpoint；
云端部署的浏览器池另带出口连接守卫。全局池单例只认首个创建者的参数，所以创建池的入口只有 `ensure_browser_pool`
（结构测试守卫）：配置、启动参数、扩展桥、云端点、代理池、出口守卫一次备齐，避免先创建者的残缺配方胜出。
只观察池的调用方不建池：健康检查用 `peek_global_browser_pool`，配置热更新用 `running_browser_pool`
（等进行中的建池结束，使建池期间保存的设置落到建成的池上）；没有池就什么也不做。
浏览器默认校验 TLS 证书；运营方可用环境变量 MYRM_BROWSER_IGNORE_HTTPS_ERRORS 让浏览器上下文打开证书不受信任的页面，
该开关对两种部署模式同样生效。
"""

import asyncio
import dataclasses
import logging
import os
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.browser.pool.config import (
    _DEFAULT_CDP_ENDPOINT,
    BrowserPoolConfig,
    LaunchMode,
)

from .deploy_mode import is_local_mode

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.browser.pool import EgressGuard, GlobalBrowserPool
    from myrm_agent_harness.toolkits.browser.pool.proxy import RoundRobinProxyPool

logger = logging.getLogger(__name__)

_IGNORE_HTTPS_ERRORS_ENV = "MYRM_BROWSER_IGNORE_HTTPS_ERRORS"

# Serializes the building of the pool: its recipe is read asynchronously, and callers that found no pool
# would otherwise each resolve it.
_creation_lock = asyncio.Lock()


def _operator_ignores_https_errors() -> bool:
    """Whether the operator opted in to opening pages whose TLS certificate the browser would refuse."""
    return (os.getenv(_IGNORE_HTTPS_ERRORS_ENV) or "").strip().lower() in ("true", "1", "yes")


def _resolve_local_cdp_endpoint() -> str:
    """Resolve loopback CDP HTTP endpoint for local/connect browser modes.

    Order matters: an E2E-bound run resolves through the same harness SSOT the launcher
    uses, so the pool config and the launcher can never disagree about which Chrome the
    run owns. Everything below is the desktop/local path.
    """
    try:
        from myrm_agent_harness.toolkits.browser.pool.chrome_discovery import (
            resolve_e2e_cdp_endpoint,
        )

        e2e_endpoint = resolve_e2e_cdp_endpoint()
        if e2e_endpoint:
            logger.info("E2E CDP endpoint bound: %s", e2e_endpoint)
            return e2e_endpoint
    except ImportError:
        logger.debug("Harness chrome discovery unavailable; using plain env resolution")

    cdp_port = os.getenv("CDP_PORT")
    if cdp_port:
        return f"http://127.0.0.1:{cdp_port}"

    try:
        from myrm_agent_harness.toolkits.browser.pool.chrome_discovery import discover_chrome_cdp_endpoint

        discovered = discover_chrome_cdp_endpoint()
        if discovered:
            logger.info("Local CDP endpoint discovered: %s", discovered)
            return discovered
    except Exception:
        logger.debug("Local CDP discovery failed; falling back to default endpoint", exc_info=True)

    return _DEFAULT_CDP_ENDPOINT


async def resolve_cloud_browser_endpoint() -> str | None:
    """Read cloud browser provider config from database and resolve WS endpoint.

    Returns the WebSocket CDP URL if cloud browser is configured and enabled, None otherwise.
    """
    try:
        from app.schemas.config import BrowserCloudProviderConfigValue
        from app.services.config.service import config_service

        record = await config_service.get("browserCloudProvider")
        if not record:
            return None

        config = BrowserCloudProviderConfigValue.model_validate(record.value)
        endpoint = config.resolve_ws_endpoint()
        if endpoint:
            logger.info("Cloud browser endpoint resolved: provider=%s", config.provider)
        return endpoint
    except Exception:
        logger.debug("Cloud browser config not available (first run or no config)")
        return None


async def resolve_browser_proxy_pool() -> "RoundRobinProxyPool | None":
    """Resolve browser proxy pool from DB config or MYRM_PROXIES env var.

    Priority: DB config (GUI) > MYRM_PROXIES environment variable.
    """
    from myrm_agent_harness.toolkits.browser.pool.proxy import RoundRobinProxyPool

    try:
        from app.schemas.config import BrowserProxyConfigValue
        from app.services.config.service import config_service

        record = await config_service.get("browserProxy")
        if record:
            proxy_config = BrowserProxyConfigValue.model_validate(record.value)
            if proxy_config.enabled and proxy_config.proxies:
                pool = RoundRobinProxyPool.from_urls(proxy_config.proxies)
                logger.info(
                    "Browser proxy pool loaded from config: %d proxies",
                    len(proxy_config.proxies),
                )
                return pool
    except Exception:
        logger.debug("Browser proxy config not available (first run or no config)")

    proxy_csv = os.getenv("MYRM_PROXIES", "")
    if proxy_csv:
        env_pool = RoundRobinProxyPool.from_csv(proxy_csv)
        if env_pool:
            logger.info("Browser proxy pool loaded from MYRM_PROXIES env var")
            return env_pool

    return None


def get_browser_pool_config(*, remote_ws_endpoint: str | None = None) -> BrowserPoolConfig:
    """根据部署模式获取浏览器池配置

    本地模式: launch_mode=AUTO, 自动探测 CDP 连接已有 Chrome, fallback 到新启
    沙箱模式: launch_mode=LAUNCH, 始终新启 Chromium

    Args:
        remote_ws_endpoint: Optional cloud browser WebSocket endpoint (pre-resolved).

    环境变量:
        CDP_PORT: 覆盖默认 CDP 端口 (默认 9222)
        MYRM_BROWSER_IGNORE_HTTPS_ERRORS: true/1/yes 时浏览器上下文打开证书不受信任的页面 (默认校验证书)

    Returns:
        对应部署模式下的 `BrowserPoolConfig` 实例
    """
    if is_local_mode():
        config = dataclasses.replace(
            BrowserPoolConfig.minimal(),
            launch_mode=LaunchMode.AUTO,
            cdp_endpoint=_resolve_local_cdp_endpoint(),
            remote_ws_endpoint=remote_ws_endpoint,
        )
    else:
        config = BrowserPoolConfig.defensive()
        if remote_ws_endpoint:
            config = dataclasses.replace(config, remote_ws_endpoint=remote_ws_endpoint)

    if _operator_ignores_https_errors():
        config = dataclasses.replace(config, ignore_https_errors=True)
    return config


def get_browser_launch_options() -> dict[str, object]:
    """Launch options for GlobalBrowserPool fallback browser launch.

    Local mode uses a visible window when AUTO falls back to launching Chromium.
    Sandbox mode keeps the default headless launch (unless VISUAL_DESKTOP=1).
    """
    from myrm_agent_harness.toolkits.browser.pool.browser_pool import _DEFAULT_LAUNCH_OPTIONS

    options = dict(_DEFAULT_LAUNCH_OPTIONS)
    if is_local_mode():
        options["headless"] = False
    return options


def get_browser_egress_guard() -> "EgressGuard | None":
    """Connection guard for the browsers the pool launches, in cloud (sandbox) deployments.

    A local deployment runs on the user's own machine and its sessions may reach private networks, so there is
    nothing to forbid. Elsewhere the harness decides whether the policy applies: it does not when the SSRF shield is
    off or when the environment sends HTTP traffic through a proxy that owns the policy.
    """
    if is_local_mode():
        return None
    from myrm_agent_harness.toolkits.browser.pool import create_egress_guard

    return create_egress_guard()


async def ensure_browser_pool() -> "GlobalBrowserPool":
    """Return the global browser pool, building it with the complete deployment recipe when none exists.

    The harness singleton keeps the arguments of its first caller and ignores every later one, so a caller that
    builds the pool with a partial recipe fixes that recipe for the whole process: this is the only place that
    builds it. A pool that exists is returned as it is and never replaced, because replacing it closes the
    browsers whose pages sessions are using.
    """
    from myrm_agent_harness.toolkits.browser.pool import get_global_browser_pool, peek_global_browser_pool

    async with _creation_lock:
        if (pool := peek_global_browser_pool()) is not None:
            return pool

        from app.config.settings import settings
        from app.services.extension.bridge import get_extension_bridge

        cloud_endpoint = await resolve_cloud_browser_endpoint()
        proxy_pool = await resolve_browser_proxy_pool()
        return get_global_browser_pool(
            max_browsers=settings.browser_pool.max_browsers,
            config=get_browser_pool_config(remote_ws_endpoint=cloud_endpoint),
            launch_options=get_browser_launch_options(),
            extension_bridge=get_extension_bridge(),
            proxy_pool=proxy_pool,
            egress_guard=get_browser_egress_guard(),
        )


async def running_browser_pool() -> "GlobalBrowserPool | None":
    """Return the pool if one exists, after a build in flight has finished; never build one.

    A build reads the saved settings before it creates the pool. A reload that finds no pool because a build is
    still reading would drop a setting saved in that interval, so it waits for the build and applies the setting
    to the pool it produced.
    """
    from myrm_agent_harness.toolkits.browser.pool import peek_global_browser_pool

    async with _creation_lock:
        return peek_global_browser_pool()


__all__ = [
    "get_browser_pool_config",
    "get_browser_launch_options",
    "get_browser_egress_guard",
    "resolve_cloud_browser_endpoint",
    "resolve_browser_proxy_pool",
    "ensure_browser_pool",
    "running_browser_pool",
]
