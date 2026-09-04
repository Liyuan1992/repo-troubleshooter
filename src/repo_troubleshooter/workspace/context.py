"""Collect safe, local facts that users should not have to type.

Only known metadata files are read. Project scripts are never executed, raw
logs are never collected, and paths never leave this module. A package found in
a manifest is reported as *present*, not as the thing that failed; presence may
improve the echo shown to a user, but it never authorises an action.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
import tomllib
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from packaging.requirements import InvalidRequirement, Requirement
from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.profiles.loader import list_profiles
from repo_troubleshooter.store.models import PackageManifest, Repository

Ecosystem = Literal["node", "python"]
VersionProbe = Callable[[str, Path], str | None]

_EXACT_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class PackageObservation:
    """One package visible in a safe workspace manifest."""

    name: str
    version: str | None
    ecosystem: Ecosystem
    source: str
    repository: str | None = None


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Raw local observations. ``root`` is deliberately never serialized."""

    root: Path
    git_repository: str | None = None
    packages: tuple[PackageObservation, ...] = ()
    runtimes: dict[str, str] = field(default_factory=dict)
    runtime_sources: dict[str, str] = field(default_factory=dict)
    os_name: str | None = None


@dataclass(frozen=True)
class WorkspaceCatalog:
    """The synced repositories and package names the evidence store knows."""

    repositories: dict[str, str] = field(default_factory=dict)
    package_repositories: dict[str, frozenset[str]] = field(default_factory=dict)
    core_packages: dict[str, frozenset[str]] = field(default_factory=dict)
    runtimes: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalDiagnosisContext:
    """Values ready to merge into a ``DiagnosisRequest``."""

    repo: str | None
    core_version: str | None
    runtime: str | None
    os_name: str | None
    detected_packages: tuple[str, ...] = ()
    sources: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _repository_slug(value: object) -> str | None:
    """Return ``owner/name`` without ever retaining credentials from a URL."""
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    raw = value.strip().removeprefix("git+")
    if not raw:
        return None

    path = ""
    if re.match(r"^[^/@\s]+@[^:/\s]+:.+$", raw):
        path = raw.split(":", 1)[1]
    else:
        parsed = urlparse(raw if "://" in raw else f"https://placeholder/{raw}")
        path = parsed.path
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1].removesuffix(".git")
    if not owner or not name:
        return None
    return f"{owner}/{name}"


def _exact_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped.removeprefix("v") if _EXACT_VERSION_RE.fullmatch(stripped) else None


def _workspace_root(start: Path) -> Path:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    git_fallback: Path | None = None
    for candidate in (current, *current.parents):
        if (candidate / "package.json").is_file() or (candidate / "pyproject.toml").is_file():
            return candidate
        if git_fallback is None and (candidate / ".git").exists():
            git_fallback = candidate
    return git_fallback or current


def _git_repository(root: Path) -> str | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [git, "-C", str(root), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _repository_slug(result.stdout) if result.returncode == 0 else None


def _default_version_probe(runtime: str, root: Path) -> str | None:
    if runtime == "python":
        # The troubleshooter may be installed globally while the target project
        # has another interpreter. Only claim this process' Python when its
        # environment actually lives inside the target workspace.
        try:
            Path(sys.prefix).resolve().relative_to(root)
        except ValueError:
            return None
        return f"python {platform.python_version()}"

    executable = shutil.which(runtime)
    if not executable:
        return None
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [executable, "--version"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode != 0 or not output:
        return None
    version = output[0].strip().removeprefix("v")
    return f"{runtime} {version}"


def _node_packages(root: Path) -> list[PackageObservation]:
    manifest = _read_json(root / "package.json")
    if manifest is None:
        return []

    found: dict[str, PackageObservation] = {}
    own_name = manifest.get("name")
    if isinstance(own_name, str) and own_name.strip():
        found[own_name.lower()] = PackageObservation(
            name=own_name,
            version=_exact_version(manifest.get("version")),
            ecosystem="node",
            source="workspace package.json",
            repository=_repository_slug(manifest.get("repository")),
        )

    declared: dict[str, object] = {}
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        values = manifest.get(section)
        if isinstance(values, dict):
            declared.update({str(name): spec for name, spec in values.items()})

    for name, spec in declared.items():
        installed_path = root / "node_modules" / Path(*name.split("/")) / "package.json"
        installed = _read_json(installed_path)
        if installed is not None:
            installed_name = installed.get("name")
            package_name = installed_name if isinstance(installed_name, str) else name
            found[name.lower()] = PackageObservation(
                name=package_name,
                version=_exact_version(installed.get("version")),
                ecosystem="node",
                source="installed package.json",
                repository=_repository_slug(installed.get("repository")),
            )
            continue
        found[name.lower()] = PackageObservation(
            name=name,
            version=_exact_version(spec),
            ecosystem="node",
            source="package.json dependency",
        )
    return list(found.values())


def _python_packages(root: Path) -> list[PackageObservation]:
    path = root / "pyproject.toml"
    try:
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            return []
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project")
    if not isinstance(project, dict):
        return []

    found: list[PackageObservation] = []
    own_name = project.get("name")
    urls = project.get("urls")
    repository: object = None
    if isinstance(urls, dict):
        repository = urls.get("Repository") or urls.get("repository") or urls.get("Homepage")
    if isinstance(own_name, str) and own_name.strip():
        found.append(
            PackageObservation(
                name=own_name,
                version=_exact_version(project.get("version")),
                ecosystem="python",
                source="workspace pyproject.toml",
                repository=_repository_slug(repository),
            )
        )

    dependencies = project.get("dependencies")
    if isinstance(dependencies, list):
        for raw in dependencies:
            if not isinstance(raw, str):
                continue
            try:
                requirement = Requirement(raw)
            except InvalidRequirement:
                continue
            exact = [spec.version for spec in requirement.specifier if spec.operator == "=="]
            found.append(
                PackageObservation(
                    name=requirement.name,
                    version=exact[0] if len(exact) == 1 else None,
                    ecosystem="python",
                    source="pyproject.toml dependency",
                )
            )
    return found


def inspect_workspace(
    start: Path, *, version_probe: VersionProbe | None = None
) -> WorkspaceSnapshot:
    """Read manifests and harmless version commands from the nearest workspace."""
    root = _workspace_root(start)
    packages = [*_node_packages(root), *_python_packages(root)]
    ecosystems = {package.ecosystem for package in packages}
    probe = version_probe or _default_version_probe
    runtimes: dict[str, str] = {}
    sources: dict[str, str] = {}
    for runtime in sorted(ecosystems):
        value = probe(runtime, root)
        if value:
            runtimes[runtime] = value
            sources[runtime] = f"{runtime} --version"

    os_name = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(
        platform.system(), platform.system().lower() or None
    )
    return WorkspaceSnapshot(
        root=root,
        git_repository=_git_repository(root),
        packages=tuple(packages),
        runtimes=runtimes,
        runtime_sources=sources,
        os_name=os_name,
    )


def build_catalog(session: Session) -> WorkspaceCatalog:
    """Build a small read-only resolver from synced manifests and profiles."""
    repo_rows = list(session.execute(select(Repository.id, Repository.full_name)))
    repositories = {full_name.lower(): full_name for _repo_id, full_name in repo_rows}
    id_to_name = {repo_id: full_name for repo_id, full_name in repo_rows}
    package_repositories: dict[str, set[str]] = defaultdict(set)
    for repo_id, package_name in session.execute(
        select(PackageManifest.repo_id, PackageManifest.name)
    ):
        full_name = id_to_name.get(repo_id)
        if full_name:
            package_repositories[package_name.lower()].add(full_name)

    core_packages: dict[str, frozenset[str]] = {}
    runtimes: dict[str, tuple[str, ...]] = {}
    for profile in list_profiles():
        canonical = repositories.get(profile.repo.lower(), profile.repo)
        repositories.setdefault(profile.repo.lower(), canonical)
        core = frozenset(name.lower() for name in profile.local_context.core_packages)
        core_packages[canonical.lower()] = core
        runtimes[canonical.lower()] = tuple(profile.local_context.runtimes)
        for package_name in core:
            package_repositories[package_name].add(canonical)

    return WorkspaceCatalog(
        repositories=repositories,
        package_repositories={
            name: frozenset(values) for name, values in package_repositories.items()
        },
        core_packages=core_packages,
        runtimes=runtimes,
    )


def _related_observations(
    snapshot: WorkspaceSnapshot, repo: str, catalog: WorkspaceCatalog
) -> list[PackageObservation]:
    related: list[PackageObservation] = []
    repo_lower = repo.lower()
    for package in snapshot.packages:
        mapped = catalog.package_repositories.get(package.name.lower(), frozenset())
        if any(name.lower() == repo_lower for name in mapped) or (
            package.repository and package.repository.lower() == repo_lower
        ):
            related.append(package)
    return related


def _choose_version(
    observations: Iterable[PackageObservation], core_packages: frozenset[str]
) -> tuple[str | None, str | None, str | None]:
    rows = [row for row in observations if row.version]
    core_rows = [row for row in rows if row.name.lower() in core_packages]
    if core_rows:
        rows = core_rows
    if not rows:
        return None, None, None

    priority = {
        "installed package.json": 0,
        "workspace package.json": 1,
        "workspace pyproject.toml": 1,
        "package.json dependency": 2,
        "pyproject.toml dependency": 2,
    }
    best = min(priority.get(row.source, 9) for row in rows)
    preferred = [row for row in rows if priority.get(row.source, 9) == best]
    versions = {row.version for row in preferred if row.version}
    if len(versions) != 1:
        return None, None, "multiple product versions were found in the workspace"
    selected = next(row for row in preferred if row.version in versions)
    return selected.version, selected.source, None


def resolve_context(
    snapshot: WorkspaceSnapshot,
    catalog: WorkspaceCatalog,
    *,
    repo: str | None = None,
    core_version: str | None = None,
    runtime: str | None = None,
    os_name: str | None = None,
) -> LocalDiagnosisContext:
    """Merge explicit overrides with auto-detected workspace facts.

    Explicit fields always win. Detected packages remain a separate field and
    are never copied into the structured ``packages`` authorization input.
    """
    sources: dict[str, str] = {}
    warnings: list[str] = []
    chosen_repo = repo.strip() if repo and repo.strip() else None
    if chosen_repo:
        sources["repo"] = "--repo"
    else:
        package_candidates: set[str] = set()
        metadata_candidates: set[str] = set()
        for package in snapshot.packages:
            package_candidates.update(
                catalog.package_repositories.get(package.name.lower(), frozenset())
            )
            if package.repository:
                canonical = catalog.repositories.get(package.repository.lower())
                if canonical:
                    metadata_candidates.add(canonical)

        synced_git = (
            catalog.repositories.get(snapshot.git_repository.lower())
            if snapshot.git_repository
            else None
        )
        candidates = package_candidates | metadata_candidates
        if synced_git:
            candidates.add(synced_git)
        if len(candidates) == 1:
            chosen_repo = next(iter(candidates))
            sources["repo"] = (
                "git remote origin"
                if chosen_repo == synced_git and not (package_candidates | metadata_candidates)
                else "workspace package metadata"
            )
        elif len(candidates) > 1:
            warnings.append(
                "multiple synced repositories match packages in this workspace; use --repo"
            )
        elif synced_git:
            chosen_repo = synced_git
            sources["repo"] = "git remote origin"
        elif snapshot.git_repository:
            chosen_repo = snapshot.git_repository
            sources["repo"] = "git remote origin"

    related = _related_observations(snapshot, chosen_repo, catalog) if chosen_repo else []
    workspace_matches_repo = bool(
        chosen_repo
        and (
            related
            or (snapshot.git_repository and snapshot.git_repository.lower() == chosen_repo.lower())
        )
    )

    detected_version = None
    version_source = None
    if chosen_repo and workspace_matches_repo:
        detected_version, version_source, version_warning = _choose_version(
            related, catalog.core_packages.get(chosen_repo.lower(), frozenset())
        )
        if version_warning:
            warnings.append(version_warning)
        if version_source in {"package.json dependency", "pyproject.toml dependency"}:
            warnings.append(
                "the product version comes from an exact dependency declaration; "
                "no installed package manifest was found"
            )
    chosen_version = core_version or detected_version
    if core_version:
        sources["core_version"] = "--version"
    elif detected_version and version_source:
        sources["core_version"] = version_source

    chosen_runtime = runtime
    if runtime:
        sources["runtime"] = "--runtime"
    elif chosen_repo and workspace_matches_repo:
        preferred_runtimes = catalog.runtimes.get(chosen_repo.lower(), ())
        if not preferred_runtimes:
            preferred_runtimes = tuple(sorted({package.ecosystem for package in related}))
        available = [name for name in preferred_runtimes if name in snapshot.runtimes]
        if len(available) == 1:
            runtime_name = available[0]
            chosen_runtime = snapshot.runtimes[runtime_name]
            sources["runtime"] = snapshot.runtime_sources.get(
                runtime_name, f"{runtime_name} --version"
            )
        elif len(available) > 1:
            warnings.append("multiple runtime versions were found; use --runtime")

    chosen_os = os_name
    if os_name:
        sources["os"] = "--os"
    elif workspace_matches_repo and snapshot.os_name:
        chosen_os = snapshot.os_name
        sources["os"] = "local operating system"

    return LocalDiagnosisContext(
        repo=chosen_repo,
        core_version=chosen_version,
        runtime=chosen_runtime,
        os_name=chosen_os,
        detected_packages=tuple(sorted({package.name for package in related}, key=str.lower)),
        sources=sources,
        warnings=tuple(warnings),
    )
