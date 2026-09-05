"""Structured anchors are one-way candidate constraints, never authority."""

from __future__ import annotations

from repo_troubleshooter.diagnosis.contract import StructuredAnchor
from repo_troubleshooter.fingerprint.features import SymptomFeatures
from repo_troubleshooter.retrieval.anchors import labels, mismatches


def test_anchor_normalises_an_exact_feature_value() -> None:
    anchor = StructuredAnchor(kind="structural", value="  __DSH_BOOT__  ")
    candidate = SymptomFeatures(structural={"__dsh_boot__"})

    assert mismatches((anchor,), candidate) == []
    assert labels((anchor,)) == ["structural:__dsh_boot__"]


def test_anchor_rejects_a_candidate_that_lacks_the_user_fact() -> None:
    anchor = StructuredAnchor(kind="subject_path", value="packages/loader/src/internal.ts")
    candidate = SymptomFeatures(subject_paths={"packages/client/src/index.ts"})

    assert mismatches((anchor,), candidate) == [anchor]


def test_quoted_candidate_evidence_cannot_satisfy_identity_anchor() -> None:
    anchor = StructuredAnchor(kind="error", value="err_invalid_arg_type")
    candidate = SymptomFeatures(
        error={"err_invalid_arg_type"},
        unquoted=SymptomFeatures(),
    )

    assert mismatches((anchor,), candidate) == [anchor]
