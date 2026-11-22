"""Tests for which files the dependency extractor reads on disk, and how it reads them.

[INPUT]
- backends.skills.scanning.dependency_extractor::extract_skill_dependencies, extract_dependencies_from_files (POS: the extractor under test)

[OUTPUT]
- Fails when a dependency file is skipped for its depth or folder (node_modules apart), when a symbolic link is followed,
  when a file over the size limit is read or skipped without a warning, when a part of the tree the walk could not list
  goes unlogged, or when the disk scan and the archive scan pick different files.

[POS]
The extractor feeds the advisory scanners, so a dependency it never sees is one they never check; this pins that it
reads what an installer reads and says so when it could not.
"""

from __future__ import annotations

import errno
import logging
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from myrm_agent_harness.backends.skills.scanning.dependency_extractor import (
    DeclaredDependency,
    extract_dependencies_from_files,
    extract_skill_dependencies,
)

_LOGGER = "myrm_agent_harness.backends.skills.scanning.dependency_extractor"
_LIMIT = 16 * 1024 * 1024
_NUMPY = "numpy==1.24.0\n"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "folder", [".git", ".hidden", "venv", ".venv", "__pycache__", "dist", "build", ".tox", "a/b/c/d/e"]
)
def test_a_dependency_file_is_read_in_every_folder_but_node_modules(tmp_path: Path, folder: str) -> None:
    _write(tmp_path, f"{folder}/requirements.txt", _NUMPY)
    _write(tmp_path, "node_modules/dep/requirements.txt", "ignored==1.0\n")

    deps = extract_skill_dependencies(tmp_path)

    assert [(d.name, d.file_path) for d in deps] == [("numpy", str(Path(folder) / "requirements.txt"))]


def test_a_symlinked_dependency_file_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside, "requirements.txt", "leaked==1.0\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    try:
        (skill / "requirements.txt").symlink_to(outside / "requirements.txt")
        (skill / "linked-folder").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available")

    assert extract_skill_dependencies(skill) == []


def test_a_file_over_the_size_limit_is_skipped_with_a_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write(tmp_path, "requirements.txt", "#" * (_LIMIT + 1))
    _write(tmp_path, "package.json", '{"dependencies": {"debug": "4.3.4"}}')

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        deps = extract_skill_dependencies(tmp_path)

    assert [d.name for d in deps] == ["debug"]
    assert f"longer than {_LIMIT} characters" in caplog.text


def test_a_file_of_exactly_the_size_limit_is_read(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _write(tmp_path, "requirements.txt", _NUMPY + "#" * (_LIMIT - len(_NUMPY)))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        deps = extract_skill_dependencies(tmp_path)

    assert [d.name for d in deps] == ["numpy"]
    assert caplog.text == ""


def test_a_dependency_file_the_process_cannot_read_is_skipped(tmp_path: Path) -> None:
    unreadable = tmp_path / "requirements.txt"
    unreadable.write_text(_NUMPY, encoding="utf-8")
    _write(tmp_path, "package.json", '{"dependencies": {"debug": "4.3.4"}}')
    unreadable.chmod(0)
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("the process reads files whatever their mode")

        assert [d.name for d in extract_skill_dependencies(tmp_path)] == ["debug"]
    finally:
        unreadable.chmod(0o600)


def test_a_folder_the_walk_cannot_list_is_logged_and_the_rest_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "locked").mkdir()
    _write(tmp_path, "open/requirements.txt", _NUMPY)
    real_scandir = os.scandir

    def scandir(path: Path) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if Path(path).name == "locked":
            raise PermissionError(errno.EACCES, "denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        deps = extract_skill_dependencies(tmp_path)

    assert [d.name for d in deps] == ["numpy"]
    assert "incomplete at 'locked': a folder cannot be listed" in caplog.text


_NAMED_FILES = {
    "package.json": '{"dependencies": {"axios": "1.0.0"}, "devDependencies": {"jest": "29.0.0"}}',
    "a/Package.JSON": '{"dependencies": {"express": "4.18.0"}}',
    "b/uv.lock": '[[package]]\nname = "idna"\nversion = "3.4"\n',
    "c/bun.lock": '{"lockfileVersion": 1, "packages": {"left-pad": ["left-pad@1.3.0", "", {}, "sha512-x"],},}',
    "d/package-lock.json": '{"lockfileVersion": 3, "packages": {"": {}, "node_modules/lodash": {"version": "4.17.21"}}}',
    "e/requirements.txt": "requests==2.28.1\n",
    "e/requirements-dev.txt": "pytest==8.0.0\n",
    "e/dev-requirements.txt": "black==23.1.0\n",
    "f/pyproject.toml": '[project]\nname = "x"\ndependencies = ["httpx>=0.24.0"]\n',
    "g/setup.py": "install_requires = ['setuptools==1.0']\n",
    "g/README.md": "numpy==1.0\n",
    "g/notes.txt": "numpy==1.0\n",
}


def _described(deps: list[DeclaredDependency]) -> list[tuple[str, str, str, bool, str]]:
    return sorted((d.name, d.version_spec, d.ecosystem, d.is_dev, Path(d.file_path).as_posix()) for d in deps)


def test_the_disk_scan_and_the_archive_scan_pick_the_same_files(tmp_path: Path) -> None:
    for relative, text in _NAMED_FILES.items():
        _write(tmp_path, relative, text)

    from_disk = extract_skill_dependencies(tmp_path)
    from_archive = extract_dependencies_from_files({name: text.encode() for name, text in _NAMED_FILES.items()})

    assert len(from_disk) == 10
    assert _described(from_disk) == _described(from_archive)
