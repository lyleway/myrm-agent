"""Static type surface of the lazily-resolved ``myrm_agent_harness.api`` exports.

``__init__.py`` resolves every public name at runtime through ``_EXPORTS`` and ``__getattr__``,
which type checkers can only see as ``object``. This stub mirrors that registry one-to-one so
importers get the real types; ``tests/api/test_public_surface.py`` fails when the two drift.
"""

from myrm_agent_harness.agent.context_management.salient_tool_filter import (
    SalientToolEvidence as SalientToolEvidence,
    SalientToolFilterConfig as SalientToolFilterConfig,
    extract_salient_tool_evidences as extract_salient_tool_evidences,
    strip_ansi_sequences as strip_ansi_sequences,
)
from myrm_agent_harness.agent.event_log import (
    sanitize_event_data as sanitize_event_data,
)
from myrm_agent_harness.agent.middlewares.approval.helpers import (
    reset_denial_counter as reset_denial_counter,
)
from myrm_agent_harness.agent.security.single_use_tickets import (
    SingleUseTicket as SingleUseTicket,
    SingleUseTicketManager as SingleUseTicketManager,
    TicketStatus as TicketStatus,
    TokenType as TokenType,
    compute_content_digest as compute_content_digest,
    get_single_use_ticket_manager as get_single_use_ticket_manager,
)
from myrm_agent_harness.agent.skill_agent.context import (
    track_background_task as track_background_task,
)
from myrm_agent_harness.agent.tool_management.tool_layers import (
    is_registered_action_tool as is_registered_action_tool,
)
from myrm_agent_harness.api.config import (
    AgentConfig as AgentConfig,
    ConfigIncompleteError as ConfigIncompleteError,
    LLMConfig as LLMConfig,
)
from myrm_agent_harness.api.factory import (
    SkillAgent as SkillAgent,
    create_skill_agent as create_skill_agent,
)
from myrm_agent_harness.api.hooks import (
    get_workspace_root as get_workspace_root,
    set_workspace_root as set_workspace_root,
)
from myrm_agent_harness.api.protocols import (
    AgentProfileBackend as AgentProfileBackend,
    CronStore as CronStore,
    HookEvent as HookEvent,
    HookRegistryProtocol as HookRegistryProtocol,
    IntegrationProvider as IntegrationProvider,
    KanbanStore as KanbanStore,
    SkillBackend as SkillBackend,
)
from myrm_agent_harness.api.subagents import (
    build_parent_delegatable_toolkit as build_parent_delegatable_toolkit,
    close_subagent_checkpointer as close_subagent_checkpointer,
    delete_subagent_checkpoint as delete_subagent_checkpoint,
    drop_subagent_checkpoint_if_terminal as drop_subagent_checkpoint_if_terminal,
    get_subagent_checkpointer as get_subagent_checkpointer,
)
from myrm_agent_harness.api.types import (
    AgentEventType as AgentEventType,
    AgentRuntimeConfig as AgentRuntimeConfig,
    AgentRuntimeSpec as AgentRuntimeSpec,
    AgentStreamEvent as AgentStreamEvent,
    CompletionStatus as CompletionStatus,
)
from myrm_agent_harness.backends.secrets import (
    ExternalSecretResolutionError as ExternalSecretResolutionError,
    invalidate_external_secret as invalidate_external_secret,
    is_external_secret_reference as is_external_secret_reference,
    resolve_external_secret as resolve_external_secret,
)
from myrm_agent_harness.backends.skills.scanning.rescan_engine import (
    AdvisoryAck as AdvisoryAck,
    AdvisoryAckRegistry as AdvisoryAckRegistry,
    InstalledSkillRescanEngine as InstalledSkillRescanEngine,
    SkillRescanResult as SkillRescanResult,
)
from myrm_agent_harness.backends.skills.workflow_compiler import (
    WorkflowIntentPlan as WorkflowIntentPlan,
    WorkflowSkillCompiler as WorkflowSkillCompiler,
)
from myrm_agent_harness.core.artifacts.fact_check import (
    ConflictSeverity as ConflictSeverity,
    FactCheckItem as FactCheckItem,
    FactCheckSheet as FactCheckSheet,
    ResolutionStatus as ResolutionStatus,
    SourceClaim as SourceClaim,
)
from myrm_agent_harness.core.message_origin import (
    last_user_authored_index as last_user_authored_index,
    runtime_human_message as runtime_human_message,
)
from myrm_agent_harness.core.security.bounded_communicate import (
    communicate_bounded as communicate_bounded,
)
from myrm_agent_harness.core.security.ephemeral_credentials import (
    EphemeralCredential as EphemeralCredential,
    EphemeralCredentialStore as EphemeralCredentialStore,
    EphemeralCredentialSummary as EphemeralCredentialSummary,
    command_digest as command_digest,
    get_ephemeral_credential_store as get_ephemeral_credential_store,
    validate_credential_key as validate_credential_key,
)
from myrm_agent_harness.core.security.guards.privacy_ladder import (
    PrivacyFailClosedLadder as PrivacyFailClosedLadder,
    PrivacyFailClosedViolationError as PrivacyFailClosedViolationError,
    PrivacyLadderVerdict as PrivacyLadderVerdict,
    PrivacyLadderViolationType as PrivacyLadderViolationType,
    PrivacyScope as PrivacyScope,
)
from myrm_agent_harness.core.security.http.redirect_guard import (
    InsecureRedirectSecurityError as InsecureRedirectSecurityError,
    Origin as Origin,
)
from myrm_agent_harness.core.security.integrity.seal import (
    SEAL_FILENAME as SEAL_FILENAME,
    SEAL_MAGIC_HEADER as SEAL_MAGIC_HEADER,
    FileChecksum as FileChecksum,
    IntegritySealer as IntegritySealer,
    IntegrityStatus as IntegrityStatus,
    IntegrityVerificationResult as IntegrityVerificationResult,
    SealManifest as SealManifest,
)
from myrm_agent_harness.core.security.missing_semantics import (
    MissingDependencyFailClosedError as MissingDependencyFailClosedError,
    MissingDependencyFailFastError as MissingDependencyFailFastError,
    MissingSemanticsBlockedError as MissingSemanticsBlockedError,
    MissingSemanticsContract as MissingSemanticsContract,
    MissingSemanticsDecision as MissingSemanticsDecision,
    MissingSemanticsError as MissingSemanticsError,
    MissingSemanticsPolicy as MissingSemanticsPolicy,
    SemanticsCategory as SemanticsCategory,
    evaluate_missing_capability as evaluate_missing_capability,
)
from myrm_agent_harness.core.security.path import (
    is_within_boundary as is_within_boundary,
    safe_join_path as safe_join_path,
)
from myrm_agent_harness.core.security.privacy.ladder import (
    PrivacyLadderLevel as PrivacyLadderLevel,
    PrivacyLadderScanResult as PrivacyLadderScanResult,
    PrivacyLadderValidator as PrivacyLadderValidator,
    PrivacyLadderViolation as PrivacyLadderViolation,
    PrivacyScanVerdict as PrivacyScanVerdict,
)
from myrm_agent_harness.core.security.process_env import (
    EnvInheritPolicy as EnvInheritPolicy,
    build_isolated_child_env as build_isolated_child_env,
)
from myrm_agent_harness.core.security.redact.command_line import (
    command_preview as command_preview,
)
from myrm_agent_harness.core.security.redact.engine import (
    escape_invisible_unicode as escape_invisible_unicode,
    redact_field_text as redact_field_text,
    redact_sensitive_text as redact_sensitive_text,
)
from myrm_agent_harness.core.security.redact.nested import (
    redact_nested as redact_nested,
)
from myrm_agent_harness.core.security.tool_registry.registry import (
    MCPAnnotations as MCPAnnotations,
    SafetyMetadata as SafetyMetadata,
    get_ptc_safety_metadata as get_ptc_safety_metadata,
    register_ptc_safety_metadata as register_ptc_safety_metadata,
    unregister_ptc_safety_metadata as unregister_ptc_safety_metadata,
)
from myrm_agent_harness.core.skill import (
    DEFAULT_ALLOWED_TOOLS as DEFAULT_ALLOWED_TOOLS,
)
from myrm_agent_harness.eval.assertions import (
    calculate_trajectory_determinism as calculate_trajectory_determinism,
)
from myrm_agent_harness.eval.contracts import (
    evaluate_five_contract_progress as evaluate_five_contract_progress,
)
from myrm_agent_harness.infra.data_dir import (
    resolve_data_dir as resolve_data_dir,
    resolve_workspace_state_dir as resolve_workspace_state_dir,
)
from myrm_agent_harness.infra.git import (
    GitMetadata as GitMetadata,
    resolve_git_branch as resolve_git_branch,
    resolve_git_metadata as resolve_git_metadata,
)
from myrm_agent_harness.observability.audit_trail import (
    ComplianceOutcome as ComplianceOutcome,
    ComplianceTrailExporter as ComplianceTrailExporter,
    DualTrackAuditCollector as DualTrackAuditCollector,
)
from myrm_agent_harness.observability.digest.health_evaluator import (
    SkillHealthEvaluator as SkillHealthEvaluator,
)
from myrm_agent_harness.observability.digest.types import (
    SkillCompoundingMetrics as SkillCompoundingMetrics,
    SkillHealthScore as SkillHealthScore,
    SkillHealthStatus as SkillHealthStatus,
)
from myrm_agent_harness.observability.spend_control import (
    FleetQuotaItem as FleetQuotaItem,
    FourTierSpendControlEngine as FourTierSpendControlEngine,
    InterventionAction as InterventionAction,
    SpendControlConfig as SpendControlConfig,
    SpendInterventionDecision as SpendInterventionDecision,
    SpendInterventionTier as SpendInterventionTier,
)
from myrm_agent_harness.runtime.context.transcripts import (
    CanonicalToolCall as CanonicalToolCall,
    CanonicalTranscriptTurn as CanonicalTranscriptTurn,
    CanonicalTurnRole as CanonicalTurnRole,
    ClaudeTranscriptParser as ClaudeTranscriptParser,
    CodexTranscriptParser as CodexTranscriptParser,
    SandboxPathRemapper as SandboxPathRemapper,
    ToolOutputCompactor as ToolOutputCompactor,
    TranscriptParseResult as TranscriptParseResult,
)
from myrm_agent_harness.runtime.context.tree_state import (
    fold_branch_todo_state as fold_branch_todo_state,
)
from myrm_agent_harness.runtime.diagnostics.compliance import (
    ComplianceAuditEngine as ComplianceAuditEngine,
    ComplianceReport as ComplianceReport,
    ComplianceStatus as ComplianceStatus,
    ComplianceViolation as ComplianceViolation,
)
from myrm_agent_harness.runtime.diagnostics.doctor import (
    Doctor as Doctor,
)
from myrm_agent_harness.runtime.install_guard.probe import (
    get_distribution_mode as get_distribution_mode,
    is_compiled_distribution as is_compiled_distribution,
)
from myrm_agent_harness.toolkits.browser.doctor.orphans import (
    cleanup_orphan_processes as cleanup_orphan_processes,
    find_orphan_automation_processes as find_orphan_automation_processes,
)
from myrm_agent_harness.toolkits.browser.session import (
    RestrictedAccessibilityBrowserSession as RestrictedAccessibilityBrowserSession,
    create_restricted_ax_browser_tools as create_restricted_ax_browser_tools,
)
from myrm_agent_harness.toolkits.browser.utils.page_health import (
    is_driver_closed_error as is_driver_closed_error,
)
from myrm_agent_harness.toolkits.code_execution.git_digest import (
    RepoCommitItem as RepoCommitItem,
    RepoHistoryEvidenceDigest as RepoHistoryEvidenceDigest,
    extract_repo_history_digest as extract_repo_history_digest,
)
from myrm_agent_harness.toolkits.code_execution.security.workspace_snapshot import (
    WorkspaceSnapshotResult as WorkspaceSnapshotResult,
    create_workspace_snapshot as create_workspace_snapshot,
    rollback_workspace_snapshot as rollback_workspace_snapshot,
)
from myrm_agent_harness.toolkits.computer_use.dref.types import (
    is_secure_role as is_secure_role,
)
from myrm_agent_harness.toolkits.computer_use.recording import (
    DesktopCaptureDriver as DesktopCaptureDriver,
    DesktopRecordedEvent as DesktopRecordedEvent,
    SynthesizedSkillDraft as SynthesizedSkillDraft,
    cluster_and_debounce_events as cluster_and_debounce_events,
    synthesize_desktop_skill_draft as synthesize_desktop_skill_draft,
)
from myrm_agent_harness.toolkits.computer_use.recording.types import (
    RecordedActionType as RecordedActionType,
)
from myrm_agent_harness.toolkits.cron.engine.connector_health import (
    ConnectorErrorCategory as ConnectorErrorCategory,
    classify_connector_error as classify_connector_error,
    delivery_target_key as delivery_target_key,
    redact_connector_url as redact_connector_url,
)
from myrm_agent_harness.toolkits.cron.engine.fingerprint import (
    compute_workflow_fingerprint as compute_workflow_fingerprint,
)
from myrm_agent_harness.toolkits.cron.stores import (
    InMemoryCronStore as InMemoryCronStore,
)
from myrm_agent_harness.toolkits.kanban.stores import (
    InMemoryKanbanStore as InMemoryKanbanStore,
)
from myrm_agent_harness.toolkits.llms.probe import (
    ProviderBalanceProbeProtocol as ProviderBalanceProbeProtocol,
    ProviderBalanceResult as ProviderBalanceResult,
    ProviderBalanceStatus as ProviderBalanceStatus,
)
from myrm_agent_harness.toolkits.llms.routing.complexity_router import (
    route_task as route_task,
)
from myrm_agent_harness.toolkits.llms.routing.specialty_router import (
    TaskSpecialty as TaskSpecialty,
    route_task_specialty as route_task_specialty,
)
from myrm_agent_harness.toolkits.mcp.tool_processing import (
    evict_tool_annotations as evict_tool_annotations,
)
from myrm_agent_harness.toolkits.memory.consolidation import (
    TaskSessionSnapshot as TaskSessionSnapshot,
    create_consolidation_cleanup_task as create_consolidation_cleanup_task,
)
from myrm_agent_harness.toolkits.memory.strategies.behavioral_measurement import (
    BehavioralMessage as BehavioralMessage,
    BehavioralStatsOptions as BehavioralStatsOptions,
    RoutineMeasurement as RoutineMeasurement,
    compute_routine_measurement as compute_routine_measurement,
    generate_behavioral_profile_candidates as generate_behavioral_profile_candidates,
    resolve_utc_offset_minutes as resolve_utc_offset_minutes,
)
from myrm_agent_harness.toolkits.memory.strategies.distillation_guards import (
    DistillationCandidate as DistillationCandidate,
    DistillationOrigin as DistillationOrigin,
    DistillationRejectionCode as DistillationRejectionCode,
    SelfIdentityState as SelfIdentityState,
    check_distillable as check_distillable,
    filter_distillable_messages as filter_distillable_messages,
    filter_memories_with_evidence as filter_memories_with_evidence,
    is_alert_or_bot_sender as is_alert_or_bot_sender,
)
from myrm_agent_harness.toolkits.memory.strategies.incremental_transcript import (
    IncrementalTranscriptParser as IncrementalTranscriptParser,
    TranscriptIncrementalChunk as TranscriptIncrementalChunk,
    TranscriptTurn as TranscriptTurn,
)
from myrm_agent_harness.toolkits.memory.strategies.merger import (
    ConfidenceEvolutionEngine as ConfidenceEvolutionEngine,
    ConflictItem as ConflictItem,
)
from myrm_agent_harness.toolkits.memory.types import (
    EvidenceReference as EvidenceReference,
    HitSource as HitSource,
    RecallDebugTrace as RecallDebugTrace,
)
from myrm_agent_harness.toolkits.retriever.cjk_tokenizer import (
    build_cjk_index_segment as build_cjk_index_segment,
    build_cjk_query_token_tiers as build_cjk_query_token_tiers,
    build_cjk_query_tokens as build_cjk_query_tokens,
    tokenize_cjk_bigram as tokenize_cjk_bigram,
)
from myrm_agent_harness.toolkits.retriever.fusion_strategies import (
    FusedHit as FusedHit,
    RankedList as RankedList,
    RecallDebug as RecallDebug,
    fuse_rrf_deterministic as fuse_rrf_deterministic,
)
from myrm_agent_harness.toolkits.ssh_remote import (
    ReadOnlySSHValidator as ReadOnlySSHValidator,
    SSHCommandResult as SSHCommandResult,
    SSHHostSpec as SSHHostSpec,
    SSHRemoteExecutor as SSHRemoteExecutor,
)
from myrm_agent_harness.toolkits.vector.quantization import (
    QuantizedVector as QuantizedVector,
    cosine_similarity_int8 as cosine_similarity_int8,
    dequantize_int8 as dequantize_int8,
    quantize_int8 as quantize_int8,
)
from myrm_agent_harness.toolkits.wiki.retrieval.fts_search import (
    index_has_pages as index_has_pages,
    search_published_index as search_published_index,
)
from myrm_agent_harness.toolkits.wiki.retrieval.graph_tables import (
    is_interim_raw_entry as is_interim_raw_entry,
)
from myrm_agent_harness.toolkits.wiki.retrieval.tokenizer import (
    STOP_WORDS as STOP_WORDS,
    tokenize_for_fts as tokenize_for_fts,
)
from myrm_agent_harness.utils.db.fts5 import (
    CjkFtsQueryPlanner as CjkFtsQueryPlanner,
)
from myrm_agent_harness.utils.runtime.cancel_hook import (
    on_token_cancelled as on_token_cancelled,
)

__all__: list[str]
