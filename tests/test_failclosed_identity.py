"""Fail-closed identity: an undetermined package is a reason to refuse.

Every earlier round of this gate leaked the same way. A phrasing the cue
vocabulary did not recognise turned the package into a neutral mention, a
neutral mention could not refuse anything, and a familiar stack path was enough
to match a real incident and recommend a version change. Adding the missing
phrase fixed that phrasing and left the next one open.

The invariant here does not depend on recognising the phrasing at all:

* a package whose role cannot be determined is `unresolved_subject`, never
  "harmless";
* if the query carries an unresolved package the candidate never names, and the
  only links between them are a dependency, a path or a symbol, the match is
  refused - because none of those say *what* failed.

So the eleven phrasings below are a demonstration, not the mechanism. A twelfth
one is expected to exist, and is expected to be refused for the same reason.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from repo_troubleshooter.fingerprint import features as feat
from repo_troubleshooter.fingerprint.subjects import (
    PackageRelation,
    PackageRole,
    PackageState,
    classify,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BIN = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
EXE = ".exe" if os.name == "nt" else ""
REPO = "deepseek-ai/deepseek-harness"
UNSAFE = {"upgrade", "downgrade", "migrate", "config_change", "workaround"}

# The symptom of the real incident, so every case below is maximally tempting:
# same behaviour, same stack path, same symbol, same exception.
BOOT_SYMPTOM = (
    "The client boot graph has no entries or batches, nothing is preloaded, and "
    "packages/loader/src/internal.ts throws TypeError: e.indexOf is not a function on Windows"
)

# An external package, phrased eleven different ways. None of them is DSH's.
PHRASINGS: list[tuple[str, str]] = [
    ("not-working", "@nebula/theme-engine is not working"),
    ("health-bleed", "@deepseek-ai/dsh is healthy; @nebula/theme-engine crashes"),
    ("not-up-to-date", "peer dependency @nebula/theme-engine is not up to date"),
    ("starts-but-crashes", "@nebula/theme-engine starts but crashes"),
    ("loads-then-crashes", "@nebula/theme-engine loads, then crashes"),
    ("label-colon", "@nebula/theme-engine: crashes"),
    ("relative-clause", "@nebula/theme-engine, which crashes"),
    ("stopped-working", "@nebula/theme-engine stopped working"),
    ("wont-start", "@nebula/theme-engine won't start"),
    ("unknown-phrasing", "@nebula/theme-engine went sideways on us"),
    ("contradictory", "@nebula/theme-engine is healthy but crashes"),
    # --- authorization invariants, added after review found them ------------
    (
        "exculpated-dsh-with-its-own-path",
        "@deepseek-ai/dsh-client-modules is healthy and does not fail. "
        "The host process crashes separately",
    ),
    (
        "failed-to-install-external",
        "failed to install @nebula/theme-engine; we depend on "
        "@deepseek-ai/dsh-client-modules, which is healthy",
    ),
    ("could-not-import-external", "could not import @nebula/theme-engine"),
    ("cannot-require-external", "cannot require @nebula/theme-engine"),
    (
        "contradictory-same-dsh-package",
        "@deepseek-ai/dsh-client-modules is healthy but crashes",
    ),
    ("bare-name-at-version", "nebula-theme@1.2.3 went sideways"),
    ("healthy-dsh-but-it-crashes", "@deepseek-ai/dsh-client-modules is healthy but it crashes"),
    # --- fact aggregation: relation and state are independent ---------------
    (
        "healthy-dsh-dependency-plus-its-path",
        "We depend on @deepseek-ai/dsh-client-modules, which is healthy. "
        "The host process crashes separately",
    ),
    (
        "same-package-across-sentences",
        "@deepseek-ai/dsh-client-modules is healthy. Later @deepseek-ai/dsh-client-modules crashes",
    ),
    (
        "same-package-repeated-mentions",
        "@deepseek-ai/dsh-client-modules is healthy and @deepseek-ai/dsh-client-modules "
        "crashes on startup",
    ),
    ("external-dependency-then-it-crashes", "We import @nebula/theme-engine, but it crashes"),
    ("bare-version-then-it-crashes", "installed nebula-theme@1.2.3, then it hangs"),
    ("dependency-then-the-package-fails", "We use @nebula/theme-engine; the package fails"),
    # --- claims that live in the next sentence ------------------------------
    ("bang-then-it-crashes", "We import @nebula/theme-engine! It crashes on startup"),
    ("newline-then-it-crashes", "We import @nebula/theme-engine\nIt crashes on startup"),
    ("bare-version-bang-it-crashes", "installed nebula-theme@1.2.3! It crashes"),
    (
        "dsh-bang-it-is-healthy",
        "We import @deepseek-ai/dsh-client-modules! It is healthy",
    ),
    (
        "dsh-as-a-healthy-dependency",
        "@deepseek-ai/dsh-client-modules as a healthy dependency",
    ),
    (
        "this-package-is-healthy",
        "We depend on @deepseek-ai/dsh-client-modules. this package is healthy",
    ),
    (
        "this-package-does-not-crash",
        "We depend on @deepseek-ai/dsh-client-modules. this package does not crash",
    ),
    # --- a shared primary does not license an unread claim ------------------
    ("crashes-then-operational", "@deepseek-ai/dsh-client-modules crashes! It is operational."),
    ("fails-then-no-issues", "@deepseek-ai/dsh-client-modules fails! It has no issues."),
    (
        "hangs-then-passed-checks",
        "@deepseek-ai/dsh-client-modules hangs! It passed every health check.",
    ),
    # --- brackets are not code, and must not delete a health statement -------
    (
        "parenthesised-health",
        "We import @deepseek-ai/dsh-client-modules (runtime dependency)! It is healthy (verified)!",
    ),
    (
        "bracketed-health",
        "We import @deepseek-ai/dsh-client-modules [runtime dependency]! "
        "This package does not crash [checked]!",
    ),
    (
        "ambiguous-antecedent",
        "We use @nebula/theme-engine and @acme/other-thing. It crashes",
    ),
    # --- spelling the package out is stronger than pointing at it -----------
    (
        "named-then-operational",
        "@deepseek-ai/dsh-client-modules crashes! @deepseek-ai/dsh-client-modules is operational.",
    ),
    ("said-package-operational", "Said package is operational."),
    ("the-same-package-passed", "The same package passed every health check."),
    ("this-exact-module-no-issues", "This exact module has no issues."),
    # --- a relation verb in a subordinate clause is not the predicate -------
    (
        "no-issues-when-using",
        "@deepseek-ai/dsh-client-modules fails! It has no issues when using plugins.",
    ),
    (
        "passed-after-requiring",
        "@deepseek-ai/dsh-client-modules hangs! "
        "It passed every health check after requiring dependencies.",
    ),
    # --- an inline span is a quotation, and can carry a claim ---------------
    ("inline-span-health", "Diagnostic summary: `It is operational`"),
    # --- a health word we know is not weaker than one we do not -------------
    (
        "said-package-is-healthy",
        "@deepseek-ai/dsh-client-modules crashes! Said package is healthy.",
    ),
    (
        "said-package-works-fine",
        "@deepseek-ai/dsh-client-modules crashes! Said package works fine.",
    ),
    # --- a negated verb we cannot read is a claim, not silence --------------
    ("did-not-malfunction", "@deepseek-ai/dsh-client-modules crashes! It did not malfunction."),
    ("wasnt-defective", "@deepseek-ai/dsh-client-modules crashes! It wasn't defective."),
    # --- a relation verb inside a relative clause is not the predicate ------
    (
        "reduced-relative-clause",
        "@deepseek-ai/dsh-client-modules crashes! "
        "The package using our fallback shim remains operational.",
    ),
    (
        "trailing-relation-verb",
        "@deepseek-ai/dsh-client-modules crashes! It is operational using plugins.",
    ),
    # --- quotation, and things that only look like code ---------------------
    ("quoted-claim-in-prose", "Diagnostic summary says `It is operational`."),
    ("fronted-adverbial", "At startup it is operational."),
    # --- however many modifiers stand in front of the head noun -------------
    (
        "many-modifiers",
        "@deepseek-ai/dsh-client-modules crashes! "
        "This carefully audited bundled runtime component remains operational.",
    ),
    # --- a reduced relative clause is not a wiring statement ----------------
    (
        "reduced-passed",
        "@deepseek-ai/dsh-client-modules crashes! "
        "The package using our fallback passed every health check.",
    ),
    (
        "reduced-survived",
        "@deepseek-ai/dsh-client-modules crashes! "
        "The component requiring our fallback survived all validation.",
    ),
    (
        "reduced-behaved",
        "@deepseek-ai/dsh-client-modules crashes! "
        "The module importing the compatibility layer behaved normally.",
    ),
    (
        "reduced-ran",
        "@deepseek-ai/dsh-client-modules crashes! The library using our shim ran without issues.",
    ),
    (
        "explicit-relative",
        "@deepseek-ai/dsh-client-modules crashes! "
        "The package that uses our fallback passed every health check.",
    ),
]

CASES = [pytest.param(f"{clause}. {BOOT_SYMPTOM}", id=case_id) for case_id, clause in PHRASINGS]


# The package the user is running, stated as a field. Free text finds
# candidates; stating this is what authorises advice, so every case here runs
# with it - which makes the negatives *harder*: they must still refuse even
# though the user has named the package the incident is about.
STATED_PACKAGE = "@deepseek-ai/dsh-client-modules"


def cli_diagnose(
    error: str,
    *,
    version: str = "0.1.2-alpha.1",
    debug: bool = False,
    packages: tuple[str, ...] = (STATED_PACKAGE,),
) -> dict:
    executable = BIN / f"repo-troubleshooter{EXE}"
    argv = (
        [str(executable)]
        if executable.exists()
        else [sys.executable, "-m", "repo_troubleshooter.cli.main"]
    )
    # `--no-persist` because this suite reads the tool's real database: a
    # diagnosis that records an incident or refreshes the containment cache
    # changes the data every later test - and every measurement in the status
    # document - is taken from.
    argv += [
        "diagnose",
        "--repo",
        REPO,
        "--json",
        "--no-persist",
        "--error",
        error,
        "--version",
        version,
    ]
    for name in packages:
        argv += ["--package", name]
    if debug:
        argv.append("--debug")
    proc = subprocess.run(  # noqa: S603
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def stdio_mcp_diagnose(
    error: str,
    *,
    version: str = "0.1.2-alpha.1",
    packages: tuple[str, ...] = (STATED_PACKAGE,),
) -> dict[str, Any]:
    """A freshly launched `repo-troubleshooter-mcp` process, spoken to over stdio."""
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    executable = BIN / f"repo-troubleshooter-mcp{EXE}"
    if not executable.exists():
        pytest.skip("repo-troubleshooter-mcp console script is not installed")

    params = StdioServerParameters(
        command=str(executable), args=[], env=dict(os.environ), cwd=str(PROJECT_ROOT)
    )

    async def run() -> dict[str, Any]:
        async with Client(params) as client:
            result = await client.call_tool(
                "diagnose",
                {
                    "repo": REPO,
                    "error": error,
                    "core_version": version,
                    "packages": list(packages),
                },
            )
            payload = result.structured_content
            if payload is None:
                blocks = [b for b in result.content if getattr(b, "type", None) == "text"]
                payload = json.loads(blocks[0].text)
            return payload

    payload = asyncio.run(run())
    assert payload["ok"] is True, payload
    return payload["result"]


class TestAuthorizationInvariants:
    """Roles alone are not enough: some reports may not authorise any action."""

    def test_a_report_that_clears_every_package_it_names(self):
        """Even the incident's own source path cannot rescue it."""
        features = feat.extract(
            "@deepseek-ai/dsh-client-modules is healthy and does not fail. The host process "
            "crashes separately. " + BOOT_SYMPTOM
        )
        assert features.subject_confirmed_non_primary == {"@deepseek-ai/dsh-client-modules"}
        assert not features.subject_packages
        assert not features.subject_unresolved

    @pytest.mark.parametrize(
        "clause",
        [
            "failed to install @nebula/theme-engine",
            "could not import @nebula/theme-engine",
            "cannot require @nebula/theme-engine",
        ],
    )
    def test_a_failure_before_the_mention_outranks_the_dependency_cue(self, clause):
        mention = classify(clause).package_mentions[0]
        assert mention.role in (PackageRole.PRIMARY, PackageRole.UNRESOLVED), mention.cue
        assert mention.role is not PackageRole.DEPENDENCY

    def test_contradiction_is_its_own_role(self):
        mention = classify(
            "@deepseek-ai/dsh-client-modules is healthy but crashes"
        ).package_mentions[0]
        assert mention.role is PackageRole.CONFLICTED

    def test_bare_name_at_version_is_not_automatically_a_dependency(self):
        unknown = classify("nebula-theme@1.2.3 went sideways").package_mentions[0]
        assert unknown.role is PackageRole.UNRESOLVED
        declared = classify("we installed nebula-theme@1.2.3").package_mentions[0]
        assert declared.role is PackageRole.DEPENDENCY


class TestFactsAreAggregated:
    """Relation and state are independent facts, merged per package name."""

    def test_a_healthy_dependency_keeps_both_facts(self):
        subjects = classify("We depend on @deepseek-ai/dsh-client-modules, which is healthy")
        mention = subjects.package_mentions[0]
        assert mention.relation is PackageRelation.DEPENDENCY
        assert mention.state is PackageState.HEALTHY
        # Both sets, because both facts are true.
        assert "@deepseek-ai/dsh-client-modules" in subjects.dependencies
        assert "@deepseek-ai/dsh-client-modules" in subjects.healthy_packages

    def test_a_failing_dependency_keeps_both_facts(self):
        subjects = classify("We import @nebula/theme-engine, but it crashes")
        mention = subjects.package_mentions[0]
        assert mention.relation is PackageRelation.DEPENDENCY
        assert mention.state is PackageState.FAILING
        assert "@nebula/theme-engine" in subjects.primary_packages

    @pytest.mark.parametrize(
        "text",
        [
            "@a/x is healthy. Later @a/x crashes.",
            "@a/x is healthy and @a/x crashes on startup",
            "@a/x is healthy. The build is fine. @a/x crashes.",
            "@a/x/inner.js is healthy. @a/x crashes.",
        ],
    )
    def test_two_statements_about_one_package_are_a_contradiction(self, text):
        """Across sentences, repeated mentions and sub-path aliases alike."""
        subjects = classify(text)
        assert subjects.conflicted_packages, text
        assert not subjects.primary_packages, text

    @pytest.mark.parametrize(
        "text",
        [
            "We import @nebula/theme-engine, but it crashes",
            "We use @nebula/theme-engine; the package fails",
            "installed nebula-theme@1.2.3, then it hangs",
        ],
    )
    def test_anaphora_after_a_dependency_is_not_ignored(self, text):
        """`it crashes` must not leave the package filed as a safe dependency."""
        mention = classify(text).package_mentions[0]
        assert mention.role in (PackageRole.PRIMARY, PackageRole.UNRESOLVED), mention.cue

    def test_an_unattributable_failure_makes_a_dependency_ambiguous(self):
        """A failure nearby with a subject we cannot resolve is not harmless."""
        subjects = classify("We import @nebula/theme-engine, but the whole box crashed")
        mention = subjects.package_mentions[0]
        assert mention.role is not PackageRole.DEPENDENCY or mention.ambiguous

    def test_a_different_subject_is_still_not_attributed(self):
        """The guard must not swallow every nearby verb."""
        subjects = classify("@a/x is healthy but the server crashes")
        assert "@a/x" in subjects.healthy_packages
        assert not subjects.primary_packages


class TestClaimsAreBound:
    """Every condition claim binds to a package, to another subject, or to nothing."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("We import @nebula/theme-engine! It crashes", PackageState.FAILING),
            ("We import @nebula/theme-engine\nIt crashes", PackageState.FAILING),
            ("installed nebula-theme@1.2.3! It crashes", PackageState.FAILING),
            ("We import @scope/lib! It is healthy", PackageState.HEALTHY),
            ("We depend on @scope/lib. this package is healthy", PackageState.HEALTHY),
            ("We depend on @scope/lib. this package does not crash", PackageState.HEALTHY),
            ("@scope/lib as a healthy dependency", PackageState.HEALTHY),
        ],
    )
    def test_a_claim_in_the_next_sentence_binds_to_its_package(self, text, expected):
        """Sentence ends, exclamation marks and newlines do not break the binding."""
        mention = classify(text).package_mentions[0]
        assert mention.state is expected, mention.cue

    def test_an_ambiguous_antecedent_is_never_guessed(self):
        subjects = classify("We use @a/one and @b/two. It crashes")
        assert subjects.unresolved_assertions, "expected an unbound claim"
        assert not subjects.primary_packages

    def test_a_claim_about_an_unrecognised_subject_does_not_blame_the_package(self):
        """`the server crashes` names something we cannot see.

        We refuse to attribute it to the only package in sight - that would
        blame something the report never blamed - and equally refuse to wave it
        through as somebody else's problem. It dangles, and the gate decides
        what that costs.
        """
        subjects = classify("We use @a/one. The server crashes")
        assert not subjects.primary_packages
        assert not subjects.conflicted_packages
        assert subjects.unresolved_assertions

    def test_a_trailing_period_is_not_part_of_the_package_name(self):
        subjects = classify("HTML did not preload @deepseek-ai/dsh-client-modules.")
        assert "@deepseek-ai/dsh-client-modules" in subjects.all_packages


class TestRolesAreFailClosed:
    def test_a_healthy_dependency_beside_a_blamed_package_keeps_both_roles(self):
        subjects = classify(
            "failed to install @nebula/theme-engine; we depend on "
            "@deepseek-ai/dsh-client-modules, which is healthy"
        )
        assert "@nebula/theme-engine" in subjects.primary_packages
        assert "@deepseek-ai/dsh-client-modules" in subjects.dependencies

    def test_an_unrecognised_phrasing_is_unresolved_not_harmless(self):
        mention = classify("@nebula/theme-engine went sideways on us").package_mentions[0]
        assert mention.role is PackageRole.UNRESOLVED

    def test_contradictory_predicates_are_conflicted(self):
        mention = classify("@nebula/theme-engine is healthy but crashes").package_mentions[0]
        assert mention.role is PackageRole.CONFLICTED

    def test_explicit_health_is_confirmed_not_merely_unresolved(self):
        mention = classify("@nebula/theme-engine does not crash").package_mentions[0]
        assert mention.role is PackageRole.CONFIRMED_NON_PRIMARY

    @pytest.mark.parametrize(
        ("clause", "expected"),
        [
            ("@a/x starts but crashes", PackageRole.PRIMARY),
            ("@a/x loads, then crashes", PackageRole.PRIMARY),
            ("@a/x: crashes", PackageRole.PRIMARY),
            ("@a/x, which crashes", PackageRole.PRIMARY),
            ("@a/x stopped working", PackageRole.PRIMARY),
            ("@a/x won't start", PackageRole.PRIMARY),
        ],
    )
    def test_coordinated_and_label_syntax(self, clause, expected):
        assert classify(clause).package_mentions[0].role is expected

    def test_a_coordinated_clause_with_a_new_subject_is_not_attributed(self):
        """`X is healthy but the server crashes` says nothing bad about X."""
        mention = classify("@a/x is healthy but the server crashes").package_mentions[0]
        assert mention.role is PackageRole.CONFIRMED_NON_PRIMARY

    # These deliberately *do* blame DSH in one sentence and clear it in the next.
    # The role is then correct; safety comes from the unread claim that follows,
    # which the end-to-end tests assert.
    SELF_CONTRADICTING = {
        "crashes-then-operational",
        "fails-then-no-issues",
        "hangs-then-passed-checks",
        "named-then-operational",
        "no-issues-when-using",
        "passed-after-requiring",
        "said-package-is-healthy",
        "said-package-works-fine",
        "did-not-malfunction",
        "wasnt-defective",
        "reduced-relative-clause",
        "trailing-relation-verb",
        "reduced-passed",
        "reduced-survived",
        "reduced-behaved",
        "reduced-ran",
        "explicit-relative",
        "many-modifiers",
    }

    def test_no_phrasing_leaves_an_actionable_dsh_subject(self):
        """Whatever the wording, the report must not end up blaming DSH.

        Except where it says so outright and then takes it back - those cases
        are held by the claim guard, not by the role.
        """
        for case_id, clause in PHRASINGS:
            if case_id in self.SELF_CONTRADICTING:
                continue
            features = feat.extract(f"{clause}. {BOOT_SYMPTOM}")
            assert "@deepseek-ai/dsh-client-modules" not in features.subject_packages, case_id


@pytest.mark.db
@pytest.mark.live
class TestElevenPhrasingsThroughBothSurfaces:
    """The required outcome for all eleven: no match, abstain, no target."""

    @pytest.mark.parametrize("error", CASES)
    def test_installed_cli(self, error):
        payload = cli_diagnose(error)
        assert payload["incident"]["matched"] is False, payload["incident"]["title"]
        assert payload["recommended_action"]["type"] == "abstain"
        assert payload["recommended_action"]["target"] is None

    @pytest.mark.parametrize("error", CASES)
    def test_fresh_stdio_mcp_process(self, error):
        payload = stdio_mcp_diagnose(error)
        assert payload["incident"]["matched"] is False, payload["incident"]["title"]
        assert payload["recommended_action"]["type"] == "abstain"
        assert payload["recommended_action"]["target"] is None

    @pytest.mark.parametrize("error", CASES)
    def test_the_two_surfaces_agree(self, error):
        cli = cli_diagnose(error)
        mcp = stdio_mcp_diagnose(error)
        assert cli["status"] == mcp["status"]
        assert cli["incident"]["matched"] == mcp["incident"]["matched"]
        assert cli["recommended_action"]["type"] == mcp["recommended_action"]["type"]
        assert cli["recommended_action"]["target"] == mcp["recommended_action"]["target"]
        assert cli["stages"]["stopped_at"] == mcp["stages"]["stopped_at"]

    def test_no_unsafe_action_anywhere_in_the_set(self):
        actions = {cli_diagnose(case.values[0])["recommended_action"]["type"] for case in CASES}
        assert not (actions & UNSAFE), actions

    def test_the_real_incident_still_matches(self):
        """The invariant must refuse the eleven without refusing everything."""
        real = (
            "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; client-modules "
            "reports HTML did not preload @deepseek-ai/dsh-client-modules/client.js, and the "
            "host throws TypeError: e.indexOf is not a function"
        )
        cli = cli_diagnose(real)
        mcp = stdio_mcp_diagnose(real)
        assert cli["incident"]["matched"] is True
        assert cli["recommended_action"]["type"] == "upgrade"
        assert cli["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"
        assert mcp["recommended_action"]["target"] == cli["recommended_action"]["target"]

    @pytest.mark.parametrize(
        "prefix",
        [
            "Our application imports @deepseek-ai/dsh-client-modules before startup. ",
            "@deepseek-ai/dsh-client-modules is one of our dependencies. ",
            "The project requires @deepseek-ai/dsh-client-modules. ",
        ],
    )
    def test_a_relation_statement_does_not_cost_the_positive(self, prefix):
        """`imports`/`requires`/`is a dependency` describe wiring, not condition.

        Reading them as claims we could not interpret made three fully evidenced
        reports abstain - a recall loss far wider than the two registered gaps.
        """
        real = (
            "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
            "client-modules reports HTML did not preload "
            "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
            "TypeError: e.indexOf is not a function"
        )
        payload = cli_diagnose(prefix + real)
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    @pytest.mark.parametrize(
        ("case_id", "clause"),
        [
            (
                "named-outright",
                "@deepseek-ai/dsh-client-modules crashes! "
                "@deepseek-ai/dsh-client-modules is operational.",
            ),
            ("said-package", "Said package is operational."),
            ("the-same-package", "The same package passed every health check."),
            ("this-exact-module", "This exact module has no issues."),
        ],
    )
    def test_strength_follows_the_source_not_the_pronoun_list(self, case_id, clause):
        """A clause that spells the package out is not weaker than `it is`.

        Deciding an unread claim's strength by matching its subject against a
        pronoun list left the strongest reference - the package name itself -
        classified as a guess, and five reports upgraded because of it.
        """
        features = feat.extract(f"{clause} {BOOT_SYMPTOM}")
        pointed = features.pointed_unread_assertions
        assert pointed, f"{case_id}: no pointed claim was recorded"
        assert all(a["source"] in {"explicit_package", "resolved_anaphor"} for a in pointed), (
            f"{case_id}: {[a['source'] for a in pointed]}"
        )
        payload = cli_diagnose(f"{clause} {BOOT_SYMPTOM}")
        assert payload["recommended_action"]["type"] not in UNSAFE, case_id

    @pytest.mark.parametrize(
        ("case_id", "clause"),
        [
            (
                "subordinate-using",
                "@deepseek-ai/dsh-client-modules fails! It has no issues when using plugins.",
            ),
            (
                "subordinate-requiring",
                "@deepseek-ai/dsh-client-modules hangs! "
                "It passed every health check after requiring dependencies.",
            ),
        ],
    )
    def test_a_subordinate_relation_verb_does_not_delete_the_claim(self, case_id, clause):
        """`using`/`requiring` after the predicate is wiring in a subordinate clause.

        Exempting the whole clause because a relation verb appeared anywhere in
        it let a trailing `when using plugins` delete `It has no issues`.
        """
        features = feat.extract(f"{clause} {BOOT_SYMPTOM}")
        assert features.pointed_unread_assertions, f"{case_id}: the claim was deleted"
        payload = cli_diagnose(f"{clause} {BOOT_SYMPTOM}")
        assert payload["recommended_action"]["type"] not in UNSAFE, case_id

    def test_a_quoted_claim_is_read_like_the_paths_around_it(self):
        """Code evidence counted towards identity while code hid the claim beside it.

        An inline span is a quotation, and what is quoted can be a sentence.
        Skipping it as code let the same region contribute paths and symbols
        towards an upgrade while its state claim was thrown away.
        """
        report = f"Diagnostic summary: `It is operational` {BOOT_SYMPTOM}"
        assert feat.extract(report).pointed_unread_assertions
        payload = cli_diagnose(report)
        assert payload["recommended_action"]["type"] not in UNSAFE

    def test_a_known_health_word_is_not_weaker_than_an_unknown_one(self):
        """`Said package is healthy` retracts the failure it follows.

        Strength was recorded only on claims whose predicate could not be read.
        A claim we *could* read went down the old path, matched the pronoun
        list, failed it, and became an unresolved health claim - which the gate
        drops as harmless. It is not harmless: it takes back the failure the
        sentence before it reported.
        """
        for clause in (
            "@deepseek-ai/dsh-client-modules crashes! Said package is healthy.",
            "@deepseek-ai/dsh-client-modules crashes! Said package works fine.",
        ):
            features = feat.extract(f"{clause} {BOOT_SYMPTOM}")
            assert features.subject_conflicted, clause
            payload = cli_diagnose(f"{clause} {BOOT_SYMPTOM}")
            assert payload["recommended_action"]["type"] not in UNSAFE, clause

    @pytest.mark.parametrize(
        "clause",
        [
            "@deepseek-ai/dsh-client-modules crashes! It did not malfunction.",
            "@deepseek-ai/dsh-client-modules crashes! It wasn't defective.",
        ],
    )
    def test_a_negated_unknown_verb_is_a_claim(self, clause):
        """A predicate we saw and could not classify is not silence.

        `did not malfunction` was classified as UNKNOWN and then dropped
        without recording anything, which is exactly the reading the unread
        path exists to prevent.
        """
        assert feat.extract(f"{clause} {BOOT_SYMPTOM}").pointed_unread_assertions
        assert cli_diagnose(f"{clause} {BOOT_SYMPTOM}")["recommended_action"]["type"] not in UNSAFE

    @pytest.mark.parametrize(
        "clause",
        [
            "@deepseek-ai/dsh-client-modules crashes! "
            "The package using our fallback shim remains operational.",
            "@deepseek-ai/dsh-client-modules crashes! It is operational using plugins.",
        ],
    )
    def test_a_relation_verb_inside_a_clause_is_not_its_predicate(self, clause):
        """`the package using our shim remains operational` predicates `remains`.

        Position could not tell a reduced relative clause from a wiring
        statement; what tells them apart is that the clause predicates
        something else as well.
        """
        assert feat.extract(f"{clause} {BOOT_SYMPTOM}").pointed_unread_assertions
        assert cli_diagnose(f"{clause} {BOOT_SYMPTOM}")["recommended_action"]["type"] not in UNSAFE

    QUOTED_REPORTS = (
        "Quoted from a retired vendor ticket:\n```text\n@nebula/theme-engine crashes. {boot}\n```",
        "Quoted from a retired vendor ticket:\n"
        "```text\n@deepseek-ai/dsh-client-modules is healthy. {boot}\n```",
        "Documentation example only:\n```text\n{boot}\n```",
        # A `>` reply and a labelled indented block are quotation the same
        # way a fence is. Recognising only the fence left both upgrading.
        "Copied from a resolved ticket, not our incident:\n> {boot}",
        "Archived documentation example only:\n    {boot}",
        # And one value outside the quotation used to re-authorise
        # everything inside it - a generic exception type, or naming the
        # same file. The gate re-runs itself on the stated view now.
        "Our unrelated color preview throws TypeError.\n"
        "For comparison, an old ticket says:\n```text\n{boot}\n```",
        "Our documentation links to packages/loader/src/internal.ts.\n"
        "An unrelated old incident follows:\n```text\n{boot}\n```",
    )

    @pytest.mark.parametrize("template", QUOTED_REPORTS)
    def test_quotation_finds_a_candidate_and_cannot_authorise_one(self, template):
        """A fence may reach stage one. It may not reach stage three alone.

        Deleting the subjects and claims inside a fence while keeping its paths,
        symbols and error strings counted its evidence in one direction only,
        and a block introduced as somebody else's ticket still upgraded.
        """
        report = template.format(boot=BOOT_SYMPTOM)
        features = feat.extract(report)
        assert features.quoted_only, "nothing was recognised as quoted-only evidence"
        payload = cli_diagnose(report)
        assert payload["recommended_action"]["type"] not in UNSAFE
        assert payload["stages"]["stopped_at"] == "retrieved_candidate"

    REAL_SYMPTOM = (
        "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
        "client-modules reports HTML did not preload "
        "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
        "TypeError: e.indexOf is not a function"
    )

    def test_free_text_alone_proposes_but_does_not_recommend(self):
        """The reason free text is allowed to be read at all.

        This report is the incident, correctly identified, with every piece of
        evidence the authorised version has. What it does not have is the user
        saying which package they run - so the answer is a proposal, and the
        action it *would* have recommended is recorded rather than issued.

        This is what makes a misreading of prose survivable. Getting a claim
        wrong can now cost a wrong suggestion, which the reader sees and
        rejects, instead of a wrong instruction to upgrade.
        """
        payload = cli_diagnose(self.REAL_SYMPTOM, packages=())
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["type"] not in UNSAFE
        assert payload["stages"]["stopped_at"] == "accepted_same_incident"
        authorization = payload["authorization"]
        assert authorization["authorized"] is False
        assert authorization["proposed_action"] == "upgrade"
        assert authorization["proposed_target"] == "dsh-v0.1.2-alpha.2"
        assert authorization["missing"]

    def test_naming_an_unrelated_package_does_not_authorise_either(self):
        """Stating *a* package is not stating *this* one."""
        payload = cli_diagnose(self.REAL_SYMPTOM, packages=("@nebula/theme-engine",))
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["type"] not in UNSAFE
        assert payload["authorization"]["authorized"] is False

    def test_both_surfaces_agree_about_authorisation(self):
        """MCP is not a way around the gate."""
        cli = cli_diagnose(self.REAL_SYMPTOM, packages=())
        mcp = stdio_mcp_diagnose(self.REAL_SYMPTOM, packages=())
        assert cli["authorization"]["authorized"] is False
        assert mcp["authorization"]["authorized"] is False
        assert mcp["recommended_action"]["type"] == cli["recommended_action"]["type"]

    def test_a_quotation_alongside_a_stated_report_costs_nothing(self):
        """The presence of a quotation does not by itself cost a positive.

        This used to be named as though it showed a report may fence its own
        trace. It does not: the evidence here is all in the prose and the fence
        holds an unrelated snippet. The case that name described is below, and
        it abstains.
        """
        real = (
            "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
            "client-modules reports HTML did not preload "
            "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
            "TypeError: e.indexOf is not a function"
        )
        report = real + "\nUpstream example for reference:\n```text\nsome unrelated snippet\n```"
        payload = cli_diagnose(report)
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    def test_a_report_whose_only_evidence_is_fenced_abstains(self):
        """A documented recall gap, asserted rather than described.

        When the identifying evidence sits *only* inside the fence, nothing
        distinguishes this report from one quoting somebody else's ticket, and
        the staging abstains. That is a real loss, and it is the conservative
        direction. The same trace under a neutral introduction, indented rather
        than fenced, still upgrades - indentation without a provenance label is
        code, not quotation.
        """
        trace = "packages/loader/src/internal.ts throws TypeError: e.indexOf is not a function"
        prose = "My current dsh web boot graph is empty and client modules never preload."

        fenced = cli_diagnose(f"{prose}\nHere is its trace:\n```text\n{trace}\n```")
        assert fenced["recommended_action"]["type"] not in UNSAFE
        assert fenced["stages"]["stopped_at"] == "retrieved_candidate"

        indented = cli_diagnose(f"{prose}\nHere is its trace:\n    {trace}")
        assert indented["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    def test_wiring_still_reads_as_wiring_when_it_is_progressive(self):
        """`We are using @x` is a relation statement; the auxiliary is part of it."""
        real = (
            "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
            "client-modules reports HTML did not preload "
            "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
            "TypeError: e.indexOf is not a function"
        )
        payload = cli_diagnose(f"We are using @deepseek-ai/dsh-client-modules. {real}")
        assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    def test_a_fenced_block_is_not_this_report_s_subject(self):
        """A documentation example must not become the package we act on.

        The fenced package was read as a second primary subject, which
        cancelled the conflict with the package the report actually blamed.
        """
        report = (
            "@nebula/theme-engine crashes. Documentation example only:\n"
            "```text\n@deepseek-ai/dsh-client-modules crashes\n```\n"
            f"{BOOT_SYMPTOM}"
        )
        features = feat.extract(report)
        assert "@deepseek-ai/dsh-client-modules" not in features.subject_packages
        assert "@nebula/theme-engine" in features.subject_packages
        assert cli_diagnose(report)["recommended_action"]["type"] not in UNSAFE

    def test_an_environment_line_does_not_cost_the_positive(self):
        """`@x version 0.1.2-alpha.1` predicates nothing about @x.

        Treating any alphabetic word after a mention as a predicate turned an
        ordinary environment line into an unconditional block.
        """
        real = (
            "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
            "client-modules reports HTML did not preload "
            "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
            "TypeError: e.indexOf is not a function"
        )
        report = "Environment: @deepseek-ai/dsh-client-modules version 0.1.2-alpha.1. " + real
        payload = cli_diagnose(report)
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    def test_a_pointed_unread_claim_blocks_even_a_shared_primary(self):
        """The invariant the previous round's commit message claimed but did not hold."""
        payload = cli_diagnose(
            "@deepseek-ai/dsh-client-modules crashes! It is operational. "
            "The client boot graph has no entries and packages/loader/src/internal.ts "
            "throws TypeError: e.indexOf is not a function",
            debug=True,
        )
        assert payload["incident"]["matched"] is False
        assert payload["recommended_action"]["type"] == "abstain"
        assert payload["debug"]["features"]["pointed_unread_assertions"]

    def test_the_copula_boundary_is_a_real_word_boundary(self):
        """A regex typo once made every bare adjective read as a copula.

        `The server is healthy when importing @dsh...` bound the server's health
        to the package, and the real incident was then wrongly abstained.
        """
        from repo_troubleshooter.fingerprint.subjects import COPULA_RE

        assert COPULA_RE.match("is healthy")
        assert not COPULA_RE.match("island")

        payload = cli_diagnose(
            "The server is healthy when importing "
            "@deepseek-ai/dsh-client-modules/client.js. HTML did not preload "
            "@deepseek-ai/dsh-client-modules/client.js and __DSH_BOOT__ has zero entries; "
            "TypeError: e.indexOf is not a function"
        )
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    def test_a_twelfth_unseen_phrasing_is_refused_too(self):
        """The point of the invariant: it does not depend on the phrasing."""
        payload = cli_diagnose(
            "@nebula/theme-engine has gone completely sideways in a way nobody has words for. "
            + BOOT_SYMPTOM
        )
        assert payload["incident"]["matched"] is False
        assert payload["recommended_action"]["type"] == "abstain"
