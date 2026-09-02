"""A package's role comes from how the sentence uses it, not from its shape.

`@acme/theme-kit` looks the same whether it crashed or whether something else
imports it, and those are opposite facts. These tests pin the three consequences:

* only a package the report *blames* can establish or refuse package identity;
* a shared dependency can never cancel a conflict between two blamed packages;
* a query that names only dependencies cannot veto anything at all.

Plus the product relation: `@scope/dsh` and `@scope/dsh-client-modules` belong
to the same product, and that fact is read from the repository's own manifests,
never from a name written into this codebase.
"""

from __future__ import annotations

import pytest

from repo_troubleshooter.fingerprint import features as feat
from repo_troubleshooter.fingerprint.subjects import PackageRole, classify
from repo_troubleshooter.retrieval.identity import evaluate
from repo_troubleshooter.versions.packages import PackageFamily

LOADER_THREAD = """
loader: internal resolveSync mis-tagged v2 on Node 24.11 - client boot graph composes empty
With dsh web on Node 24.11.1 the web UI fails with
client-modules: HTML did not preload @deepseek-ai/dsh-client-modules/client.js
The server composes an empty client boot graph (__DSH_BOOT__ has zero entries and zero batches)
because every client package manifest resolution fails and is silently dropped.
ModuleLoader.fromInternal() in packages/loader/src/internal.ts tags the Node internal ESM loader
by major version, using node:module and node:path, so the call throws
TypeError: e.indexOf is not a function.
"""

CORRECT_REPORT = (
    "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; client-modules "
    "reports HTML did not preload @deepseek-ai/dsh-client-modules/client.js, and the host "
    "throws TypeError: e.indexOf is not a function"
)


def features(text: str, **kwargs):  # noqa: ANN003, ANN201
    return feat.extract(text, **kwargs)


class TestRoleComesFromContext:
    def test_the_thing_that_crashes_is_primary(self):
        subjects = classify("@acme/theme-kit crashes on startup")
        assert subjects.primary_packages == {"@acme/theme-kit"}
        assert not subjects.dependencies

    @pytest.mark.parametrize(
        "sentence",
        [
            "@acme/app depends on @scope/lib",
            "@acme/app uses @scope/lib",
            "@acme/app imports @scope/lib",
            "@acme/app requires @scope/lib",
            "peer dependency @scope/lib is missing",
            "installing @scope/lib pulled the wrong version",
        ],
    )
    def test_a_used_package_is_a_dependency_even_when_scoped(self, sentence):
        subjects = classify(sentence)
        assert "@scope/lib" in subjects.dependencies, sentence
        assert "@scope/lib" not in subjects.primary_packages, sentence

    def test_both_roles_in_one_sentence(self):
        subjects = classify("@acme/app fails because it imports @scope/lib")
        assert "@acme/app" in subjects.primary_packages
        assert "@scope/lib" in subjects.dependencies

    def test_the_object_of_a_failure_is_primary(self):
        subjects = classify("HTML did not preload @deepseek-ai/dsh-client-modules/client.js")
        assert "@deepseek-ai/dsh-client-modules" in subjects.primary_packages

    def test_a_bare_mention_is_neither(self):
        subjects = classify("see the notes in @acme/handbook for background")
        assert "@acme/handbook" in subjects.mentioned_packages
        assert not subjects.primary_packages
        assert not subjects.dependencies

    def test_mentions_keep_their_span_and_cue(self):
        text = "@acme/theme-kit crashes while it uses @scope/lib"
        mentions = {m.name: m for m in classify(text).package_mentions}
        crashed = mentions["@acme/theme-kit"]
        used = mentions["@scope/lib"]
        assert crashed.role is PackageRole.PRIMARY
        assert text[crashed.start : crashed.end] == "@acme/theme-kit"
        assert "crash" in crashed.cue
        assert used.role is PackageRole.DEPENDENCY
        assert text[used.start : used.end] == "@scope/lib"
        assert "uses" in used.cue.lower()


class TestOnlyPrimaryDecidesIdentity:
    def test_a_healthy_dependency_does_not_change_a_correct_match(self):
        """Item 7a: adding a healthy scoped dependency changes nothing."""
        baseline = evaluate(features(CORRECT_REPORT), features(LOADER_THREAD))
        assert baseline.accepted
        for extra in (
            "The app also depends on @sindresorhus/is, which is healthy.",
            "We use @types/node and import @scope/telemetry; both are fine.",
        ):
            verdict = evaluate(features(f"{CORRECT_REPORT} {extra}"), features(LOADER_THREAD))
            assert verdict.accepted, extra
            assert verdict.rule == baseline.rule, extra
            assert verdict.shared["subject_package"] == baseline.shared["subject_package"], extra

    def test_an_unrelated_package_importing_the_candidate_is_still_refused(self):
        """Item 7b: `@acme/x uses <candidate package>` must not become a match."""
        query = features(
            "@acme/theme-kit crashes on Windows; it imports "
            "@deepseek-ai/dsh-client-modules and the boot graph has zero entries, "
            "TypeError: e.indexOf is not a function"
        )
        candidate = features(LOADER_THREAD)
        assert "@deepseek-ai/dsh-client-modules" in query.subject_dependencies
        assert "@acme/theme-kit" in query.subject_packages
        verdict = evaluate(query, candidate)
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"

    def test_a_shared_dependency_never_cancels_a_primary_conflict(self):
        """Item 4, stated directly."""
        query = features(
            "@acme/theme-kit fails; it depends on @deepseek-ai/dsh-client-modules and "
            "node:path, TypeError: e.indexOf is not a function"
        )
        candidate = features(LOADER_THREAD)
        verdict = evaluate(query, candidate)
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"
        assert "blame different packages" in verdict.reasons[0]

    def test_a_dependency_only_query_cannot_veto(self):
        """Item 5: no primary named, so no hard refusal on subject grounds."""
        query = features(
            "our build depends on @acme/design-system and the client boot graph has no "
            "entries or batches, nothing is preloaded"
        )
        assert not query.subject_packages
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.rejection != "different_subject"


class TestProductRelationIsDataDriven:
    """Item 7c: `@scope/dsh` and its sub-packages, learned from the manifests."""

    def test_name_ancestry_relates_a_product_and_its_pieces(self):
        family = PackageFamily(
            names=frozenset({"@x/dsh", "@x/dsh-client-modules", "@x/cordis"}),
            roots=frozenset({"@x/dsh"}),
        )
        assert family.related("@x/dsh", "@x/dsh-client-modules")
        assert family.related("@x/dsh-client-modules", "@x/dsh")

    def test_a_shared_scope_is_not_a_relation(self):
        family = PackageFamily(names=frozenset({"@x/dsh-client-modules", "@x/cordis"}))
        assert not family.related("@x/cordis", "@x/dsh-client-modules")

    def test_an_outside_package_is_related_to_nothing(self):
        family = PackageFamily(names=frozenset({"@x/dsh", "@x/dsh-client-modules"}))
        assert not family.related("@acme/theme-kit", "@x/dsh-client-modules")

    def test_related_packages_are_not_a_conflict(self):
        family = PackageFamily(
            names=frozenset({"@x/dsh", "@x/dsh-client-modules"}), roots=frozenset({"@x/dsh"})
        )
        query = features(
            "@x/dsh crashes on Windows: HTML did not preload the client bundle, "
            "TypeError: e.indexOf is not a function"
        )
        candidate = features(
            "@x/dsh-client-modules fails: HTML did not preload client.js, "
            "TypeError: e.indexOf is not a function"
        )
        assert query.subject_packages and candidate.subject_packages
        assert not (query.subject_packages & candidate.subject_packages)

        without_family = evaluate(query, candidate)
        assert without_family.rejection == "different_subject"

        with_family = evaluate(query, candidate, package_family=family)
        assert with_family.rejection != "different_subject"
        assert with_family.shared.get("related_packages")


@pytest.mark.db
@pytest.mark.live
class TestFamilyComesFromTheRepository:
    def test_manifests_were_read_from_the_mirror(self, session, synced_repo):
        family = PackageFamily.load(session, synced_repo.id)
        assert len(family.names) > 50, "expected a monorepo's worth of manifests"

    def test_the_product_and_its_sub_packages_are_related(self, session, synced_repo):
        family = PackageFamily.load(session, synced_repo.id)
        # Found by shape, not by name: the shortest published name that is an
        # ancestor of others is the product root.
        candidates = sorted(family.names, key=len)
        product = next(
            (
                name
                for name in candidates
                if sum(1 for other in family.names if family.related(name, other)) > 10
            ),
            None,
        )
        assert product, "no package turned out to be an ancestor of others"
        children = [n for n in family.names if n != product and family.related(product, n)]
        assert len(children) > 10
