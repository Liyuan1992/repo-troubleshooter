"""The version-perturbation guard.

Same incident, same commit, different user version must produce a different
verdict. A system that answers "upgrade" both times is pattern matching, not
version-aware troubleshooting - so this test exists to fail loudly if the
containment verdict ever stops depending on the user's version.
"""

from repo_troubleshooter.profiles.loader import RepoProfile
from repo_troubleshooter.sync.orchestrator import normalize_tag
from repo_troubleshooter.versions.containment import ContainmentResult, version_already_contains

PROFILE = RepoProfile.model_validate(
    {"repo": "deepseek-ai/deepseek-harness", "version": {"tag_prefixes": ["dsh-v", "v"]}}
)


def result_for(first_release: str | None) -> ContainmentResult:
    return ContainmentResult(
        commit_sha="dd6322d",
        resolved_sha="dd6322d" * 5 + "abcde",
        first_release_containing=first_release,
    )


class TestNormalizeTag:
    def test_profile_prefix_is_stripped(self):
        assert normalize_tag("dsh-v0.1.2-alpha.3", PROFILE) == "0.1.2a3"
        assert normalize_tag("v0.1.0-rc.7", PROFILE) == "0.1.0rc7"

    def test_unparseable_tag_stays_unknown(self):
        assert normalize_tag("nightly-2026-08-30", PROFILE) is None


class TestVersionPerturbation:
    def test_older_version_does_not_contain_the_change(self):
        verdict, why = version_already_contains(result_for("dsh-v0.1.2-alpha.3"), "0.1.1-rc.2")
        assert verdict is False
        assert "<" in why

    def test_same_version_already_contains_the_change(self):
        verdict, why = version_already_contains(result_for("dsh-v0.1.2-alpha.3"), "0.1.2-alpha.3")
        assert verdict is True
        assert "does not by itself prove" in why

    def test_newer_version_already_contains_the_change(self):
        verdict, _ = version_already_contains(result_for("dsh-v0.1.2-alpha.3"), "0.2.0")
        assert verdict is True

    def test_verdict_actually_flips(self):
        """The whole point: one input change must change the conclusion."""
        containment = result_for("dsh-v0.1.2-alpha.3")
        before, _ = version_already_contains(containment, "0.1.1-rc.2")
        after, _ = version_already_contains(containment, "0.1.2-alpha.3")
        assert before is not after


class TestAbstention:
    def test_unknown_user_version_is_unknown_not_upgrade(self):
        verdict, why = version_already_contains(result_for("dsh-v0.1.2-alpha.3"), "nightly")
        assert verdict is None
        assert "could not parse" in why

    def test_no_release_contains_the_change_yet(self):
        verdict, why = version_already_contains(result_for(None), "0.1.1-rc.2")
        assert verdict is None
        assert "no known release" in why
