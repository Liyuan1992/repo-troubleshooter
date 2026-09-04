"""Local context collection is convenient without becoming authority."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest
from repo_troubleshooter.workspace import (
    WorkspaceCatalog,
    inspect_workspace,
    resolve_context,
)

REPO = "deepseek-ai/deepseek-harness"


def _catalog(*, ambiguous: bool = False) -> WorkspaceCatalog:
    package_repositories = {"@deepseek-ai/dsh": frozenset({REPO})}
    repositories = {REPO.lower(): REPO}
    if ambiguous:
        other = "example/other-runtime"
        repositories[other] = other
        package_repositories["other-runtime"] = frozenset({other})
    return WorkspaceCatalog(
        repositories=repositories,
        package_repositories=package_repositories,
        core_packages={REPO.lower(): frozenset({"@deepseek-ai/dsh"})},
        runtimes={REPO.lower(): ("node",)},
    )


def _write_package_json(root: Path, dependencies: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": "consumer", "version": "1.0.0", "dependencies": dependencies}),
        encoding="utf-8",
    )


def test_detects_repo_version_runtime_os_and_related_package(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {"@deepseek-ai/dsh": "0.1.2-alpha.1"})
    snapshot = inspect_workspace(
        tmp_path, version_probe=lambda runtime, _root: f"{runtime} 24.11.1"
    )

    context = resolve_context(snapshot, _catalog())

    assert context.repo == REPO
    assert context.core_version == "0.1.2-alpha.1"
    assert context.runtime == "node 24.11.1"
    assert context.os_name in {"windows", "linux", "macos"}
    assert context.detected_packages == ("@deepseek-ai/dsh",)
    assert context.sources == {
        "repo": "workspace package metadata",
        "core_version": "package.json dependency",
        "runtime": "node --version",
        "os": "local operating system",
    }
    assert context.warnings == (
        "the product version comes from an exact dependency declaration; "
        "no installed package manifest was found",
    )


def test_installed_manifest_wins_over_a_declared_range(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {"@deepseek-ai/dsh": "^0.1.0"})
    installed = tmp_path / "node_modules" / "@deepseek-ai" / "dsh"
    installed.mkdir(parents=True)
    (installed / "package.json").write_text(
        json.dumps(
            {
                "name": "@deepseek-ai/dsh",
                "version": "0.1.2-alpha.1",
                "repository": "https://github.com/deepseek-ai/deepseek-harness.git",
            }
        ),
        encoding="utf-8",
    )

    context = resolve_context(
        inspect_workspace(tmp_path, version_probe=lambda runtime, _root: f"{runtime} 24.11.1"),
        _catalog(),
    )

    assert context.core_version == "0.1.2-alpha.1"
    assert context.sources["core_version"] == "installed package.json"


def test_explicit_values_override_detection(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {"@deepseek-ai/dsh": "0.1.2-alpha.1"})
    snapshot = inspect_workspace(
        tmp_path, version_probe=lambda runtime, _root: f"{runtime} 24.11.1"
    )

    context = resolve_context(
        snapshot,
        _catalog(),
        repo=REPO,
        core_version="0.1.2-alpha.3",
        runtime="node 22.19.0",
        os_name="linux",
    )

    assert context.core_version == "0.1.2-alpha.3"
    assert context.runtime == "node 22.19.0"
    assert context.os_name == "linux"
    assert context.sources["core_version"] == "--version"
    assert context.sources["runtime"] == "--runtime"
    assert context.sources["os"] == "--os"


def test_unrelated_workspace_does_not_supply_environment_for_explicit_repo(
    tmp_path: Path,
) -> None:
    _write_package_json(tmp_path, {"unrelated": "1.0.0"})
    snapshot = inspect_workspace(tmp_path, version_probe=lambda runtime, _root: f"{runtime} 99.0.0")

    context = resolve_context(snapshot, _catalog(), repo=REPO)

    assert context.repo == REPO
    assert context.core_version is None
    assert context.runtime is None
    assert context.os_name is None
    assert context.detected_packages == ()


def test_ambiguous_package_repositories_require_an_override(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {"@deepseek-ai/dsh": "0.1.2-alpha.1", "other-runtime": "2.0.0"})

    context = resolve_context(
        inspect_workspace(tmp_path, version_probe=lambda runtime, _root: f"{runtime} 24.11.1"),
        _catalog(ambiguous=True),
    )

    assert context.repo is None
    assert context.warnings == (
        "multiple synced repositories match packages in this workspace; use --repo",
    )


def test_git_origin_is_used_when_no_known_package_matches(tmp_path: Path) -> None:
    _write_package_json(tmp_path, {})
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607
    subprocess.run(  # noqa: S603, S607
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "git@github.com:deepseek-ai/deepseek-harness.git",
        ],
        check=True,
    )

    context = resolve_context(
        inspect_workspace(tmp_path, version_probe=lambda _runtime, _root: None), _catalog()
    )

    assert context.repo == REPO
    assert context.sources["repo"] == "git remote origin"


def test_detected_packages_do_not_become_structured_authority() -> None:
    request = DiagnosisRequest(repo=REPO, detected_packages=["@deepseek-ai/dsh"])

    assert request.packages == []
    assert request.environment_json()["detected_packages"] == ["@deepseek-ai/dsh"]
