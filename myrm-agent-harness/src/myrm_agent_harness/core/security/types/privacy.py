"""Privacy types: sensitivity levels, the PII actions, the per-session policy and privacy-aware model routing.

[INPUT]
- (none — pure types, zero dependencies)

[OUTPUT]
- SensitivityLevel: S1/S2/S3 privacy classification
- PIIAction: warn / redact / pseudonymize / block
- PrivacyPolicy: PII protection configuration
- PrivacyRoutingConfig: privacy-aware model routing configuration

[POS]
How content is classified and what happens to each class. Imports nothing from the security subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class SensitivityLevel(StrEnum):
    """Three-tier privacy classification for content.

    S1: public — safe to send to cloud LLM, no action needed.
    S2: sensitive — contains PII (phone, email, etc.), action per policy.
    S3: confidential — contains strong-sensitive data (ID card, bank card,
        API keys), must be redacted or blocked.
    """

    S1 = "s1"
    S2 = "s2"
    S3 = "s3"


class PIIAction(StrEnum):
    """How to handle content at a given sensitivity level.

    WARN: log but do not alter content.
    REDACT: irreversibly mask PII (e.g. 138****8000). AI loses semantic info.
    PSEUDONYMIZE: reversibly replace PII with typed placeholders
        (e.g. <PHONE_NUMBER_1>). AI retains semantic understanding,
        original values restored locally before user sees the response.
    BLOCK: reject the message entirely.
    """

    WARN = "warn"
    REDACT = "redact"
    PSEUDONYMIZE = "pseudonymize"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    """PII protection configuration for an Agent session.

    Controls whether PII detection is active, which actions to take for
    S2/S3 content, and optional custom keywords/patterns/tools/paths
    that extend the built-in detection rules.

    When ``deep_scan`` is True, an LLM-based detector supplements the
    regex engine to catch non-structured PII (medical history, family
    relations, workplace, etc.) that regex cannot match.
    """

    enabled: bool = False
    s2_action: PIIAction = PIIAction.WARN
    s3_action: PIIAction = PIIAction.REDACT
    deep_scan: bool = False
    custom_keywords_s2: tuple[str, ...] = ()
    custom_keywords_s3: tuple[str, ...] = ()
    custom_patterns_s2: tuple[str, ...] = ()
    custom_patterns_s3: tuple[str, ...] = ()
    sensitive_tools_s2: tuple[str, ...] = ()
    sensitive_tools_s3: tuple[str, ...] = ()
    sensitive_paths_s3: tuple[str, ...] = (
        ".env",
        ".key",
        ".pem",
        ".p12",
        ".pfx",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    )

    @property
    def needs_pseudonym_store(self) -> bool:
        """Whether this policy requires a shared PseudonymStore.

        True when S2/S3 enable PSEUDONYMIZE (regex pseudonymization persists
        through the store) or deep scan is on (LLM-detected non-structured PII is
        always pseudonymized through the store, even when S2/S3 use REDACT).
        Single source of truth for every memory-write path that initializes the
        store.
        """
        return self.s2_action == PIIAction.PSEUDONYMIZE or self.s3_action == PIIAction.PSEUDONYMIZE or self.deep_scan


@dataclass(frozen=True, slots=True)
class PrivacyRoutingConfig:
    """Privacy-aware model routing: PrivacyRoutingModel picks the cloud or the local model by detected sensitivity.

    When ``local_model`` is None, routing is disabled and every request goes to the cloud model unchanged.
    ``s2_strategy="cloud_after_redact"`` sends S2 content to the cloud with the policy's S2 action already applied
    (only a warning by default); the value is persisted in user settings under this name.
    """

    local_model: str | None = None
    local_base_url: str | None = None
    local_api_key: str | None = None
    s2_strategy: Literal["cloud_after_redact", "local"] = "cloud_after_redact"
    s3_strategy: Literal["local", "block"] = "local"
    local_fallback: Literal["block", "force_redact_cloud"] = "block"


def _default_privacy_policy() -> PrivacyPolicy:
    return PrivacyPolicy()
