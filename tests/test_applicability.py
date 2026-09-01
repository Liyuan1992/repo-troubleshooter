"""Applicability gate tests: unknown is not false, and only explicit bounds contradict."""

from repo_troubleshooter.versions.applicability import (
    Applicability,
    RuntimeConstraint,
    evaluate,
    extract_constraints,
)

RELEASE_NOTE = (
    "* 修复 Node.js 24.0–24.11.1 上启动可能失败且 HMR 失效的问题 @imccyu\n"
    "* Fix startup failures and broken HMR on Node.js 24.0-24.11.1 by @imccyu"
)


class TestConstraintExtraction:
    def test_reads_a_runtime_range_from_a_release_note(self):
        constraints = extract_constraints(
            RELEASE_NOTE, source="explicit", evidence_id="ev:release:x"
        )
        ranges = [c for c in constraints.runtimes if c.min_inclusive and c.max_inclusive]
        assert ranges, "expected a runtime range"
        assert ranges[0].runtime == "node"
        assert ranges[0].min_inclusive == "24.0"
        assert ranges[0].max_inclusive == "24.11.1"

    def test_reads_operating_systems(self):
        constraints = extract_constraints("Reproduced on Windows and on WSL")
        assert constraints.operating_systems == {"windows", "linux"}

    def test_no_constraints_from_unrelated_prose(self):
        constraints = extract_constraints("the button is the wrong colour")
        assert not constraints.runtimes
        assert not constraints.operating_systems


class TestRangeCoverage:
    def test_inside_outside_and_edges(self):
        rng = RuntimeConstraint("node", "24.0", "24.11.1", source="explicit")
        assert rng.covers("24.5.0") is True
        assert rng.covers("24.11.1") is True
        assert rng.covers("22.19.0") is False
        assert rng.covers("25.0.0") is False

    def test_unparseable_runtime_is_unknown(self):
        rng = RuntimeConstraint("node", "24.0", "24.11.1")
        assert rng.covers("nightly") is None
        assert rng.covers(None) is None


class TestVerdict:
    def _explicit(self):
        return extract_constraints(RELEASE_NOTE, source="explicit", evidence_id="ev:release:x")

    def test_matching_runtime_is_a_direct_match(self):
        verdict = evaluate(
            core_version="0.1.2-alpha.1",
            runtime_name="node",
            runtime_version="24.11.1",
            os_name="windows",
            constraints=self._explicit(),
        )
        assert verdict.status == Applicability.DIRECT_MATCH
        assert not verdict.conflicts

    def test_runtime_outside_an_explicit_range_is_a_hard_contradiction(self):
        verdict = evaluate(
            core_version="0.1.2-alpha.1",
            runtime_name="node",
            runtime_version="22.19.0",
            os_name="windows",
            constraints=self._explicit(),
        )
        assert verdict.status == Applicability.HARD_CONTRADICTION
        assert verdict.conflicts
        assert verdict.blocks_action

    def test_an_observed_mention_only_makes_it_possible(self):
        observed = extract_constraints(
            "I am on Node 24.11.1", source="observed", evidence_id="ev:d:1"
        )
        verdict = evaluate(
            core_version="0.1.2-alpha.1",
            runtime_name="node",
            runtime_version="22.19.0",
            os_name=None,
            constraints=observed,
        )
        assert verdict.status == Applicability.POSSIBLE_CONTRADICTION

    def test_unparseable_core_version_is_unresolved_not_contradicted(self):
        verdict = evaluate(
            core_version="nightly-2026-09-01",
            runtime_name="node",
            runtime_version="24.11.1",
            os_name="windows",
            constraints=self._explicit(),
        )
        assert verdict.status == Applicability.UNRESOLVED_VERSION
        assert verdict.blocks_action

    def test_conflicts_are_reported_once_even_from_bilingual_notes(self):
        verdict = evaluate(
            core_version="0.1.2-alpha.1",
            runtime_name="node",
            runtime_version="22.19.0",
            os_name="windows",
            constraints=self._explicit(),
        )
        assert len(verdict.conflicts) == len(set(verdict.conflicts))

    def test_no_constraints_means_compatible_unknown(self):
        verdict = evaluate(
            core_version="0.1.2-alpha.1",
            runtime_name="node",
            runtime_version="24.11.1",
            os_name="windows",
            constraints=extract_constraints("nothing relevant here"),
        )
        assert verdict.status == Applicability.COMPATIBLE_UNKNOWN
        assert not verdict.blocks_action
