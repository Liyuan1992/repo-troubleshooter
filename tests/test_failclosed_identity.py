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
from repo_troubleshooter.fingerprint.subjects import PackageRole, classify

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
]

CASES = [pytest.param(f"{clause}. {BOOT_SYMPTOM}", id=case_id) for case_id, clause in PHRASINGS]


def cli_diagnose(error: str, *, version: str = "0.1.2-alpha.1", debug: bool = False) -> dict:
    executable = BIN / f"repo-troubleshooter{EXE}"
    argv = (
        [str(executable)]
        if executable.exists()
        else [sys.executable, "-m", "repo_troubleshooter.cli.main"]
    )
    argv += ["diagnose", "--repo", REPO, "--json", "--error", error, "--version", version]
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


def stdio_mcp_diagnose(error: str, *, version: str = "0.1.2-alpha.1") -> dict[str, Any]:
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
                "diagnose", {"repo": REPO, "error": error, "core_version": version}
            )
            payload = result.structured_content
            if payload is None:
                blocks = [b for b in result.content if getattr(b, "type", None) == "text"]
                payload = json.loads(blocks[0].text)
            return payload

    payload = asyncio.run(run())
    assert payload["ok"] is True, payload
    return payload["result"]


class TestRolesAreFailClosed:
    def test_an_unrecognised_phrasing_is_unresolved_not_harmless(self):
        mention = classify("@nebula/theme-engine went sideways on us").package_mentions[0]
        assert mention.role is PackageRole.UNRESOLVED

    def test_contradictory_predicates_are_unresolved(self):
        mention = classify("@nebula/theme-engine is healthy but crashes").package_mentions[0]
        assert mention.role is PackageRole.UNRESOLVED
        assert "conflicting" in mention.cue

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

    def test_every_phrasing_keeps_the_external_package_out_of_dsh(self):
        for case_id, clause in PHRASINGS:
            features = feat.extract(f"{clause}. {BOOT_SYMPTOM}")
            named = (
                features.subject_packages
                | features.subject_unresolved
                | features.subject_confirmed_non_primary
                | features.subject_dependencies
            )
            assert "@nebula/theme-engine" in named, case_id
            assert "@nebula/theme-engine" not in features.subject_confirmed_non_primary, case_id


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

    def test_a_twelfth_unseen_phrasing_is_refused_too(self):
        """The point of the invariant: it does not depend on the phrasing."""
        payload = cli_diagnose(
            "@nebula/theme-engine has gone completely sideways in a way nobody has words for. "
            + BOOT_SYMPTOM
        )
        assert payload["incident"]["matched"] is False
        assert payload["recommended_action"]["type"] == "abstain"
