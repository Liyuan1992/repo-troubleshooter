"""The `accepted_same_incident` gate, in isolation.

These encode the three failures the independent black-box test found, so they
cannot come back:

* a candidate surfaced as a matched incident when the report stated a different
  root cause (CSP, YAML, DNS);
* identity granted on shared topic words alone;
* a natural-language rewrite of a real incident missed entirely.
"""

from __future__ import annotations

from repo_troubleshooter.fingerprint import features as feat
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


def features(text: str):  # noqa: ANN201
    return feat.extract(text)


class TestStatedCauseWins:
    def test_csp_report_is_not_the_loader_incident(self):
        query = features(
            "The browser refused to load @deepseek-ai/dsh-client-modules/client.js because it "
            "violates the following Content Security Policy directive: script-src 'self'. "
            "__DSH_BOOT__ is never populated."
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_root_cause"
        assert "content_security_policy" in verdict.conflicting_causes

    def test_yaml_parse_report_is_not_the_loader_incident(self):
        query = features(
            "dsh web will not start: cordis.yml duplicate key 'plugins' at line 12 while parsing "
            "a block mapping, so no client module is loaded"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_root_cause"

    def test_dns_failure_is_not_the_loader_incident(self):
        query = features(
            "npm ERR! code EAI_AGAIN getaddrinfo EAI_AGAIN registry.npmjs.org so dsh never starts"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "different_root_cause"

    def test_a_shared_cause_does_not_block_a_real_match(self):
        query = features(
            "Cannot find module '@deepseek-ai/dsh-client-modules/client.js'; resolveSync throws "
            "ERR_INVALID_ARG_TYPE and __DSH_BOOT__ stays empty"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.rejection != "different_root_cause"


class TestIdentityNeedsMoreThanTopic:
    def test_generic_words_are_not_identity(self):
        query = features(
            "startup failed due to a config error on windows with a plugin enabled; the server "
            "did not start and the log shows a preload warning"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted
        assert verdict.rejection == "insufficient_identity_evidence"

    def test_one_shared_component_is_not_identity(self):
        query = features("the client module for the web server did not load")
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted

    def test_environment_alone_proves_nothing(self):
        query = features("Something is broken on Windows with Node 24.11.1")
        verdict = evaluate(query, features(LOADER_THREAD))
        assert not verdict.accepted


class TestSubjectDisagreement:
    """Found by a surprise holdout: a shared error class is not a shared incident."""

    NPM_THREAD = (
        "`npx @deepseek-ai/dsh` fails with a JavaScript heap OOM during npm dependency "
        "resolution. npm ERR! ERESOLVE unable to resolve dependency tree while installing "
        "@deepseek-ai/dsh."
    )

    def test_same_error_code_on_a_different_package_is_not_the_same_incident(self):
        query = features(
            "npm ERR! ERESOLVE could not resolve peer dependency react@^19 for @acme/design-system"
        )
        verdict = evaluate(query, features(self.NPM_THREAD))
        assert not verdict.accepted
        # The query blames nothing - `react` is a stated dependency and
        # `@acme/design-system` is only mentioned - so this is refused for lack
        # of identity evidence rather than by a package veto. Both are correct;
        # what matters is that a shared ERESOLVE does not make it a match.
        assert verdict.rejection in (
            "different_subject",
            "insufficient_identity_evidence",
            "unresolved_subject",
            "unestablished_subject",
            "unread_claim_about_a_named_package",
            "unbound_state_assertion",
        )

    def test_the_same_package_still_matches(self):
        query = features(
            "npm ERR! ERESOLVE unable to resolve dependency tree when installing @deepseek-ai/dsh"
        )
        verdict = evaluate(query, features(self.NPM_THREAD))
        assert verdict.rejection != "different_subject"

    def test_a_paraphrase_with_no_subjects_is_unaffected(self):
        """The veto needs a named subject on BOTH sides; plain prose has none."""
        query = features(
            "The Harness web page starts on Windows but the client boot graph has no entries or "
            "batches, and the browser never preloads the dsh client JavaScript module."
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.accepted


class TestParaphraseIsIdentity:
    def test_behaviour_profile_plus_component_accepts_a_rewrite(self):
        query = features(
            "The Harness web page starts on Windows but the client boot graph has no entries or "
            "batches, and the browser never preloads the dsh client JavaScript module."
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.accepted
        assert verdict.rule == "behaviour_profile_plus_component"
        assert len(verdict.shared["behavior"]) >= 3

    def test_exact_paste_accepts_on_stronger_evidence(self):
        query = features(
            "__DSH_BOOT__ has zero entries; HTML did not preload "
            "@deepseek-ai/dsh-client-modules/client.js; TypeError: e.indexOf is not a function"
        )
        verdict = evaluate(query, features(LOADER_THREAD))
        assert verdict.accepted
        # With subjects typed, an exact paste matches on the package it names -
        # a stronger reason than the symbol overlap it used to rely on.
        assert verdict.rule in (
            "primary_package_plus_second_class",
            "source_path_plus_second_class",
            "error_type_plus_second_class",
            "two_independent_symbols",
        )
        assert verdict.score > 5


class TestFeatureExtraction:
    def test_behaviour_survives_rewording(self):
        exact = features("__DSH_BOOT__ has zero entries and zero batches")
        loose = features("the boot graph has no entries or batches")
        assert exact.behavior & loose.behavior

    def test_causes_are_detected_generically(self):
        assert "port_binding" in features("Error: listen EADDRINUSE: address already in use").causes
        assert "disk_space" in features("ENOSPC: no space left on device").causes
        assert "out_of_memory" in features("JavaScript heap out of memory").causes

    def test_no_cause_is_claimed_without_a_signal(self):
        assert features("the page is blank after startup").causes == set()
