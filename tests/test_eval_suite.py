"""Runs the frozen evaluation suite as a test.

Incidents, negative controls and version/runtime perturbations all go through
the same engine the CLI uses. Failures are reported per case so the message
names exactly which expectation broke.
"""

from __future__ import annotations

import pytest

from evals.runner import run_all, run_incidents, run_negatives, run_perturbations

pytestmark = [pytest.mark.db, pytest.mark.live]


@pytest.fixture(scope="module")
def report(request):  # noqa: ANN001, ANN201
    session = request.getfixturevalue("session")
    request.getfixturevalue("synced_repo")
    return run_all(session)


def _assert_all_passed(results) -> None:  # noqa: ANN001
    failures = [f"{r.case_id}: {'; '.join(r.failures)}" for r in results if not r.passed]
    assert not failures, "\n".join(failures)


class TestIncidents:
    def test_all_incident_cases_pass(self, session, synced_repo):
        results = run_incidents(session)
        _assert_all_passed(results)

    def test_covers_more_than_one_subsystem(self):
        import yaml

        from evals.runner import CASES_DIR

        spec = yaml.safe_load((CASES_DIR / "incidents.yaml").read_text(encoding="utf-8"))
        subsystems = {case["subsystem"] for case in spec["cases"]}
        assert len(spec["cases"]) >= 5
        assert len(subsystems) >= 3, f"incidents come from too few subsystems: {subsystems}"


class TestNegatives:
    def test_all_negative_controls_abstain(self, session, synced_repo):
        results = run_negatives(session)
        _assert_all_passed(results)

    def test_suite_is_large_enough_to_be_meaningful(self):
        import yaml

        from evals.runner import CASES_DIR

        spec = yaml.safe_load((CASES_DIR / "negatives.yaml").read_text(encoding="utf-8"))
        assert len(spec["cases"]) >= 10
        domains = {case["domain"] for case in spec["cases"]}
        assert {"database", "authentication", "networking"} <= domains


class TestPerturbations:
    def test_all_perturbations_pass(self, session, synced_repo):
        results = run_perturbations(session)
        _assert_all_passed(results)

    def test_the_verdict_actually_changes_with_the_version(self, session, synced_repo):
        results = {r.case_id: r for r in run_perturbations(session)}
        older = results["pert-immediately-older"].observed
        contained = results["pert-first-containing"].observed
        assert older["action"] == "upgrade"
        assert contained["action"] != "upgrade"
        assert older["target"] and not contained["target"]
