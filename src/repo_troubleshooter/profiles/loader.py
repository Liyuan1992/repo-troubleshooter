"""Repo profiles.

Generalisation in V1 is a YAML file, not an automatic repository profiler.
Onboarding a second repository should mean editing a profile - if it forces
changes in retrieval/evidence/version code, the design is not general yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from repo_troubleshooter.config import get_settings

SurfaceFlag = Literal[True, False, "auto"]


def _default_holdout_kinds() -> list[Literal["discussion", "issue"]]:
    return ["discussion"]


class SupportSurfaces(BaseModel):
    discussions: SurfaceFlag = "auto"
    issues: SurfaceFlag = "auto"
    prs: SurfaceFlag = "auto"


class VersionPolicy(BaseModel):
    strategy: str = "semver_like"
    # Tag decoration to strip, e.g. dsh-v0.1.2-alpha.3 -> 0.1.2-alpha.3
    tag_prefixes: list[str] = Field(default_factory=list)
    # Tags that are not product releases (nightly builds, per-package tags).
    ignore_tag_patterns: list[str] = Field(default_factory=list)


class DocsPolicy(BaseModel):
    paths: list[str] = Field(default_factory=list)
    # Files to skip: translations and i18n bundles duplicate the canonical doc.
    exclude_patterns: list[str] = Field(default_factory=list)


class LocalContextPolicy(BaseModel):
    """How a local checkout exposes the product version and runtime.

    This belongs in the profile because a repository may publish many packages,
    while only one or two carry the product version users mean when they say
    "my current version". The collector still discovers values from manifests;
    the profile only says which discovered package is the core product.
    """

    core_packages: list[str] = Field(default_factory=list)
    runtimes: list[Literal["node", "python"]] = Field(default_factory=list)


class SeedObjectsPolicy(BaseModel):
    """Historical objects that must be fetched even when the live walk is capped."""

    issues: list[int] = Field(default_factory=list)
    pull_requests: list[int] = Field(default_factory=list)


class ReviewedIncidentPolicy(BaseModel):
    """Human-reviewed issue -> PR -> release chain kept in the repository profile.

    This is authority, not a retrieval hint.  Sync validates every pointer
    against GitHub and git ancestry before writing a reviewed record.
    """

    key: str
    issue: int
    pull_request: int
    merge_commit: str
    first_release: str
    reported_versions: list[str] = Field(default_factory=list)
    notes: str | None = None


class HoldoutPolicy(BaseModel):
    """Repository-specific inputs for the real-report leave-one-out census.

    These values define the measured population and the positive control. They
    are evaluation policy, not identity rules: the diagnosis engine never reads
    them.
    """

    report_kinds: list[Literal["discussion", "issue"]] = Field(
        default_factory=_default_holdout_kinds
    )
    assumed_version: str | None = None
    positive_control_error: str | None = None
    case_version_source: Literal["fixed", "report"] = "fixed"
    report_version_patterns: list[str] = Field(default_factory=list)


class RepoProfile(BaseModel):
    repo: str
    clone_url: str | None = None
    host: str = "github.com"
    role: Literal["live", "evaluation"] = "live"
    support_surfaces: SupportSurfaces = Field(default_factory=SupportSurfaces)
    version: VersionPolicy = Field(default_factory=VersionPolicy)
    docs: DocsPolicy = Field(default_factory=DocsPolicy)
    local_context: LocalContextPolicy = Field(default_factory=LocalContextPolicy)
    seed_objects: SeedObjectsPolicy = Field(default_factory=SeedObjectsPolicy)
    reviewed_incidents: list[ReviewedIncidentPolicy] = Field(default_factory=list)
    holdout: HoldoutPolicy = Field(default_factory=HoldoutPolicy)
    error_patterns: list[str] = Field(default_factory=list)
    environment: list[str] = Field(default_factory=list)
    notes: str | None = None

    @property
    def owner(self) -> str:
        return self.repo.split("/")[0]

    @property
    def name(self) -> str:
        return self.repo.split("/")[1]

    @property
    def slug(self) -> str:
        return self.repo.replace("/", "__")

    def resolved_clone_url(self) -> str:
        return self.clone_url or f"https://{self.host}/{self.repo}.git"

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def profile_path(name: str, profiles_dir: Path | None = None) -> Path | None:
    directory = profiles_dir or get_settings().profiles_dir
    candidates = [
        directory / f"{name}.yaml",
        directory / f"{name}.yml",
        directory / f"{name.replace('/', '__')}.yaml",
        directory / f"{name.split('/')[-1]}.yaml",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_profile(name: str, profiles_dir: Path | None = None) -> RepoProfile:
    path = profile_path(name, profiles_dir)
    if path is None:
        raise FileNotFoundError(
            f"no repo profile for '{name}' in {profiles_dir or get_settings().profiles_dir}"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RepoProfile.model_validate(data)


def list_profiles(profiles_dir: Path | None = None) -> list[RepoProfile]:
    directory = profiles_dir or get_settings().profiles_dir
    profiles: list[RepoProfile] = []
    for path in sorted(directory.glob("*.y*ml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles.append(RepoProfile.model_validate(data))
    return profiles
