"""Skill discovery API request/response schemas.

[INPUT]
pydantic::BaseModel (POS: Schema validation)
app.api.skills.schemas::EncodableStr, ScanFindingResponse (POS: response text type that repairs lone surrogates; scan finding model)

[OUTPUT]
Request/response models for skill discovery endpoints. A response field that carries registry, package or
scanner text is an EncodableStr, so a lone surrogate in it becomes U+FFFD instead of making the JSON body unencodable.

[POS]
Request and response schemas of the skill discovery routes: search, install, update, uninstall, preview,
URL analysis, custom sources, static-index status and skill-pool sync.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.api.skills.schemas import EncodableStr, ScanFindingResponse


class SkillSearchResultResponse(BaseModel):
    id: EncodableStr
    name: EncodableStr
    description: EncodableStr
    source: EncodableStr
    author: EncodableStr
    install_url: EncodableStr
    install_method: EncodableStr
    version: EncodableStr = ""
    stars: int = 0
    downloads: int = 0
    tags: list[EncodableStr] = []
    readme_url: EncodableStr | None = None
    subdirectory: EncodableStr | None = None
    installed_version: EncodableStr = ""
    upgrade_available: bool = False
    installed_skill_id: EncodableStr = ""
    package_type: EncodableStr = "skill"
    keywords: list[EncodableStr] = []
    declared_mcp_servers: list[EncodableStr] = []


class SkillSearchResponse(BaseModel):
    results: list[SkillSearchResultResponse]
    total: int
    query: str


class SkillInstallRequest(BaseModel):
    skill_id: str
    source: str
    agent_id: str | None = None
    mount_to_agent: bool = True
    allow_downgrade: bool = False


class SkillReceiptResponse(BaseModel):
    receipt_id: EncodableStr
    skill_id: EncodableStr
    skill_name: EncodableStr
    source: EncodableStr
    installed_at: EncodableStr
    version: EncodableStr = ""
    installed_path: EncodableStr = ""
    installed_skills: list[EncodableStr] = []
    declared_mcp_servers: list[EncodableStr] = []
    scan_score: int = 100
    security_verified: bool = True
    manifest_hash: EncodableStr = ""


class SkillInstallResponse(BaseModel):
    success: bool
    skill_name: EncodableStr = ""
    skill_id: EncodableStr = ""
    installed_path: EncodableStr = ""
    error: EncodableStr = ""
    error_code: EncodableStr = ""
    mounted: bool = False
    mount_agent_id: EncodableStr = ""
    mount_skill_id: EncodableStr = ""
    mount_already_present: bool = False
    mount_error: EncodableStr = ""
    allowlist_appended: bool = False
    allowlist_append_error: EncodableStr = ""
    installed_skills: list[EncodableStr] = []
    declared_mcp_servers: list[EncodableStr] = []
    receipt: SkillReceiptResponse | None = None
    pre_replace_batch_id: EncodableStr | None = None
    """Rollback batch id when a market replace captured a pre-replace snapshot."""
    snapshot_truncated: bool = False
    """True when the pre-replace snapshot hit size limits (undo may be partial)."""


class SkillUpdateInfoResponse(BaseModel):
    skill_name: EncodableStr
    current_version: EncodableStr
    remote_version: EncodableStr
    source: EncodableStr
    skill_id: EncodableStr
    has_update: bool


class UpdateCheckResponse(BaseModel):
    has_updates: bool
    updates: list[SkillUpdateInfoResponse]


class SkillUpdateRequest(BaseModel):
    skill_name: str
    skill_id: str
    source: str
    allow_downgrade: bool = False


class SkillUninstallRequest(BaseModel):
    skill_id: str
    force: bool = False


class SkillPreviewRequest(BaseModel):
    skill_id: str
    source: str


class PrerequisiteDiagnosticResponse(BaseModel):
    is_supported_os: bool = True
    all_satisfied: bool = True
    current_os: EncodableStr = ""
    missing_binaries: list[EncodableStr] = []
    missing_packages: list[EncodableStr] = []
    remediation_commands: list[EncodableStr] = []
    summary_message: EncodableStr = ""


class SkillPreviewResponse(BaseModel):
    skill_id: EncodableStr
    name: EncodableStr
    description: EncodableStr
    version: EncodableStr
    files: list[EncodableStr]
    scan_findings: list[ScanFindingResponse] = []
    is_clean: bool = True
    package_type: EncodableStr = "skill"
    installed_skills: list[EncodableStr] = []
    declared_mcp_servers: list[EncodableStr] = []
    prerequisites: PrerequisiteDiagnosticResponse | None = None


class SkillInstallFromUrlRequest(BaseModel):
    url: str
    agent_id: str | None = None
    mount_to_agent: bool = True
    allow_downgrade: bool = False


class SkillUrlInfo(BaseModel):
    url: EncodableStr
    name: EncodableStr
    description: EncodableStr = ""
    is_installed: bool


class SkillAnalyzeUrlResponse(BaseModel):
    urls: list[SkillUrlInfo]


class CustomSourceRequest(BaseModel):
    url: str
    source_type: str = "well-known"
    label: str = ""


class CustomSourceResponse(BaseModel):
    url: str
    source_type: str
    label: str
    healthy: bool


class CustomSourceListResponse(BaseModel):
    sources: list[CustomSourceResponse]


class CustomSourceProbeResponse(BaseModel):
    reachable: bool
    skill_count: int
    url: str


class SkillPoolSyncRequest(BaseModel):
    skill_id: str
    target_agent_ids: list[str]


class SkillPoolSyncResponse(BaseModel):
    success: bool
    skill_id: str
    synced_agents: list[str]
    failed_agents: list[str] = []


class StaticIndexStatusResponse(BaseModel):
    is_loaded: bool
    total_indexed_skills: int
    source_name: str
    source_url: str
    last_synced_at: float
