"""Agent run lifecycle helpers — workspace setup, cleanup, and post-processing.

Stateless functions used by BaseAgent.run() for workspace creation,
run-end cleanup (ContextVar resets, token stats collection), and
post-processing event emission.

[INPUT]
- agent.types::AgentRunStatistics (POS: statistics of one agent run: duration, tool and node counts, token usage, cost, completion status)
- toolkits.code_execution.executors.base::CodeExecutor, close_executor_in_background (POS: Code executor base classes.)
- toolkits.code_execution.workspace.storage_root_bind (POS: ContextVar binding for aggregate workspace filesystem roots during a single agent Task.)
- utils.runtime.steering::SteeringToken (POS: steering token: injects a message while the agent runs to interrupt the current tool chain)
- _internals.message_end_event::build_message_end_event (POS: MESSAGE_END 事件负载)
- _internals.pseudonym_context::init_pseudonym_store (POS: the run's PseudonymStore and memory-write PII closure)

[OUTPUT]
- setup_workspace: Create workspace under host ``workspaces_storage_root``, bind executor, set context vars. The storage-root ContextVar undo token is stashed in a ContextVar (not in ``merged_context``) so checkpoints/msgpack never serialize it. An executor it creates itself (none was handed in) is recorded in a ContextVar as this run's own and closed if the start fails; a handed-in executor is never recorded.
- cleanup_run: Run-end cleanup: cancel children, reset context vars, collect stats, and close the executor this run created itself (an executor handed in stays open: its owner closes it).
- collect_tracker_stats: Extract token usage and cost from the current TokenTracker into the run statistics.
- post_run_events: Yield post-processing events: artifacts, FILE_MUTATION_FAILED, and MESSAGE_END (payload assembly lives in ``message_end_event``).

[POS]
Agent run lifecycle helpers — workspace setup, cleanup, and post-processing.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator, Callable
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from myrm_agent_harness.agent._internals.message_end_event import build_message_end_event
from myrm_agent_harness.agent._internals.pseudonym_context import init_pseudonym_store
from myrm_agent_harness.agent.context_management.infra.cache_metrics_collector import (
    clear_pending_explicit_cache_snapshot,
    install_llm_response_hook,
)
from myrm_agent_harness.agent.event_log.llm_observability import (
    install_llm_observability_hook,
)
from myrm_agent_harness.agent.middlewares.approval import set_workspace_root
from myrm_agent_harness.agent.types import (
    AgentRunStatistics,
    WorkspaceBinding,
    map_to_completion_status,
)
from myrm_agent_harness.toolkits.code_execution import create_workspace_service
from myrm_agent_harness.toolkits.code_execution.executors.base import close_executor_in_background
from myrm_agent_harness.toolkits.code_execution.workspace.storage_root_bind import (
    WORKSPACE_BIND_CTX_KEY,
    bind_workspace_storage_root,
    release_workspace_storage_bind_token,
)
from myrm_agent_harness.utils.logger_utils import get_agent_logger
from myrm_agent_harness.utils.runtime.cancellation import set_cancel_token
from myrm_agent_harness.utils.runtime.progress_sink import set_tool_progress_sink
from myrm_agent_harness.utils.token_economics.tracker import (
    get_token_tracker,
    reset_token_tracker,
)

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.code_execution.executors.base import CodeExecutor
    from myrm_agent_harness.utils.runtime.steering import SteeringToken
    from myrm_agent_harness.utils.token_economics.tracker import TokenTracker

logger = get_agent_logger(__name__)

install_llm_response_hook()
install_llm_observability_hook()

_workspace_bind_handle_stash: ContextVar[object | None] = ContextVar("workspace_bind_handle_stash", default=None)


def _stash_workspace_bind_handle(handle: object) -> None:
    _workspace_bind_handle_stash.set(handle)


def _take_workspace_bind_handle() -> object | None:
    handle = _workspace_bind_handle_stash.get()
    _workspace_bind_handle_stash.set(None)
    return handle


# The executor this run created itself, or None when the host handed one in. Whoever builds an executor closes it, so
# the run's end closes this one and no other. A run's start and end share one context, and a child run's task starts
# from a copy of its parent's: each start therefore records its own answer, None included.
_run_created_executor: ContextVar[CodeExecutor | None] = ContextVar("run_created_executor", default=None)


async def setup_workspace(
    executor: CodeExecutor | None, context: dict[str, object] | None
) -> tuple[dict[str, object], CodeExecutor]:
    """Create workspace, bind executor, and set context vars.

    Returns:
        (merged_context, executor) — context now contains workspace_path.
    """
    if context is None:
        context = {}

    ws_root_raw = context.get("workspaces_storage_root")
    if ws_root_raw is None or isinstance(ws_root_raw, bool):
        raise ValueError(
            "workspaces_storage_root is required (absolute filesystem root containing "
            "`workspaces/`). Host layer must set merged_context.workspaces_storage_root."
        )
    ws_storage_text = str(ws_root_raw).strip()
    if not ws_storage_text:
        raise ValueError("workspaces_storage_root must not be empty.")
    agg_root = Path(ws_storage_text).expanduser().resolve()
    bind_handle = bind_workspace_storage_root(agg_root)
    _stash_workspace_bind_handle(bind_handle)

    binding: WorkspaceBinding | None = context.get("workspace_binding")  # type: ignore

    if binding and binding.root_path:
        # Explicit binding from AgentRuntimeSpec
        workspace_path = binding.root_path
        if binding.mode == "chat" and binding.chat_id:
            # We don't append chat_id here because Server already did it in storage.service
            pass
        elif binding.mode == "background" and binding.task_id:
            import os

            workspace_path = os.path.join(workspace_path, "jobs", binding.task_id)

        import os

        os.makedirs(workspace_path, exist_ok=True)
    else:
        # Resolve via WorkspaceService under the host-provided aggregate root
        raw_session_id = context.get("session_id")
        if not raw_session_id:
            raise ValueError(
                "workspace_binding with root_path, or session_id with workspaces_storage_root, "
                "is required — business layer supplies session_id (e.g., chat_<id>)."
            )

        session_id = str(raw_session_id)
        workspace_svc = create_workspace_service(root_dir=agg_root)
        workspace = await workspace_svc.get_or_create(session_id=session_id)
        workspace_path = workspace_svc.get_workspace_absolute_path(workspace)

    created: CodeExecutor | None = None
    try:
        if executor is None:
            from myrm_agent_harness.toolkits.code_execution import create_executor

            try:
                executor = created = create_executor()
                logger.debug(f" Auto-created Executor: {executor.get_executor_name()}")
            except Exception as e:
                logger.error(f" Failed to create Executor: {e}")
                raise
        else:
            logger.debug(f" Using provided Executor: {executor.get_executor_name()}")

        executor.bind_workspace(workspace_path)
        logger.debug(f" {executor.get_executor_name()}: workspace bound to {workspace_path}")

        from myrm_agent_harness.toolkits.code_execution.executors.base import (
            set_executor,
        )

        set_executor(executor)
        set_workspace_root(workspace_path)
        init_pseudonym_store(workspace_path)

        from myrm_agent_harness.agent.security.workspace_trust.runtime import (
            apply_workspace_trust_for_root,
        )

        trust_level = apply_workspace_trust_for_root(workspace_path)
        logger.debug("Workspace trust applied: %s for %s", trust_level.value, workspace_path)

        from myrm_agent_harness.agent.workspace_rules.tracker import (
            init_subdirectory_tracker,
        )

        init_subdirectory_tracker(workspace_path)
        context["workspace_path"] = workspace_path

        from myrm_agent_harness.toolkits.code_execution.executors.base import (
            stash_executor_for_session,
        )

        session_id = str(context.get("session_id", ""))
        if session_id:
            stash_executor_for_session(session_id, executor)

        from myrm_agent_harness.observability.metrics.agent_metrics import (
            record_ttfa_run_start,
        )

        record_ttfa_run_start()

        # Last in the try: from here on the run's end owns the close, and nothing above can fail after the claim.
        _run_created_executor.set(created)
    except Exception:
        leaked = _take_workspace_bind_handle()
        release_workspace_storage_bind_token(leaked)
        if created is not None:
            close_executor_in_background(created)
        raise

    return context, executor


def cleanup_run(
    stats: AgentRunStatistics,
    start_time: float,
    cancel_token: object | None,
    steering_token: SteeringToken | None,
    cancel_all_fn: Callable[..., int],
    *,
    merged_context: dict[str, object] | None = None,
    include_detached: bool = True,
) -> None:
    """Run-end cleanup: cancel children, reset context vars, collect stats, close the executor this run created.

    An executor the host handed in stays open: whoever built it closes it. Dropping the session's stash entry
    never closes anything.

    Args:
        stats: Mutable statistics object to fill.
        start_time: ``time.time()`` captured at run start.
        cancel_token: Active cancellation token (will be cleared).
        steering_token: Active steering token (will be cleared if set).
        cancel_all_fn: Callable to cancel all child subagents. Accepts
            ``include_detached`` so a normally-finished parent run can keep
            ``wait=False`` background children alive.
        merged_context: Run context carrying workspace bind token resets.
        include_detached: Whether ``cancel_all_fn`` should also cancel children
            spawned with ``wait=False`` (background/detached). A cancelled or
            failed run passes ``True``; a normally-completed run passes
            ``False`` so background subagents outlive the parent stream.
    """
    created_executor = _run_created_executor.get()
    _run_created_executor.set(None)
    try:
        stashed = _take_workspace_bind_handle()
        legacy: object | None = None
        if merged_context is not None:
            legacy = merged_context.pop(WORKSPACE_BIND_CTX_KEY, None)
        for t in (stashed, legacy):
            release_workspace_storage_bind_token(t)

        cancelled_count = cancel_all_fn(include_detached=include_detached)
        if cancelled_count > 0:
            logger.info(f" Cancelled {cancelled_count} running subagents on parent cleanup")

        set_tool_progress_sink(None)
        set_cancel_token(None)
        if steering_token:
            from myrm_agent_harness.utils.runtime.steering import set_steering_token

            set_steering_token(None)
        from myrm_agent_harness.agent.middlewares.approval import (
            set_event_logger,
            set_security_config,
        )

        set_security_config(None)

        set_workspace_root("")

        from myrm_agent_harness.agent.security.workspace_trust.runtime import (
            clear_workspace_trust_runtime,
        )

        clear_workspace_trust_runtime()
        set_event_logger(None)
        from myrm_agent_harness.agent.middlewares._session_context import (
            set_goal_provider,
        )

        set_goal_provider(None)

        from myrm_agent_harness.agent.workspace_rules.tracker import (
            reset_subdirectory_tracker,
        )

        reset_subdirectory_tracker()

        from myrm_agent_harness.agent.middlewares._session_context import (
            set_pseudonym_store,
        )

        set_pseudonym_store(None)

        from myrm_agent_harness.toolkits.memory._internal.memory_scanner import (
            set_pii_pseudonymizer,
        )

        set_pii_pseudonymizer(None)

        from myrm_agent_harness.toolkits.code_execution.executors.base import (
            clear_stashed_executor,
            set_executor,
        )

        set_executor(None)

        if merged_context:
            cleanup_session_id = str(merged_context.get("session_id", ""))
            if cleanup_session_id:
                clear_stashed_executor(cleanup_session_id)

        collect_tracker_stats(stats)

        # Record Prometheus metrics
        from myrm_agent_harness.observability.metrics.registry import metrics_registry

        if metrics_registry.enabled:
            agent_type = "base_agent"  # Can be passed if needed, but we'll use a generic one here
            duration_s = time.time() - start_time
            metrics_registry.record_execution(
                agent_id=agent_type,
                duration_s=duration_s,
                status="success" if not stats.was_cancelled else "cancelled",
            )

            if stats.model_usage:
                for model, usage in stats.model_usage.items():
                    metrics_registry.record_tokens(
                        agent_id=agent_type,
                        model=model,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                    )

        reset_token_tracker()
        clear_pending_explicit_cache_snapshot()

        stats.total_duration_seconds = time.time() - start_time
    except Exception as cleanup_error:
        logger.error(f"Error during cleanup: {cleanup_error}", exc_info=True)
    finally:
        # Last, so a step above that fails never keeps the executor open.
        if created_executor is not None:
            close_executor_in_background(created_executor)


def collect_tracker_stats(stats: AgentRunStatistics, *, tracker: TokenTracker | None = None) -> None:
    """Extract token usage and cost from the current TokenTracker into stats.

    Args:
        stats: Mutable stats object to populate.
        tracker: Explicit tracker reference. Falls back to ContextVar lookup if None.
                 Needed because async generators lose ContextVar state across yields.
    """
    if tracker is None:
        tracker = get_token_tracker()
    if not tracker:
        return

    stats.token_usage = tracker.get_usage()
    stats.cost_usd = tracker.total_cost_usd
    stats.cost_status = tracker.cost_status
    stats.completion_status = map_to_completion_status(tracker.last_finish_reason)

    if tracker.model_usage:
        stats.model_usage = {
            model: {
                **mu.to_dict(),
                "cost_usd": round(tracker.model_cost.get(model, 0.0), 6),
            }
            for model, mu in tracker.model_usage.items()
        }
        stats.primary_model = max(tracker.model_usage, key=lambda m: tracker.model_usage[m].total_tokens)

    if tracker.usage.cached_tokens > 0:
        cache_stats = tracker.usage.get_cache_effectiveness()
        logger.info(
            f" [Session Cache Summary] "
            f"Calls: {tracker.call_count} | "
            f"Hit Rate: {cache_stats['cache_hit_rate']:.1%} | "
            f"Cost Savings: {cache_stats['cost_savings_pct']:.1%} "
            f"({cache_stats['cost_savings_absolute']:.0f} tokens)"
        )

    if tracker.total_cost_usd > 0:
        logger.warning(
            f" [Session Cost] ${tracker.total_cost_usd:.6f} ({tracker.call_count} calls, {tracker.error_count} errors)"
        )


async def post_run_events(
    stats: AgentRunStatistics,
    message_id: str,
    merged_context: dict[str, object],
    collect_artifacts: bool,
    on_artifacts_ready: object | None,
    tracker: TokenTracker | None = None,
) -> AsyncGenerator[dict[str, object]]:
    """Yield post-processing events: artifacts and MESSAGE_END.

    Args:
        tracker: Explicit token tracker reference. Fall back to ContextVar lookup
            if None. Explicit reference is preferred because async generators may
            resume in a different task than the one that initialized the tracker,
            so the ContextVar is not guaranteed to be visible.
    """
    if stats.was_cancelled:
        return

    if collect_artifacts and on_artifacts_ready:
        from myrm_agent_harness.agent.streaming.artifact_events import (
            emit_artifacts_ready_event,
        )

        async for raw_event in emit_artifacts_ready_event(message_id, merged_context):
            processed = await on_artifacts_ready(raw_event)  # type: ignore[operator]
            if processed:
                yield processed

        from myrm_agent_harness.agent.streaming.artifact_events import (
            emit_artifact_focus_event,
        )

        async for focus_event in emit_artifact_focus_event(message_id):
            yield focus_event

    # File Mutation Verifier — emit failure event before MESSAGE_END
    from myrm_agent_harness.agent.middlewares.tooling._mutation_verifier import (
        format_mutation_failures,
    )
    from myrm_agent_harness.agent.streaming.types import AgentEventType

    mutation_payload = format_mutation_failures()
    if mutation_payload:
        yield {
            "type": AgentEventType.FILE_MUTATION_FAILED.value,
            "data": mutation_payload,
            "messageId": message_id,
        }

    yield build_message_end_event(stats, message_id, tracker=tracker)
