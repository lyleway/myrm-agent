"""Forced direct delegation: a turn addressed to one named external agent runs there, or fails visibly.

[INPUT]
- app.ai_agents.extensions.security_policy_extension::sync_wrapper_security_inputs (POS: 每回合重载有效安全策略的组成：Agent 覆盖与已保存的 securityConfig) [lazy]
- app.core.security.session_overlay::SecurityOverlaySpec (POS: 请求覆盖在已保存安全配置之上的层)
- app.services.external_agents.delegation::direct_turn_denial, agent_unavailable_event, delegate_denied_event, delegate_failed_event (POS: 直连委托的安全判定与失败的用户指引)

[OUTPUT]
- stream_forced_delegate: 安全闸门 → 可用性检查 → 直连流式转发；每条失败路径都以带处置指引的错误事件结束

[POS]
`execute_stream_pipeline` 里 `force_external_agent` 分支（用户在输入框指定了某个外部 Agent）的实现。用户指定了
哪个 Agent 就只由它回答：被安全策略拦下、不可用、中途出意外，都明确报错，不换成主 Agent 或别的 Agent 作答。
闸门与模型发起的委派判的是同一份有效策略（通道预设、已保存的配置、Agent 自己的覆盖、请求的覆盖），判之前先像
每个普通回合那样重载它的组成，所以设置里的改动对下一条消息就生效。
用户的原话原样交给 CLI：这条路径不加给主 Agent 模型看的路由横幅，日志里也不记用户的消息内容。
"""

from __future__ import annotations

import logging
from contextlib import aclosing
from typing import TYPE_CHECKING

from app.core.security.session_overlay import SecurityOverlaySpec
from app.services.external_agents.delegation import (
    Denial,
    agent_unavailable_event,
    delegate_denied_event,
    delegate_failed_event,
    direct_turn_denial,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from myrm_agent_harness.utils.runtime.cancellation import CancellationToken

    from app.ai_agents.general_agent.agent import GeneralAgent

logger = logging.getLogger(__name__)


async def _denial(agent_wrapper: GeneralAgent, agent_name: str) -> Denial | None:
    """Why the security policy does not let this chat delegate to ``agent_name`` (None: it may)."""
    from app.ai_agents.extensions.security_policy_extension import sync_wrapper_security_inputs

    stored = await sync_wrapper_security_inputs(agent_wrapper)
    return direct_turn_denial(
        agent_name,
        agent_wrapper.security_overlay or SecurityOverlaySpec(),
        lambda overlay: agent_wrapper._security_config_for(overlay.apply(stored)),
    )


async def stream_forced_delegate(
    agent_wrapper: GeneralAgent,
    query: object,
    agent_name: str,
    *,
    cancel_token: CancellationToken | None,
    chat_id: str | None,
) -> AsyncGenerator[dict[str, object]]:
    """Run the turn on ``agent_name`` and stream its events; any failure ends it with one guided error event."""
    locale = agent_wrapper.locale or "en"

    denial = await _denial(agent_wrapper, agent_name)
    if denial is not None:
        # The user is told what to change for this cause; the policy engine's own wording stays in the log.
        logger.warning(
            "Direct external delegate blocked by security policy: agent=%s cause=%s reason=%s",
            agent_name,
            denial.cause.value,
            denial.reason,
        )
        yield delegate_denied_event(agent_name, locale, denial.cause)
        return

    if agent_wrapper._runtime_pool is None:
        await agent_wrapper._ensure_runtime_pool()
    pool = agent_wrapper._runtime_pool
    if pool is None or agent_name not in pool.available_backends:
        logger.warning("Direct external delegate unavailable: agent=%s", agent_name)
        yield agent_unavailable_event(agent_name, locale)
        return

    logger.info("Direct routing to external agent: agent=%s chat_id=%s", agent_name, chat_id or "")
    try:
        async with aclosing(
            agent_wrapper._direct_delegate_stream(agent_name, query, cancel_token=cancel_token, chat_id=chat_id)
        ) as delegate_events:
            async for event in delegate_events:
                yield event
    except Exception:
        logger.error("Direct delegate failed: agent=%s", agent_name, exc_info=True)
        yield delegate_failed_event(agent_name, locale)
