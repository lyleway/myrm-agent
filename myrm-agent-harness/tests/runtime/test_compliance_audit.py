"""Tests for compliance audit engine."""

from myrm_agent_harness.core.security.tool_registry.registry import (
    MCPAnnotations,
    SafetyMetadata,
    register_ptc_safety_metadata,
)
from myrm_agent_harness.runtime.diagnostics.compliance import (
    ComplianceAuditEngine,
    ComplianceStatus,
)
from myrm_agent_harness.toolkits.mcp.tool_processing import evict_tool_annotations


def test_compliance_audit_engine_clean():
    # Initial state clean
    report = ComplianceAuditEngine.evaluate_full_compliance()
    assert report.status == ComplianceStatus.COMPLIANT
    assert report.compliance_score == 100
    assert report.is_fully_compliant is True
    assert len(report.violations) == 0

    d = report.to_dict()
    assert d["status"] == "compliant"
    assert d["compliance_score"] == 100


def test_compliance_audit_detects_tools_of_inactive_server():
    server_name = "ghost-plugin-alpha"
    register_ptc_safety_metadata("mcp__ghost_plugin_alpha__ghost_tool", SafetyMetadata(), MCPAnnotations())

    try:
        # The ghost server is among the active ones: its tools are expected
        report = ComplianceAuditEngine.evaluate_full_compliance(active_server_names=[server_name])
        assert report.is_fully_compliant is True

        eviction_needed_report = ComplianceAuditEngine.evaluate_full_compliance(
            active_server_names=["authorized-plugin-beta"]
        )
        assert eviction_needed_report.status in (
            ComplianceStatus.WARNING,
            ComplianceStatus.NON_COMPLIANT,
        )
        assert eviction_needed_report.compliance_score < 100
        assert eviction_needed_report.is_fully_compliant is False
        assert [v.target for v in eviction_needed_report.violations] == ["ghost_plugin_alpha"]
        assert eviction_needed_report.questions["Q2_REGISTRY_CLEAN"] is False
    finally:
        assert evict_tool_annotations(server_name) == 1

    # After eviction, clean again
    clean_report = ComplianceAuditEngine.evaluate_full_compliance(active_server_names=["authorized-plugin-beta"])
    assert clean_report.is_fully_compliant is True


def test_compliance_audit_does_not_take_a_server_for_the_one_it_prefixes():
    """Server ``a`` being active must not hide that server ``a_b`` is not."""
    register_ptc_safety_metadata("mcp__a_b__tool", SafetyMetadata(), MCPAnnotations())

    report = ComplianceAuditEngine.evaluate_full_compliance(active_server_names=["a"])

    assert [v.target for v in report.violations] == ["a_b"]


def test_compliance_audit_without_active_servers_has_nothing_to_compare():
    register_ptc_safety_metadata("mcp__ghost__tool", SafetyMetadata(), MCPAnnotations())

    assert ComplianceAuditEngine.evaluate_full_compliance().is_fully_compliant is True


def test_compliance_audit_custom_business_checks():
    custom_checks = [
        ("Q3_CRON_CLEAN", False, "orphan_cron_job_123", "Found orphan cron job for removed plugin"),
    ]
    report = ComplianceAuditEngine.evaluate_full_compliance(custom_checks=custom_checks)
    assert report.status == ComplianceStatus.WARNING
    assert report.questions["Q3_CRON_CLEAN"] is False
    assert len(report.violations) == 1
    assert report.violations[0].question_id == "Q3_CRON_CLEAN"
