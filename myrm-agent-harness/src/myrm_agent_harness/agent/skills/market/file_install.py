"""Skill file-tree installation: quarantine, security gates and transactional promotion.

[INPUT]
- agent.plugins.rules::DocumentTooLargeError (POS: Refusal of a plugin document over its decode ceiling)
- backends.skills.market_protocols::SkillInstallResult (POS: Typed install outcome)
- backends.skills.scanning::compute_scan_summary (POS: Security score of a scan)
- backends.skills.scanning.archive_security::classify_archive_security_issue, format_archive_security_user_message (POS: Canonical archive-security contract for typed/untyped ZIP guard errors.)
- backends.skills.scanning.package_audit::check_lifecycle_scripts (POS: In-memory lifecycle script gate)
- backends.skills.versioning::validate_version_guard (POS: Downgrade guard)
- utils.text_sanitizer::encodable_text (POS: repairs a text that only has to become UTF-8 encodable, replacing each lone surrogate with ``U+FFFD``)
- .helpers::read_origin, write_origin, scan_all_text_files (POS: Provenance file and in-memory scan)
- .transaction::SkillInstallTransaction, build_skill_receipt, write_receipt_file (POS: Atomic promotion and receipts)

[OUTPUT]
- install_skill_files: Install an in-memory skill file tree under an install root through the quarantine pipeline.
- is_safe_skill_dir_name: Whether a publisher-controlled name is a single relative path segment.
- resolve_install_error: Map an install exception to a user message and a stable error code.
- DOCUMENT_TOO_LARGE_MESSAGE, INVALID_PACKAGE_PATH_MESSAGE: The fixed refusal sentences a user sees; the details go to the log.

[POS]
The one install path for every source that already holds the files. The market service passes its install root, so the
pipeline itself reads no module-level location.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Iterable
from itertools import chain
from pathlib import Path

from myrm_agent_harness.agent.plugins.rules import DocumentTooLargeError
from myrm_agent_harness.backends.skills.local_skill_id import local_skill_id_from_path
from myrm_agent_harness.backends.skills.market_protocols import SkillInstallResult
from myrm_agent_harness.backends.skills.scanning import compute_scan_summary
from myrm_agent_harness.backends.skills.scanning.archive_security import (
    classify_archive_security_issue,
    format_archive_security_user_message,
)
from myrm_agent_harness.backends.skills.scanning.package_audit import check_lifecycle_scripts
from myrm_agent_harness.backends.skills.scanning.path_security import (
    CategoryBucketCollisionError,
    PathRedirectSecurityError,
)
from myrm_agent_harness.backends.skills.versioning import SkillDowngradeBlockedError, validate_version_guard
from myrm_agent_harness.utils.text_sanitizer import encodable_text

from .helpers import read_origin, scan_all_text_files, write_origin
from .transaction import SkillInstallTransaction, build_skill_receipt, write_receipt_file

logger = logging.getLogger(__name__)

# 用户可见拒绝原因的固定文案：不含包内路径、数字与解析器原文，这些细节只写日志
DOCUMENT_TOO_LARGE_MESSAGE = "安装被拦截：Agent Plugin 包内有文件超出大小限制。"
INVALID_PACKAGE_PATH_MESSAGE = "安装被拦截：包内有文件名或目录名过长，或含不支持的字符。"

# ext4 等 POSIX 文件系统的路径分量上限是 255 字节；按 UTF-8 字节数计，同一个包在每台主机上的结论一致
_MAX_NAME_BYTES = 255


def is_safe_skill_dir_name(name: str) -> bool:
    """Skill install directory names must be a single relative path segment.

    The name comes from publisher-controlled frontmatter, so anything that
    could escape the install root (separators, dot segments, absolute paths)
    must be rejected before it reaches filesystem operations.
    """
    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name:
        return False
    return not Path(name).is_absolute()


def _is_storable(part: str) -> bool:
    try:
        raw = part.encode("utf-8")
    except UnicodeEncodeError:  # 孤立代理字符没有 UTF-8 形式
        return False
    return len(raw) <= _MAX_NAME_BYTES and b"\0" not in raw


def _find_unstorable_name(skill_name: str, file_paths: Iterable[str]) -> str | None:
    """The first name of a package beyond the portable limits: the skill name, then each component of each path.

    Every name is chosen by the publisher; the first write to one that is too long or holds a NUL raises an
    OSError or ValueError, and a lone surrogate has no UTF-8 form, so each has to be refused before the quarantine
    is created. Each name is judged on its own: the total path length and Windows reserved names are not checked.
    """
    names = chain([skill_name], (part for path in file_paths for part in path.split("/")))
    return next((name for name in names if not _is_storable(name)), None)


def resolve_install_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, CategoryBucketCollisionError):
        return (
            str(error),
            "CATEGORY_BUCKET_COLLISION",
        )
    if isinstance(error, PathRedirectSecurityError):
        return (
            str(error),
            "PATH_REDIRECT_ATTACK",
        )
    if isinstance(error, DocumentTooLargeError):
        return DOCUMENT_TOO_LARGE_MESSAGE, "DOCUMENT_TOO_LARGE"

    violation = classify_archive_security_issue(error)
    if violation is None:
        return str(error), ""
    return format_archive_security_user_message(violation), violation.code.value


async def install_skill_files(
    install_root: Path,
    skill_id: str,
    name: str,
    files: dict[str, bytes],
    *,
    source: str = "",
    allow_downgrade: bool = False,
    progress_callback: Callable[[str, str, str], None] | None = None,
) -> SkillInstallResult:
    """Install an in-memory skill file tree through the quarantine pipeline.

    Single install entry for every source that already holds the files
    (market downloads, LobeHub templates, Agent Plugin skills, business-layer
    imports). ``skill_id`` is a provenance label written to the origin file;
    the canonical local skill id is derived from the install path.

    Rejection pipeline (each returns a failed SkillInstallResult, never
    raises): invalid directory name → package name beyond the portable
    limits → lifecycle script guard → security score gate → version downgrade guard →
    refusals found while promoting (unsafe install target: symlink/junction
    or category bucket; an Agent Plugin document over its decode ceiling).
    Unexpected errors propagate.
    """

    def _emit(stage: str, msg: str) -> None:
        if progress_callback:
            progress_callback(skill_id, stage, msg)

    # 0. 安装目录名必须是单个相对路径段：frontmatter 的 name 完全由
    # 技能发布方控制，未经校验会拼出目录穿越或让 mkdtemp 直接崩溃。
    # 本步两道门的拒绝结果都回显这个名字，它可能含孤立代理字符，须先换成 U+FFFD，否则 JSON 响应序列化会抛异常
    if not is_safe_skill_dir_name(name):
        reason = f"Invalid skill name for install directory: {name!r}"
        logger.warning("Skill install rejected: %s", reason)
        _emit("rejected", reason)
        return SkillInstallResult(
            success=False,
            skill_name=encodable_text(name),
            error=reason,
            error_code="INVALID_SKILL_NAME",
        )

    # 同属第 0 步：技能名与包内路径分量同样由发布方决定。过长或含 NUL 的名字在第一次写盘时就会抛 OSError/ValueError
    # （隔离目录的创建、写入循环、提升都会碰到），孤立代理字符没有 UTF-8 形式，按 UTF-8 判断才能让各主机的结论一致，
    # 所以都必须在创建任何目录之前拒绝；名字本身只写日志
    unstorable = _find_unstorable_name(name, files)
    if unstorable is not None:
        logger.warning(
            "Skill %.80r install rejected: a package name beyond %d UTF-8 bytes, or with a NUL or a lone surrogate "
            "(%d characters): %r",
            name,
            _MAX_NAME_BYTES,
            len(unstorable),
            unstorable[:80],
        )
        _emit("rejected", INVALID_PACKAGE_PATH_MESSAGE)
        return SkillInstallResult(
            success=False,
            skill_name=encodable_text(name),
            error=INVALID_PACKAGE_PATH_MESSAGE,
            error_code="INVALID_PACKAGE_PATH",
        )

    # 1. 前置生命周期脚本防御门禁 (Lifecycle Script Guard)
    lifecycle_findings = check_lifecycle_scripts(files)
    if any(f.severity in ("critical", "high") for f in lifecycle_findings):
        blocked_reasons = [f.description for f in lifecycle_findings if f.severity in ("critical", "high")]
        reason_str = "; ".join(blocked_reasons)
        logger.warning("Skill '%s' blocked by lifecycle script guard: %s", name, reason_str)
        _emit("rejected", f"Blocked by lifecycle script guard: {reason_str}")
        return SkillInstallResult(
            success=False,
            skill_name=name,
            error=f"Security policy blocked installation: malicious lifecycle scripts detected ({reason_str})",
            error_code="LIFECYCLE_SCRIPT_BLOCKED",
            scan_summary=reason_str,
        )

    quarantine_dir = Path(tempfile.mkdtemp(prefix="skill-quarantine-"))

    try:
        _emit("quarantine", "Writing to quarantine...")
        quarantine_resolved = quarantine_dir.resolve()
        for rel_path, content in files.items():
            file_path = (quarantine_dir / rel_path).resolve()
            if not file_path.is_relative_to(quarantine_resolved):
                logger.warning("Blocked path escape in skill '%s': %s", name, rel_path)
                continue
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)

        _emit("scanning", "Running security scan...")
        scan_result = scan_all_text_files(name, files)
        scan_summary_obj = compute_scan_summary(scan_result)

        if scan_summary_obj.score < 50:
            logger.warning(
                "Skill '%s' blocked by security gate (score %d/100 < 50): %s",
                name,
                scan_summary_obj.score,
                scan_result.summary,
            )
            _emit(
                "rejected",
                f"Security Gate Blocked: score {scan_summary_obj.score}/100 < 50 threshold. {scan_result.summary}",
            )
            return SkillInstallResult(
                success=False,
                skill_name=name,
                error=f"Security scan blocked installation (Score {scan_summary_obj.score}/100 below 50 threshold): {scan_result.summary}",
                error_code="SECURITY_SCORE_BELOW_THRESHOLD",
                scan_summary=scan_result.summary,
            )

        _emit("installing", "Promoting to install directory...")
        installed_skills: list[str] = []
        declared_mcp_servers: list[str] = []

        # 2. 版本防御门禁 (Skill Version Guardrail)
        target_dir = install_root / name
        incoming_version = ""
        current_version = ""
        if "SKILL.md" in files:
            try:
                from myrm_agent_harness.backends.skills._utils import (
                    parse_skill_frontmatter,
                )

                fm = parse_skill_frontmatter(files["SKILL.md"].decode("utf-8", errors="replace"), name)
                incoming_version = fm.version or ""
            except Exception as exc:
                logger.debug("Could not parse incoming version from SKILL.md: %s", exc)

        if target_dir.exists():
            origin_meta = read_origin(target_dir)
            current_version = str(origin_meta.get("version", "")) if origin_meta else ""
            if not current_version:
                cur_skill_md = target_dir / "SKILL.md"
                if cur_skill_md.exists():
                    try:
                        from myrm_agent_harness.backends.skills._utils import (
                            parse_skill_frontmatter,
                        )

                        cur_fm = parse_skill_frontmatter(cur_skill_md.read_text(encoding="utf-8"), name)
                        current_version = cur_fm.version or ""
                    except Exception as exc:
                        logger.debug(
                            "Could not parse current version from local SKILL.md: %s",
                            exc,
                        )

        try:
            guard_res = validate_version_guard(
                current_version,
                incoming_version,
                allow_downgrade=allow_downgrade,
            )
            if guard_res.reason:
                logger.info("Skill '%s' version guard notice: %s", name, guard_res.reason)
        except SkillDowngradeBlockedError as exc:
            logger.warning("Skill '%s' installation blocked by version guard: %s", name, exc)
            _emit("rejected", str(exc))
            return SkillInstallResult(
                success=False,
                skill_name=name,
                error=str(exc),
                error_code="DOWNGRADE_BLOCKED",
            )

        with SkillInstallTransaction() as tx:
            if "plugin.json" in files:
                try:
                    from myrm_agent_harness.agent.plugins.parser import (
                        AgentPluginParser,
                    )

                    parser = AgentPluginParser()
                    parsed = parser.parse_files(files)
                    declared_mcp_servers = [s.name for s in parsed.servers]
                    for p_skill in parsed.skills:
                        s_target_dir = install_root / p_skill.name
                        s_quarantine = Path(tempfile.mkdtemp(prefix="skill-q-"))
                        try:
                            for rel_p, data in p_skill.files.items():
                                f_path = s_quarantine / rel_p
                                f_path.parent.mkdir(parents=True, exist_ok=True)
                                f_path.write_bytes(data)
                            tx.stage_replace(s_quarantine, s_target_dir)
                            write_origin(
                                s_target_dir,
                                source=source,
                                skill_id=skill_id,
                                version=incoming_version,
                                parent_plugin=name,
                            )
                            p_scan = scan_all_text_files(p_skill.name, p_skill.files)
                            p_summary = compute_scan_summary(p_scan)
                            p_receipt = build_skill_receipt(
                                skill_id=local_skill_id_from_path(s_target_dir),
                                skill_name=p_skill.name,
                                source=source,
                                installed_path=str(s_target_dir),
                                files=p_skill.files,
                                scan_score=p_summary.score,
                                security_verified=p_scan.is_clean,
                            )
                            write_receipt_file(s_target_dir, p_receipt)
                            installed_skills.append(p_skill.name)
                        finally:
                            if s_quarantine.exists():
                                shutil.rmtree(s_quarantine, ignore_errors=True)
                except Exception as exc:
                    logger.warning(
                        "Failed to unpack Agent Plugin sub-skills for %s: %s",
                        name,
                        exc,
                    )
                    raise

            tx.stage_replace(quarantine_dir, target_dir)

            write_origin(
                target_dir,
                source=source,
                skill_id=skill_id,
                version=incoming_version,
                declared_mcp_servers=declared_mcp_servers or None,
            )

            canonical_id = local_skill_id_from_path(target_dir)
            receipt = build_skill_receipt(
                skill_id=canonical_id,
                skill_name=name,
                source=source,
                installed_path=str(target_dir),
                files=files,
                installed_skills=installed_skills or [name],
                declared_mcp_servers=declared_mcp_servers,
                scan_score=scan_summary_obj.score,
                security_verified=scan_result.is_clean,
            )
            write_receipt_file(target_dir, receipt)
            tx.commit()

        scan_summary = scan_result.summary if not scan_result.is_clean else ""
        if scan_summary:
            logger.warning(
                "Installed skill with findings: %s -> %s (%s)",
                name,
                target_dir,
                scan_summary,
            )
        else:
            logger.info("Installed skill: %s -> %s", name, target_dir)

        _emit("completed", f"Installed to {target_dir}")
        return SkillInstallResult(
            success=True,
            skill_name=name,
            skill_id=canonical_id,
            installed_path=str(target_dir),
            scan_summary=scan_summary,
            installed_skills=installed_skills or [name],
            declared_mcp_servers=declared_mcp_servers,
            receipt=receipt,
        )
    except (CategoryBucketCollisionError, PathRedirectSecurityError, DocumentTooLargeError) as exc:
        # 安装目标是符号链接/分类桶等不安全路径，或 Agent Plugin 包内有文档超过解码上限：
        # 都是可预期的拒绝，转成结构化失败结果，避免调用方收到不透明的 500
        resolved_error, error_code = resolve_install_error(exc)
        logger.warning("Skill '%s' install rejected: %s", name, exc)
        _emit("rejected", resolved_error)
        return SkillInstallResult(
            success=False,
            skill_name=name,
            error=resolved_error,
            error_code=error_code,
        )
    finally:
        if quarantine_dir.exists():
            shutil.rmtree(quarantine_dir, ignore_errors=True)
