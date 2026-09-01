"""Subjects have a type and a strength, and only real ones may veto.

Three properties are pinned here:

1. **Adjectives are not subjects.** `customer-facing`, `time-sensitive` and the
   rest are descriptions. A hyphen is not evidence.
2. **Modifier invariance.** Padding a correct report with those adjectives must
   not change the incident decision - if it does, the gate is keying on noise.
3. **Strong conflicts win.** A package or path conflict is decisive and cannot be
   bought off by a weak module name that happens to appear on both sides.
"""

from __future__ import annotations

from repo_troubleshooter.fingerprint import features as feat
from repo_troubleshooter.fingerprint.subjects import (
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
by major version, so the call throws TypeError: e.indexOf is not a function.
"""

CORRECT_REPORT = (
    "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; client-modules "
    "reports HTML did not preload @deepseek-ai/dsh-client-modules/client.js, and the host "
    "throws TypeError: e.indexOf is not a function"
)

MODIFIERS = [
    "This is a customer-facing, time-sensitive regression.",
    "High-priority, user-visible, long-running issue for a business-critical deployment.",
    "Reported by a well-known enterprise customer; considered release-blocking "
    "and revenue-impacting.",
]


def features(text: str, **kwargs):  # noqa: ANN003, ANN201
    return feat.extract(text, **kwargs)


class TestAdjectivesAreNotSubjects:
    def test_common_modifiers_are_never_subjects(self):
        extracted = features(" ".join(MODIFIERS))
        assert extracted.subject_strong == set()
        assert extracted.subject_weak == set(), f"leaked subjects: {extracted.subject_weak}"

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
        assert "web-search-deepseek" not in features(text).subject_weak
        with_corpus = features(text, known_modules=frozenset({"web-search-deepseek"}))
        assert "web-search-deepseek" in with_corpus.subject_weak


class TestModifierInvariance:
    """Padding a correct report with business adjectives must change nothing."""

    def _verdict(self, text: str):  # noqa: ANN202
        return evaluate(features(text), features(LOADER_THREAD))

    def test_baseline_matches(self):
        assert self._verdict(CORRECT_REPORT).accepted

    def test_each_modifier_block_leaves_the_decision_unchanged(self):
        baseline = self._verdict(CORRECT_REPORT)
        for modifier in MODIFIERS:
            for padded in (f"{modifier} {CORRECT_REPORT}", f"{CORRECT_REPORT} {modifier}"):
                verdict = self._verdict(padded)
                assert verdict.accepted == baseline.accepted, modifier
                assert verdict.rule == baseline.rule, modifier
                assert verdict.rejection == baseline.rejection, modifier

    def test_all_modifiers_at_once_leave_the_decision_unchanged(self):
        baseline = self._verdict(CORRECT_REPORT)
        padded = " ".join(MODIFIERS) + " " + CORRECT_REPORT
        verdict = self._verdict(padded)
        assert verdict.accepted == baseline.accepted
        assert verdict.rule == baseline.rule
        assert verdict.shared["subject_strong"] == baseline.shared["subject_strong"]

    def test_modifiers_do_not_rescue_an_unrelated_report(self):
        """Invariance cuts both ways: padding must not create a match either."""
        unrelated = "Redis connection lost: READONLY You can't write against a read only replica"
        assert not self._verdict(unrelated).accepted
        padded = " ".join(MODIFIERS) + " " + unrelated
        assert not self._verdict(padded).accepted


class TestStrongConflictWins:
    """An unrelated scoped package plus shared common words must still be refused."""

    def test_foreign_package_with_shared_vocabulary_is_rejected(self):
        query = features(
            "@acme/theme-kit fails on Windows: the client boot graph has zero entries and zero "
            "batches, client-modules never preloads, and we see TypeError: e.indexOf is not a "
            "function during startup"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"

    def test_a_shared_weak_module_cannot_offset_a_package_conflict(self):
        """Both mention `client-modules`; the packages still disagree."""
        query = features(
            "@acme/design-system client-modules integration: HTML did not preload the bundle, "
            "__DSH_BOOT__ style manifest is empty, TypeError: e.indexOf is not a function"
        )
        candidate = features(LOADER_THREAD)
        assert "client-modules" in query.subject_weak & candidate.subject_weak
        verdict = evaluate(query, candidate)
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"
        assert "packages or source paths" in verdict.reasons[0]

    def test_a_foreign_path_with_shared_symbols_is_rejected(self):
        query = features(
            "apps/reporting/src/formatter.ts throws TypeError: e.indexOf is not a function and "
            "the boot graph has no entries"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_subject"

    def test_the_real_subject_still_matches(self):
        verdict = evaluate(features(CORRECT_REPORT), features(LOADER_THREAD))
        assert verdict.accepted
        assert verdict.shared["subject_strong"]
