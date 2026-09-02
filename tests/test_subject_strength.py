"""Subjects carry a role, and only the right role may veto.

Pinned here:

1. **Adjectives are not subjects.** `customer-facing`, `time-sensitive` and the
   rest are descriptions. A hyphen is not evidence.
2. **Transformation invariance.** Padding a correct report with business nouns,
   quotes and tracker tags must not change the incident decision.
3. **Role precedence.** A scoped-package conflict is decisive and cannot be
   cancelled by a shared `node:*` builtin, a shared dependency, a shared source
   path, or a shared module name.
4. **Weak roles cannot refuse.** A module-name mismatch raises the bar; it never
   produces a `different_subject` veto on its own.
"""

from __future__ import annotations

from repo_troubleshooter.fingerprint import features as feat
from repo_troubleshooter.fingerprint.subjects import (
    classify,
    morphology_proves_module,
    syntax_proves_module,
)
from repo_troubleshooter.retrieval.identity import evaluate

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

ADJECTIVE_MODIFIERS = [
    "This is a customer-facing, time-sensitive regression.",
    "High-priority, user-visible, long-running issue for a business-critical deployment.",
    "Reported by a well-known enterprise customer; considered release-blocking "
    "and revenue-impacting.",
]

# Item 5a: business nouns, quotation and tracker tags - all noise, none of it subject.
NOISE_MODIFIERS = [
    'Our customer "Contoso Manufacturing" hit this during the quarterly release window.',
    "[SEV-2] [triage] [needs-info] filed as TICKET-4821 by the platform team.",
    "Business impact: onboarding, revenue recognition and the compliance audit are blocked.",
    "Quoting the runbook: 'restart the service and escalate to the on-call engineer'.",
]


def features(text: str, **kwargs):  # noqa: ANN003, ANN201
    return feat.extract(text, **kwargs)


class TestSubjectRoles:
    def test_roles_are_separated(self):
        subjects = classify(
            "@deepseek-ai/dsh-client-modules fails in packages/loader/src/internal.ts "
            "while importing node:path; peer dependency react@^19 is installed"
        )
        assert "@deepseek-ai/dsh-client-modules" in subjects.primary_packages
        assert "loader/src/internal.ts" in subjects.paths
        assert "node:path" in subjects.builtins
        assert "react" in subjects.dependencies

    def test_builtins_never_count_as_identifying(self):
        subjects = classify("importing node:path and node:fs failed")
        assert subjects.builtins
        assert not subjects.identifying, "a builtin must not identify an incident"

    def test_builtin_alone_cannot_match_two_reports(self):
        query = features("node:path resolution failed during startup")
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted


class TestAdjectivesAreNotSubjects:
    def test_common_modifiers_are_never_subjects(self):
        extracted = features(" ".join(ADJECTIVE_MODIFIERS))
        assert extracted.subject == set(), f"leaked subjects: {extracted.subject}"

    def test_business_noise_is_never_a_subject(self):
        extracted = features(" ".join(NOISE_MODIFIERS))
        assert extracted.subject_packages == set()
        assert extracted.subject_paths == set()

    def test_morphology_rejects_participles_and_adjectives(self):
        for token in (
            "customer-facing",
            "time-sensitive",
            "user-visible",
            "long-running",
            "release-blocking",
            "revenue-impacting",
            "non-widening",
            "well-known",
        ):
            assert not morphology_proves_module(token), token

    def test_morphology_accepts_module_shaped_names(self):
        for token in (
            "theme-parser",
            "markdown-renderer",
            "session-exporter",
            "config-loader",
            "client-modules",
            "esm-shim",
        ):
            assert morphology_proves_module(token), token

    def test_syntax_proof_needs_a_code_like_context(self):
        assert syntax_proves_module("weird-thing", "install `weird-thing` first")
        assert syntax_proves_module("weird-thing", "weird-thing/src/index.ts blew up")
        assert syntax_proves_module("weird-thing", "the plugin weird-thing failed")
        assert not syntax_proves_module("weird-thing", "this is a weird-thing to happen")

    def test_corpus_proof_admits_a_name_the_repository_knows(self):
        text = "the web-search-deepseek integration returns nothing"
        assert "web-search-deepseek" not in features(text).subject_modules
        with_corpus = features(text, known_modules=frozenset({"web-search-deepseek"}))
        assert "web-search-deepseek" in with_corpus.subject_modules


class TestTransformationInvariance:
    """Padding a correct report with noise must change nothing."""

    def _verdict(self, text: str):  # noqa: ANN202
        return evaluate(features(text), features(LOADER_THREAD))

    def _assert_same(self, padded: str, baseline, label: str) -> None:  # noqa: ANN001
        verdict = self._verdict(padded)
        assert verdict.accepted == baseline.accepted, label
        assert verdict.rule == baseline.rule, label
        assert verdict.rejection == baseline.rejection, label
        assert verdict.shared["subject_package"] == baseline.shared["subject_package"], label

    def test_baseline_matches(self):
        assert self._verdict(CORRECT_REPORT).accepted

    def test_adjective_modifiers_change_nothing(self):
        baseline = self._verdict(CORRECT_REPORT)
        for modifier in ADJECTIVE_MODIFIERS:
            for padded in (f"{modifier} {CORRECT_REPORT}", f"{CORRECT_REPORT} {modifier}"):
                self._assert_same(padded, baseline, modifier)

    def test_business_nouns_quotes_and_tags_change_nothing(self):
        baseline = self._verdict(CORRECT_REPORT)
        for modifier in NOISE_MODIFIERS:
            for padded in (f"{modifier} {CORRECT_REPORT}", f"{CORRECT_REPORT} {modifier}"):
                self._assert_same(padded, baseline, modifier)

    def test_everything_at_once_changes_nothing(self):
        baseline = self._verdict(CORRECT_REPORT)
        padded = " ".join(ADJECTIVE_MODIFIERS + NOISE_MODIFIERS) + " " + CORRECT_REPORT
        self._assert_same(padded, baseline, "all modifiers")

    def test_noise_does_not_rescue_an_unrelated_report(self):
        """Invariance cuts both ways: padding must not create a match either."""
        unrelated = "Redis connection lost: READONLY You can't write against a read only replica"
        assert not self._verdict(unrelated).accepted
        padded = " ".join(ADJECTIVE_MODIFIERS + NOISE_MODIFIERS) + " " + unrelated
        assert not self._verdict(padded).accepted


class TestPackageConflictOutranksEverything:
    """A foreign package stays foreign however much of the candidate you paste in."""

    FOREIGN = "@acme/theme-kit crashed on Windows"

    def test_plain_foreign_package(self):
        query = features(
            f"{self.FOREIGN}: the client boot graph has zero entries and zero batches, "
            "client-modules never preloads, TypeError: e.indexOf is not a function"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"
        assert "different packages" in verdict.reasons[0]

    def test_shared_runtime_builtin_does_not_rescue_it(self):
        query = features(f"{self.FOREIGN} while importing node:path and node:module")
        candidate = features(LOADER_THREAD)
        assert query.subject_builtins & candidate.subject_builtins
        verdict = evaluate(query, candidate)
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"

    def test_shared_source_path_does_not_rescue_it(self):
        query = features(f"{self.FOREIGN}, raised from packages/loader/src/internal.ts")
        candidate = features(LOADER_THREAD)
        assert query.subject_paths & candidate.subject_paths
        verdict = evaluate(query, candidate)
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"

    def test_shared_module_name_does_not_rescue_it(self):
        query = features(f"{self.FOREIGN}: client-modules did not preload, __DSH_BOOT__ is empty")
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"

    def test_shared_dependency_does_not_rescue_it(self):
        query = features(
            f"{self.FOREIGN}: peer dependency react@^19, TypeError: e.indexOf is not a function"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted

    def test_all_of_them_together_still_do_not_rescue_it(self):
        query = features(
            f"{self.FOREIGN}: importing node:path and node:module, raised from "
            "packages/loader/src/internal.ts, client-modules did not preload, "
            "__DSH_BOOT__ has zero entries and zero batches, "
            "TypeError: e.indexOf is not a function"
        )
        candidate = features(LOADER_THREAD)
        assert query.subject_builtins & candidate.subject_builtins
        assert query.subject_paths & candidate.subject_paths
        verdict = evaluate(query, candidate)
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"
        assert "different packages" in verdict.reasons[0]

    def test_the_real_package_still_matches(self):
        verdict = evaluate(features(CORRECT_REPORT), features(LOADER_THREAD))
        assert verdict.accepted
        assert verdict.shared["subject_package"]


class TestWeakRolesCannotVeto:
    """A module-name mismatch raises the bar; it never refuses on its own."""

    def test_module_mismatch_is_not_a_hard_veto(self):
        query = features(
            "theme-parser crashes during startup: TypeError: e.indexOf is not a function "
            "while reading the palette"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        # Refused for lack of identity evidence, NOT by a subject veto.
        assert verdict.rejection == "insufficient_identity_evidence"
        assert verdict.weakened_by

    def test_dependency_mismatch_is_not_a_hard_veto(self):
        query = features(
            "peer dependency react@^19 could not be resolved; TypeError: e.indexOf is not a "
            "function during install"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.rejection != "different_subject"

    def test_a_paraphrase_naming_nothing_is_unaffected(self):
        query = features(
            "The Harness web page starts on Windows but the client boot graph has no entries or "
            "batches, and the browser never preloads the dsh client JavaScript module."
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.accepted
        assert verdict.rule == "behaviour_profile_plus_component"
