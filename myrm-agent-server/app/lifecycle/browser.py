"""Browser lifecycle management — global pool warmup/shutdown.

[INPUT]
- app.config.browser::ensure_browser_pool (POS: 唯一建池入口)
- app.config.settings::settings (POS: 预热浏览器数与页数配置)
- app.core.security.browser_vault::get_global_session_vault (POS: 浏览器会话保险库)
- myrm_agent_harness.toolkits.browser.pool::shutdown_global_browser_pool (POS: 关闭并遗忘全局浏览器池，没有池时不创建)
- myrm_agent_harness.toolkits.web_fetch::web_fetch_tools (POS: web_fetch 工具集，注入会话保险库)

[OUTPUT]
- warmup_global_browser_pool: 启动时预热全局浏览器池，池已存在则复用
- shutdown_global_browser_pool: 关闭全局浏览器池，没有池时什么也不做

[POS]
应用生命周期中的浏览器池预热与关闭。预热经 `ensure_browser_pool` 取池，不关闭也不替换已存在的池：
启动期间提前到达的请求可能已建好池，替换它会杀掉请求正在使用的页面，并让旧池的浏览器无人关闭。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def warmup_global_browser_pool() -> None:
    """预热全局浏览器池（预创建 Browser 实例和 Page）"""
    from myrm_agent_harness.toolkits.web_fetch import web_fetch_tools

    from app.config.browser import ensure_browser_pool
    from app.config.settings import settings
    from app.core.security.browser_vault import get_global_session_vault

    web_fetch_tools.set_session_vault(get_global_session_vault())

    pool = await ensure_browser_pool()
    await pool.warmup(
        browsers=settings.browser_pool.warmup_browsers,
        pages_per_context=settings.browser_pool.warmup_pages,
    )
    logger.info("GlobalBrowserPool warmup completed")


async def shutdown_global_browser_pool() -> None:
    """关闭全局浏览器池"""
    from myrm_agent_harness.toolkits.browser.pool import shutdown_global_browser_pool as close_global_browser_pool

    await close_global_browser_pool()
    logger.info("[Shutdown] GlobalBrowserPool closed")
