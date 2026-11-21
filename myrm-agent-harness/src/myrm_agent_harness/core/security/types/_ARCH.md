# core/security/types/

## Overview
The foundation of the security type hierarchy: every dataclass, enum and protocol the engine, the config parser
and the approval pipeline share. One module per concern; none imports anything of the security subsystem, and
`__init__.py` is the single import surface (`from myrm_agent_harness.core.security.types import X`), so a caller
never learns which file defines a name. `agent/security/types.py` re-exports this surface under the stable
`agent.security.types` path.

## File Index

| File | Role | Description | I/O/P |
|---|---|---|---|
| `__init__.py` | Package | Aggregation facade. Re-exports the public names, plus the three private default factories the `agent.security.types` shim forwards, and declares `__all__`. | ✅ |
| `rules.py` | Core | The permission model: `Capability` / `CapabilitySet` / `DEFAULT_CAPABILITIES` (negative `!perm` exclusions win over grants), `PermissionAction`, `CapabilitySurface` / `CAPABILITY_SURFACE_PERMISSIONS`, `PermissionRule` / `PermissionRuleset` and `DEFAULT_RULESET`. | ✅ |
| `privacy.py` | Core | `SensitivityLevel` (S1/S2/S3), `PIIAction`, `PrivacyPolicy` and `PrivacyRoutingConfig`. | ✅ |
| `paths.py` | Core | `AccessRoot`, `access_roots_from_paths` and `PathPolicy` (forbidden paths always win over roots and the workspace). | ✅ |
| `review.py` | Core | The Layer 5.5 reviewer contract: `ReviewDecision`, `ReviewResult`, `RecentToolCall` and the `SecurityReviewerProtocol` an LLM-based Transcript Classifier implements. | ✅ |
| `security_config.py` | Core | `SecurityConfig`: capabilities, the main `ruleset`, the `restrictions` layers (each judged over an allow-all base, the strictest verdict of the ruleset and every layer standing), the timeouts, path policy, network lists, YOLO and auto-review settings, plus the `readonly` / `workspace` / `full_access` / `remote_exposed` factory profiles; and `InjectionPolicy`. | ✅ |
| `credentials.py` | Core | `EphemeralUserCredential`, the `user_credentials_ctx` context variable with `with_user_credentials` / `propagate_user_credentials`, and `ToolClarificationError`. | ✅ |

## Dependencies
- Standard library only. `paths.py` reads `core.security.path.DANGEROUS_PATHS` lazily inside its default factory, so loading the package imports nothing of the subsystem.
- `security_config.py` imports `rules`, `paths` and `privacy`; the other modules import nothing of the package.

## Consumers
- The engine, the config parser, the channel presets and every other `core.security` / `agent.security` module (through the facade).
- `agent/security/types.py` — re-export under the `agent.security.types` path.
