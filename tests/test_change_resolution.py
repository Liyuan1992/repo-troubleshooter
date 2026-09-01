"""Change-resolution tests against a real local git history.

The rule under test: a change may only be linked to a symptom when the symptom
named a source path and a fix commit changed that path. These tests encode the
false links an earlier, looser version produced - a chore commit, a test-only
change, a release merge, and a coincidental `src/index.ts` - so they cannot
come back.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_troubleshooter.connectors.git.repo import GitRepo
from repo_troubleshooter.relations.change_resolution import resolve_change, symptom_paths


class FakeRelease:
    def __init__(self, tag: str) -> None:
        self.tag_name = tag


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _commit(work: Path, message: str, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", message)


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> tuple[GitRepo, list[FakeRelease]]:
    work = tmp_path_factory.mktemp("changes")
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")

    _commit(work, "chore: seed", {"packages/loader/src/internal.ts": "v1\n", "README.md": "x\n"})
    _git(work, "tag", "app-v1.0.0")

    # Noise that an over-eager matcher used to pick.
    _commit(work, "chore: regenerate third-party notices", {"THIRD_PARTY_NOTICES.md": "n\n"})
    _commit(
        work,
        "test(loader): cover internal resolver stubs",
        {"packages/loader/tests/internal.spec.ts": "t\n"},
    )
    _commit(work, "refactor(loader): tidy internals", {"packages/loader/src/internal.ts": "v2\n"})
    _commit(work, "fix(other): unrelated area", {"packages/other/src/index.ts": "o\n"})
    # The real fix.
    _commit(
        work,
        "fix: node 24 internal resolver order",
        {"vendor/loader/src/internal.ts": "fixed\n", "vendor/README.md": "note\n"},
    )
    _git(work, "tag", "app-v1.1.0")

    return GitRepo(work), [FakeRelease("app-v1.0.0"), FakeRelease("app-v1.1.0")]


SYMPTOM = (
    "Boot graph is empty. The host throws TypeError: e.indexOf is not a function from "
    "packages/loader/src/internal.ts when resolving a module."
)


class TestSymptomPaths:
    def test_extracts_named_source_paths(self):
        tails, basenames = symptom_paths(SYMPTOM)
        assert "loader/src/internal.ts" in tails
        assert "internal.ts" in basenames

    def test_generic_tails_are_not_usable_identity(self):
        tails, basenames = symptom_paths("crash in packages/app/src/index.ts")
        assert "src/index.ts" not in tails
        assert "index.ts" not in basenames

    def test_prose_with_no_paths_yields_nothing(self):
        assert symptom_paths("it just does not start on windows") == (set(), set())


class TestResolveChange:
    def test_picks_the_fix_commit_that_touched_the_named_path(self, history):
        git, releases = history
        candidate = resolve_change(git, releases, {"internal.ts"}, symptom_text=SYMPTOM)
        assert candidate is not None
        assert candidate.subject.startswith("fix: node 24 internal resolver order")
        assert any("internal.ts" in path for path in candidate.matched_paths)

    def test_does_not_pick_chore_test_or_refactor_commits(self, history):
        git, releases = history
        candidate = resolve_change(git, releases, {"internal.ts"}, symptom_text=SYMPTOM)
        assert candidate is not None
        assert not candidate.subject.startswith(("chore", "test", "refactor"))

    def test_abstains_when_the_symptom_names_no_path(self, history):
        git, releases = history
        candidate = resolve_change(
            git, releases, {"boot", "startup"}, symptom_text="it fails to start on windows"
        )
        assert candidate is None

    def test_a_coincidental_generic_filename_is_not_a_link(self, history):
        git, releases = history
        candidate = resolve_change(
            git,
            releases,
            {"index.ts"},
            symptom_text="failure raised from packages/mine/src/index.ts",
        )
        assert candidate is None

    def test_no_releases_means_no_guess(self, history):
        git, _ = history
        assert (
            resolve_change(git, [FakeRelease("app-v1.0.0")], {"internal.ts"}, symptom_text=SYMPTOM)
            is None
        )
