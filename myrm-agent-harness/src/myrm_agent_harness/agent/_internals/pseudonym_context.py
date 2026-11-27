"""Pseudonymization context of one agent run — the PseudonymStore and the memory-write PII closure.

[INPUT]
- agent.middlewares._session_context::get_privacy_policy, set_pseudonym_store (POS: Middleware session context — shared ContextVars for the middleware chain.)
- agent.security.detection.pseudonym_store::get_pseudonym_store (POS: Local SQLite store for reversible PII pseudonymization.)
- core.security.detection.pseudonym_store::PseudonymStore (POS: Local SQLite store for reversible PII pseudonymization.)
- core.security.detection.custom_rules::custom_rules (POS: Single definition of custom-term matching for the privacy policy.)
- core.security.detection.pii_classifier::classify_content (POS: Input-side PII classification engine.)
- core.security.detection.pseudonymizer::pseudonymize_text (POS: Reversible PII pseudonymization engine.)
- core.security.types::PIIAction, PrivacyPolicy, SensitivityLevel (POS: the privacy policy and its sensitivity levels)
- toolkits.memory._internal.memory_scanner::set_pii_pseudonymizer (POS: Memory write-path security scanner.)

[OUTPUT]
- init_pseudonym_store: Initialize the context-local PseudonymStore and regex PII closure when the active PrivacyPolicy requires one (``needs_pseudonym_store``); consumed by ``setup_workspace`` and by skill-agent session-end privacy re-establishment.
- register_pii_pseudonymizer: Build the regex PII pseudonymization closure over a store and install it for memory writes; consumed by ``init_pseudonym_store`` and by ``api.hooks.install_memory_pseudonymizer``.

[POS]
Privacy context a run opens at its start: the store that hands out pseudonyms and the closure that memory writes
pseudonymize through. Both are context-local, so the run's end clears them and fire-and-forget tasks created during the
run inherit them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from myrm_agent_harness.utils.logger_utils import get_agent_logger

if TYPE_CHECKING:
    from myrm_agent_harness.core.security.detection.pseudonym_store import PseudonymStore

logger = get_agent_logger(__name__)


def init_pseudonym_store(workspace_path: str) -> None:
    """Initialize PseudonymStore and the PII closure for the current context.

    Reads the active PrivacyPolicy from the current task context and, when the
    policy requires a shared store (``needs_pseudonym_store``: S2/S3
    PSEUDONYMIZE or deep PII scan), creates the store and registers the regex PII
    pseudonymizer as a context-local closure (inherited by fire-and-forget
    memory-write tasks created afterwards). Deep scan needs the store even when
    S2/S3 use REDACT, because non-structured PII is always pseudonymized through
    PseudonymStore.
    """
    from myrm_agent_harness.agent.middlewares._session_context import (
        get_privacy_policy,
        set_pseudonym_store,
    )

    policy = get_privacy_policy()
    if not policy.enabled:
        return
    if not policy.needs_pseudonym_store:
        return

    from myrm_agent_harness.agent.security.detection.pseudonym_store import (
        get_pseudonym_store,
    )

    db_path = str(Path(workspace_path).parent / "pseudonym_store.db")
    store = get_pseudonym_store(db_path)
    set_pseudonym_store(store)
    logger.info("PseudonymStore initialized at %s", db_path)

    register_pii_pseudonymizer(policy, store)


def register_pii_pseudonymizer(policy: object, store: PseudonymStore | None) -> None:
    """Build and register a PII pseudonymization closure for memory_scanner."""
    from myrm_agent_harness.core.security.detection.custom_rules import custom_rules
    from myrm_agent_harness.core.security.detection.pii_classifier import classify_content
    from myrm_agent_harness.core.security.detection.pseudonymizer import (
        pseudonymize_text,
    )
    from myrm_agent_harness.core.security.types import (
        PIIAction,
        PrivacyPolicy,
        SensitivityLevel,
    )
    from myrm_agent_harness.toolkits.memory._internal.memory_scanner import (
        set_pii_pseudonymizer,
    )

    if not isinstance(policy, PrivacyPolicy):
        return

    def _pseudonymize(text: str) -> str:
        if not policy.enabled:
            return text
        has_pseudonymize = policy.s2_action == PIIAction.PSEUDONYMIZE or policy.s3_action == PIIAction.PSEUDONYMIZE
        if not has_pseudonymize or store is None:
            return text

        pii_result = classify_content(text, policy)
        if pii_result.level == SensitivityLevel.S1:
            return text

        levels_to_process: list[SensitivityLevel] = [pii_result.level]
        if pii_result.level == SensitivityLevel.S3 and policy.s2_action == PIIAction.PSEUDONYMIZE:
            levels_to_process.append(SensitivityLevel.S2)

        result = text
        total_count = 0
        for level in levels_to_process:
            action = policy.s3_action if level == SensitivityLevel.S3 else policy.s2_action
            if action != PIIAction.PSEUDONYMIZE:
                continue
            # The level's custom terms are pseudonymized with its built-in detectors, so memory never keeps them.
            ps_result = pseudonymize_text(result, store, level, custom_rules(policy, level))
            if ps_result.count > 0:
                result = ps_result.text
                total_count += ps_result.count

        if total_count > 0:
            logger.warning(
                "[MEMORY_SCAN] Pseudonymized %d PII items in memory content",
                total_count,
            )
        return result

    set_pii_pseudonymizer(_pseudonymize)
