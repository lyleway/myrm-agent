"""Host model-call proxy for artifact apps with the ``ai`` capability.

[INPUT]
- app.services.artifact_apps.ai_usage (POS: Per-app daily spend ledger)
- app.services.budget.enforcer (POS: should_block_execution and the process-wide budget guard)
- myrm_agent_harness.utils.token_economics (POS: compute_cost_by_tokens, per-call TokenTracker)
- myrm_agent_harness.utils.chat_utils::extract_answer_text (POS: User-visible text of a model reply)

[OUTPUT]
- AiRequest / AiResult / AiRejected: the proxy's inputs, output and refusal.
- run_ai: one plain completion on the user's model, charged to the app and to the global budget.
- MAX_PROMPT_CHARS / MAX_SYSTEM_CHARS / MAX_OUTPUT_TOKENS / DEFAULT_OUTPUT_TOKENS: request limits shared with the API schema.

[POS]
Business layer. A sandboxed page has no key and no network, so the host asks the model for it, with the user's configured
model, no tools and no conversation, and only the text it was given. The spend is bounded three ways: the user's overall
budget (an app call does not get the finalization reserve an agent run wraps up with, and every call counts toward the
day), a daily cap per app, and the call's own upper bound (prompt size and output cap priced up front, so a call cannot
overshoot what was reserved for it). A model without a known price is refused: its cost could not be counted, so
nothing could bound it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Final, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from myrm_agent_harness.utils.chat_utils import extract_answer_text
from myrm_agent_harness.utils.token_economics.budget_guard import BudgetStatus
from myrm_agent_harness.utils.token_economics.cost_engine import compute_cost_by_tokens
from myrm_agent_harness.utils.token_economics.tracker import init_token_tracker, reset_token_tracker
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.artifact_apps import ai_usage
from app.services.budget.enforcer import get_budget_guard, get_budget_guard_sync, should_block_execution

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS: Final = 16_000
MAX_SYSTEM_CHARS: Final = 4_000
MAX_OUTPUT_TOKENS: Final = 4_096
DEFAULT_OUTPUT_TOKENS: Final = 1_024

# What one app may spend per budget day, whatever the user's overall budget allows.
APP_DAILY_BUDGET_USD: Final = 1.0
CALL_TIMEOUT_S: Final = 60.0
# Per-message framing a provider adds on top of the text.
_FRAMING_TOKENS: Final = 32


class ChatModel(Protocol):
    async def ainvoke(self, input: list[BaseMessage], *, max_tokens: int) -> BaseMessage: ...


class AiRejected(Exception):
    """The call was refused or failed; ``code`` is the stable vocabulary the host maps to the page."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AiRequest:
    prompt: str
    system: str | None
    max_tokens: int


@dataclass(frozen=True, slots=True)
class AiResult:
    text: str
    cost_usd: float


def _model_identity(llm: object) -> tuple[str, str | None]:
    name = getattr(llm, "model_name", None) or getattr(llm, "model", None) or ""
    return str(name), getattr(llm, "custom_llm_provider", None)


def _upper_bound_cost(llm: object, request: AiRequest) -> float:
    """Price of the largest call this request can make; a model without a price is refused."""
    model, provider = _model_identity(llm)
    # Bytes, not characters: a token never covers less than a byte of the text it was cut from.
    prompt_tokens = len(request.prompt.encode()) + len((request.system or "").encode()) + _FRAMING_TOKENS
    cost = compute_cost_by_tokens(model, prompt_tokens, request.max_tokens, custom_llm_provider=provider)
    if cost.usd <= 0:
        raise AiRejected("unavailable", "The current model has no known price, so its cost cannot be limited")
    return cost.usd


async def _budget_allows(cost: float) -> bool:
    if await should_block_execution():
        return False
    guard = await get_budget_guard()
    return guard is None or guard.check_budget(cost) in (BudgetStatus.OK, BudgetStatus.WARNING)


async def run_ai(db: AsyncSession, artifact_id: str, request: AiRequest, llm: ChatModel) -> AiResult:
    """Run one completion for the app; raises ``AiRejected`` instead of returning a failure."""
    reserved = _upper_bound_cost(llm, request)
    if not await _budget_allows(reserved):
        raise AiRejected("budget_exceeded", "The daily budget leaves no room for this call")
    charge_id = await ai_usage.reserve(db, artifact_id, reserved, APP_DAILY_BUDGET_USD)
    if charge_id is None:
        raise AiRejected("budget_exceeded", "This app has used its budget for today")

    messages: list[BaseMessage] = [HumanMessage(request.prompt)]
    if request.system:
        messages.insert(0, SystemMessage(request.system))
    tracker = init_token_tracker()
    # Stays at the reservation unless the call completes and reports what it cost: a cancelled or failed call has
    # already been paid for at most that much.
    charged = reserved
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            reply = await llm.ainvoke(messages, max_tokens=request.max_tokens)
        if tracker.total_cost_usd > 0:
            charged = tracker.total_cost_usd
        await ai_usage.settle(db, charge_id, charged)
        return AiResult(extract_answer_text(reply).strip(), charged)
    except TimeoutError as exc:
        raise AiRejected("timeout", "The model did not answer in time") from exc
    except Exception as exc:
        logger.warning("Artifact app model call failed (artifact=%s): %s", artifact_id, type(exc).__name__)
        raise AiRejected("upstream_error", "The model call failed") from exc
    finally:
        reset_token_tracker()
        guard = get_budget_guard_sync()
        if guard is not None:
            guard.record_cost(charged)
