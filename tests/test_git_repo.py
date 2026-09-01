"""Git connector tests against a real, locally built repository.

No network. Builds a small history with tags so ancestry and containment are
checked against actual git behaviour rather than a mock.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_troubleshooter.connectors.git.repo import GitRepo


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8"
    )
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def sample_repo(tmp_path_factory) -> GitRepo:
    work = tmp_path_factory.mktemp("upstream")
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")

    docs = work / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("v1 guide\n", encoding="utf-8")
    (docs / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "first")
    _git(work, "tag", "dsh-v0.1.0")

    (docs / "guide.md").write_text("v2 guide with the fix\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "fix: service llm not found")
    fix_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "tag", "-a", "dsh-v0.1.1", "-m", "annotated release")

    repo = GitRepo(work)
    repo.fix_sha = fix_sha  # type: ignore[attr-defined]
    return repo


class TestTagsAndCommits:
    def test_list_tags_peels_annotated_tags_to_commits(self, sample_repo):
        tags = {t.name: t for t in sample_repo.list_tags()}
        assert set(tags) == {"dsh-v0.1.0", "dsh-v0.1.1"}
        assert len(tags["dsh-v0.1.1"].commit_sha) == 40
        assert tags["dsh-v0.1.1"].commit_sha == sample_repo.fix_sha

    def test_commit_info(self, sample_repo):
        info = sample_repo.commit_info(sample_repo.fix_sha)
        assert info is not None
        assert info.subject == "fix: service llm not found"
        assert len(info.parents) == 1

    def test_unknown_commit_returns_none(self, sample_repo):
        assert sample_repo.commit_info("0" * 40) is None
        assert sample_repo.commit_exists("0" * 40) is False


class TestContainment:
    def test_ancestry_direction(self, sample_repo):
        contains, result = sample_repo.is_ancestor(sample_repo.fix_sha, "dsh-v0.1.1")
        assert contains is True
        assert result.transcript()["command"].startswith("git merge-base --is-ancestor")

        contains, _ = sample_repo.is_ancestor(sample_repo.fix_sha, "dsh-v0.1.0")
        assert contains is False

    def test_unknown_ref_is_unknown_not_false(self, sample_repo):
        contains, _ = sample_repo.is_ancestor("0" * 40, "dsh-v0.1.1")
        assert contains is None

    def test_tags_containing(self, sample_repo):
        tags, _ = sample_repo.tags_containing(sample_repo.fix_sha)
        assert tags == ["dsh-v0.1.1"]


class TestContent:
    def test_show_file_at_tag_returns_that_version(self, sample_repo):
        assert sample_repo.show_file("dsh-v0.1.0", "docs/guide.md").strip() == "v1 guide"
        assert "fix" in sample_repo.show_file("dsh-v0.1.1", "docs/guide.md")

    def test_ls_tree_entries_gives_blob_shas(self, sample_repo):
        entries = dict(
            (path, blob) for blob, path in sample_repo.ls_tree_entries("dsh-v0.1.1", "docs/")
        )
        assert "docs/guide.md" in entries
        assert len(entries["docs/guide.md"]) == 40

    def test_blob_sha_changes_only_when_content_changes(self, sample_repo):
        old = dict((p, b) for b, p in sample_repo.ls_tree_entries("dsh-v0.1.0", "docs/"))
        new = dict((p, b) for b, p in sample_repo.ls_tree_entries("dsh-v0.1.1", "docs/"))
        assert old["docs/guide.md"] != new["docs/guide.md"]
        assert old["docs/logo.png"] == new["docs/logo.png"]

    def test_show_blob(self, sample_repo):
        entries = dict((p, b) for b, p in sample_repo.ls_tree_entries("dsh-v0.1.1", "docs/"))
        assert "fix" in sample_repo.show_blob(entries["docs/guide.md"])
