"""Git connector.

Git answers the questions GitHub cannot answer honestly:
tags, commit ancestry, release containment, and what the docs said at a tag.

Every containment answer carries the exact command transcript, because
``RELEASE_CONTAINS_COMMIT`` is a deterministic claim we must be able to
re-verify later.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(argv)} failed ({returncode}): {stderr.strip()}")
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class GitResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    def transcript(self) -> dict[str, object]:
        return {
            "command": "git " + " ".join(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout.strip()[:2000],
        }


@dataclass(frozen=True)
class TagRef:
    name: str
    commit_sha: str
    tagged_at: dt.datetime | None


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    short_sha: str
    subject: str
    body: str
    author_name: str
    authored_at: dt.datetime | None
    committed_at: dt.datetime | None
    parents: list[str] = field(default_factory=list)


def _parse_iso(value: str) -> dt.datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


class GitRepo:
    """A bare mirror of an upstream repository."""

    # Unit separator keeps commit fields unambiguous even with newlines in bodies.
    _FS = "\x1f"
    _RS = "\x1e"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        git = shutil.which("git")
        if not git:
            raise GitError(["--version"], 127, "git executable not found on PATH")
        self._git: str = git

    # --- process plumbing -------------------------------------------------

    def _run(
        self,
        argv: list[str],
        check: bool = True,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> GitResult:
        proc = subprocess.run(  # noqa: S603 - fixed executable, argv list, no shell
            [self._git, *argv],
            cwd=str(cwd or self.path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        result = GitResult(argv, proc.returncode, proc.stdout, proc.stderr)
        if check and proc.returncode != 0:
            raise GitError(argv, proc.returncode, proc.stderr)
        return result

    # --- lifecycle --------------------------------------------------------

    @property
    def exists(self) -> bool:
        return (self.path / "HEAD").exists() or (self.path / ".git").exists()

    @classmethod
    def ensure(cls, path: Path | str, clone_url: str) -> GitRepo:
        """Clone a bare mirror if missing; otherwise reuse the existing one."""
        path = Path(path)
        git = shutil.which("git")
        if not git:
            raise GitError(["--version"], 127, "git executable not found on PATH")
        if not (path / "HEAD").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(  # noqa: S603
                # Blobless mirrors retain the complete commit/tag graph needed
                # for ancestry and release containment, while avoiding an
                # up-front download of every historical model fixture or image.
                # `show_blob` transparently fetches a blob later when versioned
                # docs actually need it.
                [git, "clone", "--mirror", "--filter=blob:none", clone_url, str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if proc.returncode != 0:
                raise GitError(["clone", "--mirror", clone_url], proc.returncode, proc.stderr)
        return cls(path)

    def fetch(self) -> GitResult:
        """Incremental update of refs and tags. Safe to call repeatedly."""
        return self._run(["remote", "update", "--prune"])

    # --- reads ------------------------------------------------------------

    def resolve_ref(self, ref: str) -> str | None:
        result = self._run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False)
        sha = result.stdout.strip()
        return sha or None

    def list_tags(self) -> list[TagRef]:
        fmt = self._FS.join(["%(refname:short)", "%(objectname)", "%(creatordate:iso-strict)"])
        result = self._run(["for-each-ref", "--format", fmt, "refs/tags"])
        tags: list[TagRef] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            name, objname, created = (line.split(self._FS) + ["", "", ""])[:3]
            # Annotated tags point at a tag object; peel to the commit.
            commit_sha = self.resolve_ref(name) or objname
            tags.append(TagRef(name=name, commit_sha=commit_sha, tagged_at=_parse_iso(created)))
        return tags

    def commit_info(self, sha: str) -> CommitInfo | None:
        fmt = self._FS.join(["%H", "%h", "%s", "%an", "%aI", "%cI", "%P", "%b"])
        result = self._run(
            ["show", "--no-patch", f"--format={fmt}", f"{sha}^{{commit}}"],
            check=False,
            # A hex token copied from a log may resemble an unreachable commit.
            # Partial clones otherwise contact the network for every such token.
            env={**os.environ, "GIT_NO_LAZY_FETCH": "1"},
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.split(self._FS)
        if len(parts) < 8:
            return None
        full, short, subject, author, authored, committed, parents, body = parts[:8]
        return CommitInfo(
            sha=full.strip(),
            short_sha=short.strip(),
            subject=subject.strip(),
            body=body.strip(),
            author_name=author.strip(),
            authored_at=_parse_iso(authored),
            committed_at=_parse_iso(committed),
            parents=[p for p in parents.split() if p],
        )

    def commit_exists(self, sha: str) -> bool:
        return self.resolve_ref(sha) is not None

    # --- containment ------------------------------------------------------

    def is_ancestor(self, commit_sha: str, ref: str) -> tuple[bool | None, GitResult]:
        """True/False when both refs resolve, None when either is unknown.

        Proves ancestry only. It does NOT prove that a symptom was fixed.
        """
        result = self._run(
            ["merge-base", "--is-ancestor", f"{commit_sha}^{{commit}}", f"{ref}^{{commit}}"],
            check=False,
        )
        if result.returncode == 0:
            return True, result
        if result.returncode == 1:
            return False, result
        return None, result

    def tags_containing(self, commit_sha: str) -> tuple[list[str], GitResult]:
        result = self._run(["tag", "--contains", commit_sha], check=False)
        if result.returncode != 0:
            return [], result
        return [line.strip() for line in result.stdout.splitlines() if line.strip()], result

    # --- content ----------------------------------------------------------

    def show_file(self, ref: str, path: str) -> str | None:
        result = self._run(["show", f"{ref}:{path}"], check=False)
        return result.stdout if result.returncode == 0 else None

    def list_files(self, ref: str, prefix: str = "") -> list[str]:
        argv = ["ls-tree", "-r", "--name-only", ref]
        if prefix:
            argv.append(prefix)
        result = self._run(argv, check=False)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def ls_tree_entries(self, ref: str, prefix: str = "") -> list[tuple[str, str]]:
        """(blob_sha, path) pairs. Blob shas let a sync skip unchanged files."""
        argv = ["ls-tree", "-r", ref]
        if prefix:
            argv += ["--", prefix]
        result = self._run(argv, check=False)
        if result.returncode != 0:
            return []
        entries: list[tuple[str, str]] = []
        for line in result.stdout.splitlines():
            meta, _, path = line.partition("	")
            fields = meta.split()
            if len(fields) < 3 or not path:
                continue
            obj_type, blob_sha = fields[1], fields[2]
            if obj_type != "blob":
                continue
            entries.append((blob_sha, path))
        return entries

    def show_blob(self, blob_sha: str) -> str | None:
        result = self._run(["cat-file", "-p", blob_sha], check=False)
        return result.stdout if result.returncode == 0 else None

    def diff_name_only(self, base: str, head: str, paths: list[str] | None = None) -> list[str]:
        argv = ["diff", "--name-only", f"{base}..{head}"]
        if paths:
            argv += ["--", *paths]
        result = self._run(argv, check=False)
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def log_with_files(
        self, base: str, head: str, limit: int = 400
    ) -> list[tuple[CommitInfo, list[str]]]:
        """Commits in base..head together with the files each one touched.

        Used to link a symptom to the change that plausibly fixed it: file paths
        are far more discriminating than commit prose.
        """
        fmt = self._FS.join(["%H", "%h", "%s", "%an", "%aI", "%cI", "%P"])
        result = self._run(
            ["log", f"--format={self._RS}{fmt}", "--name-only", f"-{limit}", f"{base}..{head}"],
            check=False,
        )
        if result.returncode != 0:
            return []
        out: list[tuple[CommitInfo, list[str]]] = []
        for record in result.stdout.split(self._RS):
            record = record.strip()
            if not record.strip():
                continue
            header, _, files_blob = record.partition(chr(10))
            parts = header.split(self._FS)
            if len(parts) < 7:
                continue
            full, short, subject, author, authored, committed, parents = parts[:7]
            files = [line.strip() for line in files_blob.splitlines() if line.strip()]
            out.append(
                (
                    CommitInfo(
                        sha=full.strip(),
                        short_sha=short.strip(),
                        subject=subject.strip(),
                        body="",
                        author_name=author.strip(),
                        authored_at=_parse_iso(authored),
                        committed_at=_parse_iso(committed),
                        parents=[p for p in parents.split() if p],
                    ),
                    files,
                )
            )
        return out

    def log_between(self, base: str, head: str, limit: int = 500) -> list[CommitInfo]:
        fmt = self._FS.join(["%H", "%h", "%s", "%an", "%aI", "%cI", "%P"]) + self._RS
        result = self._run(["log", f"--format={fmt}", f"-{limit}", f"{base}..{head}"], check=False)
        if result.returncode != 0:
            return []
        commits: list[CommitInfo] = []
        for record in result.stdout.split(self._RS):
            record = record.strip()
            if not record:
                continue
            parts = record.split(self._FS)
            if len(parts) < 7:
                continue
            full, short, subject, author, authored, committed, parents = parts[:7]
            commits.append(
                CommitInfo(
                    sha=full.strip(),
                    short_sha=short.strip(),
                    subject=subject.strip(),
                    body="",
                    author_name=author.strip(),
                    authored_at=_parse_iso(authored),
                    committed_at=_parse_iso(committed),
                    parents=[p for p in parents.split() if p],
                )
            )
        return commits
