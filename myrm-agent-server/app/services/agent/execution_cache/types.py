"""Execution cache types — pooled BuiltExecutionUnit and execution mode.

[INPUT]
- myrm_agent_harness.api::SkillAgent (POS: harness agent 实例，其 executor 随单元复用)
- myrm_agent_harness.toolkits.browser::BrowserSession (POS: 随单元复用的浏览器会话，类型与 GeneralAgent 持有的一致)

[OUTPUT]
- ExecutionMode, BuiltExecutionUnit (teardown 依次关闭浏览器、桌面、执行器与 agent)

[POS]
execution_cache 类型层。定义 pooled/ephemeral 模式与可复用构建单元。单元复用 agent 的执行器，run 结束只关闭它自己创建的执行器，
所以池化 chat 的执行器（及其中的 shell）只在单元 teardown 时关闭。
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myrm_agent_harness.api import SkillAgent
    from myrm_agent_harness.toolkits.browser import BrowserSession

logger = logging.getLogger(__name__)


class ExecutionMode(StrEnum):
    """Whether a run may reuse a chat-scoped built execution unit."""

    POOLED = "pooled"
    EPHEMERAL = "ephemeral"


@dataclass(slots=True)
class BuiltExecutionUnit:
    """Heavy build artifacts kept warm across messages in one chat scope."""

    skill_agent: SkillAgent
    browser_session: BrowserSession | None = None
    desktop_session: object | None = None

    async def teardown(self) -> None:
        """Fully close resources when evicting or deleting a chat scope.

        The unit's agent owns the executor its runs share, and a run's end never closes an executor it was handed, so
        closing it here is what ends the chat's shell.
        """
        if self.browser_session is not None:
            try:
                close = getattr(self.browser_session, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                pass
            finally:
                self.browser_session = None

        if self.desktop_session is not None:
            try:
                close = getattr(self.desktop_session, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            except Exception:
                pass
            finally:
                self.desktop_session = None

        executor = self.skill_agent.executor
        if executor is not None:
            try:
                await executor.close()
            except Exception:
                logger.warning("Executor close failed while tearing down an execution unit", exc_info=True)

        try:
            await self.skill_agent.close()
        except Exception:
            pass
