"""Security type definitions — shared data structures for the security subsystem.

Zero-dependency package providing all core types used across the security
subsystem. No internal imports — eliminates circular dependency by serving
as the single foundation layer.

[INPUT]
- (none — pure types, zero dependencies)

[OUTPUT]
- Capability / CapabilitySet / DEFAULT_CAPABILITIES: Layer 1 capability grants
- PermissionAction: allow / ask / deny
- CapabilitySurface / CAPABILITY_SURFACE_PERMISSIONS: the coarse capability surfaces and the permission types each governs
- PermissionRule / PermissionRuleset / DEFAULT_RULESET: Layer 3 rules
- PathPolicy / AccessRoot: Layer 2.5 path access control
- SensitivityLevel: S1/S2/S3 privacy classification
- PIIAction: warn / redact / block
- PrivacyPolicy: PII protection configuration
- PrivacyRoutingConfig: privacy-aware model routing configuration
- ReviewDecision / ReviewResult / RecentToolCall / SecurityReviewerProtocol: Layer 5.5 Transcript Classifier
- SecurityConfig / InjectionPolicy: complete per-session security configuration
- EphemeralUserCredential / user_credentials_ctx / with_user_credentials / propagate_user_credentials: context-bound user credentials
- ToolClarificationError: a tool needs the person to clarify

[POS]
Foundation layer of the security type hierarchy. All other security modules
import from here; this module imports from none of them.
"""

from myrm_agent_harness.core.security.types.credentials import (
    EphemeralUserCredential,
    ToolClarificationError,
    propagate_user_credentials,
    user_credentials_ctx,
    with_user_credentials,
)
from myrm_agent_harness.core.security.types.paths import (
    AccessRoot,
    PathPolicy,
    access_roots_from_paths,
)
from myrm_agent_harness.core.security.types.paths import (
    _default_dangerous_paths as _default_dangerous_paths,
)
from myrm_agent_harness.core.security.types.paths import (
    _default_path_policy as _default_path_policy,
)
from myrm_agent_harness.core.security.types.privacy import (
    PIIAction,
    PrivacyPolicy,
    PrivacyRoutingConfig,
    SensitivityLevel,
)
from myrm_agent_harness.core.security.types.privacy import (
    _default_privacy_policy as _default_privacy_policy,
)
from myrm_agent_harness.core.security.types.review import (
    RecentToolCall,
    ReviewDecision,
    ReviewResult,
    SecurityReviewerProtocol,
)
from myrm_agent_harness.core.security.types.rules import (
    CAPABILITY_SURFACE_PERMISSIONS,
    DEFAULT_CAPABILITIES,
    DEFAULT_RULESET,
    Capability,
    CapabilitySet,
    CapabilitySurface,
    PermissionAction,
    PermissionRule,
    PermissionRuleset,
)
from myrm_agent_harness.core.security.types.security_config import InjectionPolicy, SecurityConfig

__all__ = [
    "CAPABILITY_SURFACE_PERMISSIONS",
    "DEFAULT_CAPABILITIES",
    "DEFAULT_RULESET",
    "AccessRoot",
    "Capability",
    "CapabilitySet",
    "CapabilitySurface",
    "EphemeralUserCredential",
    "InjectionPolicy",
    "PIIAction",
    "PathPolicy",
    "PermissionAction",
    "PermissionRule",
    "PermissionRuleset",
    "PrivacyPolicy",
    "PrivacyRoutingConfig",
    "RecentToolCall",
    "ReviewDecision",
    "ReviewResult",
    "SecurityConfig",
    "SecurityReviewerProtocol",
    "SensitivityLevel",
    "ToolClarificationError",
    "access_roots_from_paths",
    "propagate_user_credentials",
    "user_credentials_ctx",
    "with_user_credentials",
]
