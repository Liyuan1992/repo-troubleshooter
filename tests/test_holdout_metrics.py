"""The measurement's own arithmetic, tested without a corpus.

`evals/holdout.py` decides what this project may claim about itself, and it had
no test of any kind while it was being rewritten twice. These are deterministic
and touch no database: every case is constructed, so a change to how a rate is
computed shows up here rather than in a number someone quotes later.

What is checked is the part that has been wrong before: which denominator each
rate is taken over, what a rate means when the question was never put, and that
nothing machine-counted is named as an error.
"""

from __future__ import annotations

import pytest

from evals.holdout import HoldoutCase, HoldoutResult, _pooled, gate_failures


def case(
    number: int,
    *,
    matched: bool = False,
    opportunity: bool = False,
    proposes: bool = False,
) -> HoldoutCase:
    return HoldoutCase(
        object_id=number,
        number=number,
        title=f"report {number}",
        matched_url=f"https://example.invalid/{number}" if matched else None,
        matched_object_id=None,
        proposed_action="upgrade" if proposes else None,
        proposed_target="v2" if proposes else None,
        status="probable" if matched else "insufficient_evidence",
        stopped_at="accepted_same_incident" if matched else "retrieved_candidate",
        proposal_possible=opportunity,
    )


def result(cases: list[HoldoutCase], *, seed: int = 1) -> HoldoutResult:
    built = HoldoutResult(repo="owner/name", sample=len(cases), seed=seed, cases=cases)
    built.control = {"reaches_a_proposal": True}
    built.provenance = {
        "seed": seed,
        "requested_sample": len(cases),
        "eligible_reports": 499,
        "discussions_in_corpus": 550,
        "signature_rows": 15682,
        "data_as_of": "2026-09-01T00:00:00+00:00",
        "sampled_numbers": sorted(c.number for c in cases if c.number is not None),
    }
    return built


class TestDenominators:
    """Which population each rate is over. Reported wrongly once already."""

    def test_overall_is_over_every_report_sampled(self):
        payload = result(
            [
                case(1, matched=True, opportunity=True, proposes=True),
                case(2, matched=True, opportunity=True),
                *[case(n) for n in range(3, 11)],
            ]
        ).to_json()
        overall = payload["other_report_proposal_rate_overall"]
        assert overall["numerator"] == 1
        assert overall["denominator"] == 10, "the product rate is over the whole sample"
        assert overall["value"] == 0.1

    def test_conditional_is_over_the_opportunities_only(self):
        payload = result(
            [
                case(1, matched=True, opportunity=True, proposes=True),
                case(2, matched=True, opportunity=True),
                *[case(n) for n in range(3, 11)],
            ]
        ).to_json()
        conditional = payload["other_report_proposal_rate_given_opportunity"]
        assert conditional["numerator"] == 1
        assert conditional["denominator"] == 2, "the quality rate is over opportunities"
        assert conditional["value"] == 0.5

    def test_the_two_rates_differ_when_opportunities_are_rare(self):
        """The reason both are reported: they can be an order of magnitude apart."""
        payload = result(
            [
                case(1, matched=True, opportunity=True, proposes=True),
                *[case(n) for n in range(2, 101)],
            ]
        ).to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.01
        assert payload["other_report_proposal_rate_given_opportunity"]["value"] == 1.0


class TestWhenTheQuestionWasNeverPut:
    def test_conditional_is_null_without_opportunities(self):
        """Not zero. Zero would read as "nothing wrong was proposed"."""
        payload = result([case(n, matched=True) for n in range(1, 6)]).to_json()
        conditional = payload["other_report_proposal_rate_given_opportunity"]
        assert conditional["value"] is None
        assert conditional["denominator"] == 0

    def test_overall_is_still_zero_and_still_over_the_sample(self):
        payload = result([case(n, matched=True) for n in range(1, 6)]).to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.0
        assert payload["other_report_proposal_rate_overall"]["denominator"] == 5

    def test_an_empty_run_does_not_divide_by_zero(self):
        payload = result([]).to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.0
        assert payload["other_report_proposal_rate_given_opportunity"]["value"] is None


class TestNothingMachineCountedIsCalledFalse:
    def test_the_rates_are_named_for_what_they_count(self):
        payload = result([case(1)]).to_json()
        assert "other_report_proposal_rate_overall" in payload
        assert "other_report_proposal_rate_given_opportunity" in payload
        assert not [key for key in payload if key.startswith("false_")], (
            "a duplicate matched correctly is not an error, and the machine cannot "
            "tell the difference"
        )

    def test_the_report_says_so_in_words(self):
        payload = result([case(1)]).to_json()
        assert "not_an_error_rate" in payload
        assert "adjudicated" in payload["not_an_error_rate"]


class TestPooling:
    def test_pooled_counts_draws_and_distinct_reports_separately(self):
        """Seeds overlap. 300 draws over 246 distinct reports is not 300 reports."""
        first = result([case(1, matched=True, opportunity=True, proposes=True), case(2)], seed=11)
        second = result([case(2), case(3, matched=True, opportunity=True)], seed=22)
        pooled = _pooled([first, second])
        assert pooled["seeds"] == [11, 22]
        assert pooled["reports"] == 4, "draws"
        assert pooled["distinct_reports"] == 3, "the overlap is counted once"

    def test_pooled_rates_use_the_pooled_denominators(self):
        first = result([case(1, matched=True, opportunity=True, proposes=True), case(2)], seed=11)
        second = result([case(3, matched=True, opportunity=True), case(4)], seed=22)
        pooled = _pooled([first, second])
        assert pooled["other_report_proposal_count"] == 1
        assert pooled["proposal_opportunity_count"] == 2
        assert pooled["other_report_proposal_rate_overall"] == 0.25
        assert pooled["other_report_proposal_rate_given_opportunity"] == 0.5

    def test_pooled_conditional_is_null_without_opportunities(self):
        pooled = _pooled([result([case(1, matched=True)], seed=11)])
        assert pooled["other_report_proposal_rate_given_opportunity"] is None


class TestProvenance:
    """Two runs are only comparable when these match."""

    @pytest.mark.parametrize(
        "field",
        [
            "seed",
            "requested_sample",
            "eligible_reports",
            "discussions_in_corpus",
            "signature_rows",
            "data_as_of",
            "sampled_numbers",
        ],
    )
    def test_the_run_records_what_it_was_measured_against(self, field):
        payload = result([case(1), case(2)]).to_json()
        assert field in payload["provenance"]

    def test_the_sampled_reports_are_named(self):
        payload = result([case(7), case(3)]).to_json()
        assert payload["provenance"]["sampled_numbers"] == [3, 7]


class TestTheGate:
    def test_a_run_under_the_threshold_passes(self):
        payload = result(
            [
                case(1, matched=True, opportunity=True, proposes=True),
                *[case(n) for n in range(2, 101)],
            ]
        ).to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.01
        assert gate_failures(payload) == []

    def test_a_run_over_the_threshold_fails(self):
        cases = [case(n, matched=True, opportunity=True, proposes=True) for n in range(1, 7)]
        cases += [case(n) for n in range(7, 101)]
        payload = result(cases).to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.06
        assert gate_failures(payload), "0.06 exceeds the 0.05 threshold"

    def test_exactly_the_threshold_passes(self):
        cases = [case(n, matched=True, opportunity=True, proposes=True) for n in range(1, 6)]
        cases += [case(n) for n in range(6, 101)]
        payload = result(cases).to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.05
        assert gate_failures(payload) == []

    def test_a_failed_positive_control_fails_the_run_whatever_the_rate(self):
        """A low rate over a harness that cannot propose measures nothing."""
        built = result([case(n) for n in range(1, 101)])
        built.control = {"reaches_a_proposal": False}
        payload = built.to_json()
        assert payload["other_report_proposal_rate_overall"]["value"] == 0.0
        failures = gate_failures(payload)
        assert failures and "positive control" in failures[0]
