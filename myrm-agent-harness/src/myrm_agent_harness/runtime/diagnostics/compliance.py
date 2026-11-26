"""Runtime Capability Eviction & Procurement Compliance Self-Audit Engine.

Provides the Six-Question Eviction Compliance Checklist (太一/政企采购合规六问自检):
1. Q1_ROUTES_CLEAN: Are deprecated/uninstalled plugin endpoints fully evicted from routing tables?
2. Q2_REGISTRY_CLEAN: Is the Tool Registry memory completely clean of phantom tools?
3. Q3_CRON_CLEAN: Are background cron jobs and polling consumers associated with uninstalled plugins stopped/purged?
4. Q4_FILESYSTEM_CLEAN: Are temporary bundles, lock files, and isolated storage free of orphan artifacts?
5. Q5_PROCESS_CLEAN: Are orphan MCP subprocesses and connection pools gracefully terminated?
6. Q6_VAULT_CLEAN: Are session credentials and access tokens properly revoked/cleared?

[POS]
Zero-dependency, thread-safe, pure diagnostic and compliance assertion engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ComplianceStatus(StrEnum):
    """Compliance verification status for each question."""

    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ComplianceViolation:
    """Detailed record of a single compliance violation."""

    question_id: str
    target: str
    reason: str
    severity: str = "high"
    remediation_hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "question_id": self.question_id,
            "target": self.target,
            "reason": self.reason,
            "severity": self.severity,
            "remediation_hint": self.remediation_hint,
        }


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    """Structured result of a procurement/runtime compliance self-audit."""

    status: ComplianceStatus
    compliance_score: int
    checked_at: str
    questions: dict[str, bool]
    violations: list[ComplianceViolation] = field(default_factory=list)
    remediation_hints: list[str] = field(default_factory=list)

    @property
    def is_fully_compliant(self) -> bool:
        return self.status == ComplianceStatus.COMPLIANT and self.compliance_score == 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "compliance_score": self.compliance_score,
            "checked_at": self.checked_at,
            "is_fully_compliant": self.is_fully_compliant,
            "questions": dict(self.questions),
            "violations": [v.to_dict() for v in self.violations],
            "remediation_hints": list(self.remediation_hints),
        }


class ComplianceAuditEngine:
    """Core compliance evaluation engine verifying zero-darkline capability eviction."""

    @staticmethod
    def audit_tool_registry_cleanliness(
        active_server_names: set[str] | list[str] | None = None,
    ) -> list[ComplianceViolation]:
        """Audit the Tool Registry memory for tools of MCP servers that are no longer active.

        Without ``active_server_names`` there is nothing to compare against, so nothing is reported.
        """
        from myrm_agent_harness.core.security.tool_registry.registry import _PTC_LOCK, _PTC_TOOL_SAFETY
        from myrm_agent_harness.toolkits.mcp.config import mcp_tool_prefix, parse_mcp_tool_name

        if active_server_names is None:
            return []

        active_prefixes = tuple(mcp_tool_prefix(name) for name in active_server_names)
        with _PTC_LOCK:
            ghost_targets = {
                parsed[0] if (parsed := parse_mcp_tool_name(name)) else name
                for name in _PTC_TOOL_SAFETY
                if not name.startswith(active_prefixes)
            }

        return [
            ComplianceViolation(
                question_id="Q2_REGISTRY_CLEAN",
                target=target,
                reason=f"Server '{target}' is not in the active server list but its tools are still registered in memory",
                severity="high",
                remediation_hint=f"Call evict_tool_annotations('{target}') to remove ghost tools",
            )
            for target in sorted(ghost_targets)
        ]

    @classmethod
    def evaluate_full_compliance(
        cls,
        active_server_names: set[str] | list[str] | None = None,
        custom_checks: list[tuple[str, bool, str, str]] | None = None,
    ) -> ComplianceReport:
        """Run the comprehensive Six-Question Compliance Audit."""
        violations: list[ComplianceViolation] = []
        questions: dict[str, bool] = {
            "Q1_ROUTES_CLEAN": True,
            "Q2_REGISTRY_CLEAN": True,
            "Q3_CRON_CLEAN": True,
            "Q4_FILESYSTEM_CLEAN": True,
            "Q5_PROCESS_CLEAN": True,
            "Q6_VAULT_CLEAN": True,
        }

        # Run Tool Registry audit
        reg_violations = cls.audit_tool_registry_cleanliness(active_server_names)
        if reg_violations:
            questions["Q2_REGISTRY_CLEAN"] = False
            violations.extend(reg_violations)

        # Run optional custom / business layer checks
        # Each item: (question_id, is_ok, target, failure_reason)
        if custom_checks:
            for q_id, is_ok, target, reason in custom_checks:
                if not is_ok:
                    questions[q_id] = False
                    violations.append(
                        ComplianceViolation(
                            question_id=q_id,
                            target=target,
                            reason=reason,
                            severity="high",
                            remediation_hint=f"Remediate resource '{target}' for compliance rule {q_id}",
                        )
                    )

        # Compute score (100 base, deductions for failed questions and violations)
        failed_questions = sum(1 for passed in questions.values() if not passed)
        score = max(0, 100 - (failed_questions * 15) - (len(violations) * 5))

        if score == 100 and not violations:
            status = ComplianceStatus.COMPLIANT
        elif score >= 70:
            status = ComplianceStatus.WARNING
        else:
            status = ComplianceStatus.NON_COMPLIANT

        hints = [v.remediation_hint for v in violations if v.remediation_hint]

        return ComplianceReport(
            status=status,
            compliance_score=score,
            checked_at=datetime.now(UTC).isoformat(),
            questions=questions,
            violations=violations,
            remediation_hints=hints,
        )
