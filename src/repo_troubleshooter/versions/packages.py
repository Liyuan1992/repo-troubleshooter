"""Which packages belong to the same product, learned from the repository.

Two reports naming `@deepseek-ai/dsh` and `@deepseek-ai/dsh-client-modules` are
not naming unrelated things - one ships inside the other. Knowing that must not
come from a hardcoded name: it comes from the repository's own manifests, read
out of the git mirror at sync time.

Every `package.json` in the tree is recorded, which is what tells us the names
this product publishes. The relation between two of them is **name ancestry** on
a segment boundary: `@x/dsh` is an ancestor of `@x/dsh-client-modules`. That is
what npm ecosystems already encode, so it needs no list of names.

A shared scope is deliberately not a relation. In a monorepo nearly everything
shares the scope, and accepting it would quietly disable the package-conflict
rule the identity gate depends on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.store.models import PackageManifest, Repository

MANIFEST_NAME = "package.json"
MAX_MANIFESTS = 400


@dataclass
class ManifestRecord:
    name: str
    path: str
    version: str | None = None
    private: bool = False
    workspace_root: bool = False
    dependencies: list[str] = field(default_factory=list)


def discover_manifests(git: GitRepo, ref: str = "HEAD") -> list[ManifestRecord]:
    """Read every package.json in the tree. No repository-specific knowledge."""
    records: list[ManifestRecord] = []
    for blob_sha, path in git.ls_tree_entries(ref):
        if not path.endswith(MANIFEST_NAME) or "node_modules/" in path:
            continue
        if len(records) >= MAX_MANIFESTS:
            break
        raw = git.show_blob(blob_sha)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        name = payload.get("name")
        if not isinstance(name, str) or not name:
            continue
        dependencies = sorted(
            {
                *(payload.get("dependencies") or {}),
                *(payload.get("peerDependencies") or {}),
            }
        )
        records.append(
            ManifestRecord(
                name=name.lower(),
                path=path,
                version=payload.get("version") if isinstance(payload.get("version"), str) else None,
                private=bool(payload.get("private")),
                workspace_root=path.count("/") == 0,
                dependencies=[d.lower() for d in dependencies if isinstance(d, str)][:100],
            )
        )
    return records


def store_manifests(
    session: Session, repo: Repository, records: list[ManifestRecord]
) -> tuple[int, int]:
    """Replace this repository's manifest rows. Returns (deleted, inserted)."""
    existing = session.scalars(
        select(PackageManifest).where(PackageManifest.repo_id == repo.id)
    ).all()
    deleted = len(existing)
    for row in existing:
        session.delete(row)
    session.flush()

    for record in records:
        session.add(
            PackageManifest(
                repo_id=repo.id,
                name=record.name,
                path=record.path,
                version=record.version,
                is_private=record.private,
                is_workspace_root=record.workspace_root,
                extra={"dependencies": record.dependencies},
            )
        )
    session.flush()
    return deleted, len(records)


def _is_name_ancestor(ancestor: str, descendant: str) -> bool:
    """`@x/dsh` is an ancestor of `@x/dsh-client-modules`, on a segment boundary."""
    if ancestor == descendant or not descendant.startswith(ancestor):
        return False
    return descendant[len(ancestor)] in "-/."


@dataclass
class PackageFamily:
    """Which package names this repository publishes, and how they relate."""

    names: frozenset[str] = frozenset()
    roots: frozenset[str] = frozenset()

    @classmethod
    def load(cls, session: Session, repo_id: int) -> PackageFamily:
        rows = session.execute(
            select(PackageManifest.name, PackageManifest.is_workspace_root).where(
                PackageManifest.repo_id == repo_id
            )
        ).all()
        return cls(
            names=frozenset(name for name, _ in rows),
            roots=frozenset(name for name, is_root in rows if is_root),
        )

    def owns(self, name: str) -> bool:
        return name in self.names

    def related(self, left: str, right: str) -> bool:
        """True when one package ships inside the other.

        Name ancestry on a segment boundary, which is what npm ecosystems
        actually encode: `@x/dsh` is the product, `@x/dsh-client-modules` is a
        piece of it.

        A shared scope is deliberately *not* enough. In a monorepo almost every
        package shares the scope, so accepting that would relate
        `@x/cordis` to `@x/dsh-client-modules` and quietly disable the package
        conflict this whole gate exists to enforce.

        Ancestry alone is not enough either. For a match to be *accepted* on a
        product relation, **both** names must be published by this repository
        according to the manifests read from its tree. A name that merely shares
        a prefix - `@scope/dsh-fabricated` - is not part of the product just
        because it looks like one, and an empty family relates nothing.
        """
        if left == right:
            return True
        if not (self.owns(left) and self.owns(right)):
            return False
        return _is_name_ancestor(left, right) or _is_name_ancestor(right, left)

    def related_for_retrieval(self, left: str, right: str) -> bool:
        """Looser relation, for finding candidates only.

        Stage 1 may follow a prefix relation with evidence on just one side,
        because surfacing a candidate costs nothing - the identity gate still
        has to accept it, and acceptance uses the strict `related`. An unknown
        prefix name can therefore help recall without ever proving identity.
        """
        if self.related(left, right):
            return True
        if not (self.owns(left) or self.owns(right)):
            return False
        return _is_name_ancestor(left, right) or _is_name_ancestor(right, left)

    def expand_for_retrieval(self, names: set[str]) -> set[str]:
        """Published names worth pulling into stage 1 alongside ``names``."""
        expanded: set[str] = set()
        for published in self.names:
            if any(self.related_for_retrieval(published, name) for name in names):
                expanded.add(published)
        return expanded

    def any_related(self, left: set[str], right: set[str]) -> list[tuple[str, str]]:
        return [(a, b) for a in sorted(left) for b in sorted(right) if self.related(a, b)]

    def to_json(self) -> dict[str, Any]:
        return {"packages": len(self.names), "roots": sorted(self.roots)}
