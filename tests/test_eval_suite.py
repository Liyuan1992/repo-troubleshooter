"""Runs the frozen evaluation suite as a test.

Incidents, paraphrases, negative controls and version/runtime perturbations all
go through the same engine the CLI uses. Failures are reported per case so the
message names exactly which expectation broke.

Cases marked `known_gap` are measured limits, not passes: they are reported
separately and never counted toward Correct Action@1.
"""

from __future__ import annotations

import pytest
import yaml

from evals.runner import (
    CASES_DIR,
    compute_metrics,
    run_all,
    run_incidents,
    run_negatives,
    run_paraphrases,
    run_perturbations,
)

pytestmark = [pytest.mark.db, pytest.mark.live]


def _spec(name: str) -> dict:
    return yaml.safe_load((CASES_DIR / name).read_text(encoding="utf-8"))


def _assert_all_passed(results) -> None:  # noqa: ANN001
    failures = [
        f"{r.case_id}: {'; '.join(r.failures)}" for r in results if not r.passed and not r.known_gap
    ]
    assert not failures, "\n".join(failures)


class TestSuiteSize:
    def test_incident_coverage(self):
        spec = _spec("incidents.yaml")
        subsystems = {case["subsystem"] for case in spec["cases"]}
        assert len(spec["cases"]) >= 8, "the incident set is the development set, not a demo"
        assert len(subsystems) >= 5, f"incidents come from too few subsystems: {subsystems}"

    def test_negative_coverage(self):
        spec = _spec("negatives.yaml")
        domains = {case["domain"] for case in spec["cases"]}
        assert len(spec["cases"]) >= 20
        # The domains that produced false candidate matches in the independent test.
        assert any("near-miss" in d for d in domains)
        assert any("distractor" in d for d in domains)
        assert {"database", "authentication", "networking"} <= {d.split(" / ")[0] for d in domains}

    def test_perturbation_coverage(self):
        spec = _spec("perturbations.yaml")
        assert len(spec["cases"]) >= 12
        ids = {case["id"] for case in spec["cases"]}
        # older / first-containing / newer / missing / unparseable / runtime / os
        for required in (
            "pert-older-release",
            "pert-first-containing",
            "pert-newer-release",
            "pert-missing-version",
            "pert-unparseable-version",
            "pert-runtime-below-range",
            "pert-other-os",
        ):
            assert required in ids, f"missing perturbation: {required}"


class TestIncidents:
    def test_all_incident_cases_pass(self, session, synced_repo):
        _assert_all_passed(run_incidents(session))


class TestParaphrases:
    def test_paraphrase_recall_and_its_limits(self, session, synced_repo):
        results = run_paraphrases(session)
        _assert_all_passed(results)

    def test_the_reported_paraphrase_is_recalled(self, session, synced_repo):
        """The exact rewrite the independent test found missing."""
        results = {r.case_id: r for r in run_paraphrases(session)}
        recalled = results["para-boot-graph-plain-english"]
        assert recalled.passed, recalled.failures
        assert recalled.observed["action"] == "upgrade"
        assert recalled.observed["matched"] is True


class TestNegatives:
    def test_all_negative_controls_abstain(self, session, synced_repo):
        _assert_all_passed(run_negatives(session))

    def test_near_misses_do_not_surface_an_incident(self, session, synced_repo):
        """CSP, YAML and DNS look like the loader incident but are not it."""
        results = {r.case_id: r for r in run_negatives(session)}
        for case_id in (
            "neg-csp-blocks-client-js",
            "neg-yaml-duplicate-key",
            "neg-dns-registry-failure",
        ):
            observed = results[case_id].observed
            assert observed["matched"] is False, f"{case_id} exposed {observed['matched_title']!r}"
            assert observed["action"] in ("abstain", "collect_more_info")


class TestPerturbations:
    def test_all_perturbations_pass(self, session, synced_repo):
        _assert_all_passed(run_perturbations(session))

    def test_the_verdict_actually_changes_with_the_version(self, session, synced_repo):
        results = {r.case_id: r for r in run_perturbations(session)}
        older = results["pert-immediately-older"].observed
        contained = results["pert-first-containing"].observed
        assert older["action"] == "upgrade"
        assert contained["action"] != "upgrade"
        assert older["target"] and not contained["target"]


@pytest.fixture
def metrics(session, synced_repo):  # noqa: ANN001, ANN201
    """One full suite run backing every hard-gate assertion.

    Function-scoped on purpose: it uses the same session fixture the rest of the
    suite uses, and one extra run is cheaper than a scope mismatch.
    """
    report = run_all(session)
    return compute_metrics(session, report, _spec("incidents.yaml")["repo"])


class TestHardGates:
    """The gates from the mainline directive. Any non-zero here is a stop-ship."""

    def test_no_unsafe_action_on_negatives(self, metrics):
        assert metrics["unsafe_action_rate_on_negatives"]["value"] == 0.0

    def test_no_false_incident_on_negatives(self, metrics):
        assert metrics["negative_false_incident_rate"]["value"] == 0.0

    def test_no_unsafe_action_on_environment_contradictions(self, metrics):
        assert metrics["unsafe_action_rate_on_contradictions"]["value"] in (0.0, None)

    def test_every_cited_evidence_id_resolves(self, metrics):
        assert metrics["citation_validity"]["unresolvable"] == []
        assert metrics["citation_validity"]["value"] == 1.0

    def test_version_perturbations_never_recommend_a_stale_upgrade(self, metrics):
        assert metrics["version_verdict_accuracy"]["value"] == 1.0

    def test_no_future_leakage(self, metrics):
        assert metrics["future_leakage_violations"]["value"] == 0
