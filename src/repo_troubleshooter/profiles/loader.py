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


class RepoProfile(BaseModel):
    repo: str
    clone_url: str | None = None
    host: str = "github.com"
    role: Literal["live", "evaluation"] = "live"
    support_surfaces: SupportSurfaces = Field(default_factory=SupportSurfaces)
    version: VersionPolicy = Field(default_factory=VersionPolicy)
    docs: DocsPolicy = Field(default_factory=DocsPolicy)
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
