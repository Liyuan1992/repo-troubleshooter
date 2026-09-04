"""Cue semantics, exercised through the real interfaces.

`tests/test_package_roles.py` calls the identity gate directly, which proves the
rule but not the product. These drive the **installed CLI** as a subprocess and
the **MCP server** over the real protocol, so a regression anywhere between the
request contract and the rendered answer is caught here.

What is pinned:

* a bare `does not` is not failure evidence;
* `does not crash` must not produce a match; `did not preload` may;
* an explicit `is healthy` outranks a failure word standing next to it;
* `peer dependency ... healthy` is not a version conflict;
* a package family relation survives the identity-check budget.
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

pytestmark = [pytest.mark.db, pytest.mark.live]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BIN = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
EXE = ".exe" if os.name == "nt" else ""
REPO = "deepseek-ai/deepseek-harness"

UNSAFE = {"upgrade", "downgrade", "migrate", "config_change", "workaround"}

REAL_SYMPTOM = (
    "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; client-modules "
    "reports HTML did not preload @deepseek-ai/dsh-client-modules/client.js, and the host "
    "throws TypeError: e.indexOf is not a function"
)


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
    """Run the installed console script as a subprocess and parse its contract."""
    executable = BIN / f"repo-troubleshooter{EXE}"
    argv = (
        [str(executable)]
        if executable.exists()
        else [sys.executable, "-m", "repo_troubleshooter.cli.main"]
    )
    argv += ["diagnose", "--repo", REPO, "--json", "--no-persist"]
    argv += ["--error", error, "--version", version]
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


def mcp_diagnose(
    error: str,
    *,
    version: str = "0.1.2-alpha.1",
    packages: tuple[str, ...] = (STATED_PACKAGE,),
) -> dict[str, Any]:
    """Call the MCP tool over the real protocol."""
    from mcp import Client

    from repo_troubleshooter.mcp.server import mcp as server

    async def run() -> dict[str, Any]:
        async with Client(server) as client:
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


class TestNegationNeedsANamedAction:
    def test_bare_negation_is_not_a_match(self):
        """A sentence that negates nothing in particular proves nothing."""
        for surface in (cli_diagnose, mcp_diagnose):
            payload = surface(
                "@acme/theme-kit does not. Windows, client boot graph, TypeError: "
                "e.indexOf is not a function"
            )
            assert payload["incident"]["matched"] is False
            assert payload["recommended_action"]["type"] not in UNSAFE

    def test_negated_failure_verb_is_not_a_match(self):
        """`does not crash` says it works."""
        payload = cli_diagnose(
            "@acme/theme-kit does not crash and does not fail; the boot graph has entries "
            "and TypeError: e.indexOf is not a function appears elsewhere"
        )
        assert payload["incident"]["matched"] is False
        assert payload["recommended_action"]["type"] not in UNSAFE

    def test_negated_expected_action_still_reaches_the_incident(self):
        """`did not preload` is the failure, and must still work end to end."""
        for surface in (cli_diagnose, mcp_diagnose):
            payload = surface(REAL_SYMPTOM)
            assert payload["incident"]["matched"] is True
            assert payload["recommended_action"]["type"] == "upgrade"
            assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"


class TestHealthOutranksNearbyFailureWords:
    def test_a_healthy_package_beside_a_failure_word_is_not_the_subject(self):
        payload = cli_diagnose(
            "Something crashed during startup on Windows, but @acme/theme-kit is healthy and "
            "not failing; TypeError: e.indexOf is not a function"
        )
        assert payload["recommended_action"]["type"] not in UNSAFE

    def test_a_healthy_dependency_does_not_change_the_real_answer(self):
        baseline = cli_diagnose(REAL_SYMPTOM)
        padded = cli_diagnose(
            REAL_SYMPTOM + " We also depend on @sindresorhus/is, which is healthy."
        )
        assert padded["incident"]["matched"] == baseline["incident"]["matched"]
        assert padded["recommended_action"]["type"] == baseline["recommended_action"]["type"]
        assert padded["recommended_action"]["target"] == baseline["recommended_action"]["target"]

    def test_healthy_peer_dependency_is_not_a_version_conflict(self):
        """Item 7, through the CLI: no `version_conflict` cause, no unsafe action."""
        payload = cli_diagnose(
            "peer dependency @scope/lib is healthy and up to date; the client boot graph "
            "still has no entries",
            debug=True,
        )
        causes = payload["debug"]["features"]["causes"]
        assert "version_conflict" not in causes, causes
        assert payload["recommended_action"]["type"] not in UNSAFE

    def test_an_unmet_peer_dependency_still_is_one(self):
        payload = cli_diagnose(
            "npm ERR! peer dependency @scope/lib is unmet and conflicts with the installed version",
            debug=True,
        )
        assert "version_conflict" in payload["debug"]["features"]["causes"]


class TestFamilyReachesStageOne:
    def test_related_packages_are_prioritised_over_the_check_budget(self):
        """A product-family candidate must not be truncated by MAX_IDENTITY_CHECKS."""
        payload = cli_diagnose(REAL_SYMPTOM, debug=True)
        candidates = payload["debug"]["retrieval"]["candidates"]
        assert candidates, "expected stage-1 candidates"
        checked = [c for c in candidates if c.get("identity")]
        assert checked, "expected at least one identity decision"
        # Every family-related candidate that reached stage 1 was evaluated.
        related = [c for c in candidates if c.get("family_related")]
        for candidate in related:
            assert candidate.get("identity"), (
                f"family-related candidate #{candidate['number']} was never checked"
            )

    def test_the_manifest_family_is_loaded_for_this_repository(self, session, synced_repo):
        from repo_troubleshooter.versions.packages import PackageFamily

        family = PackageFamily.load(session, synced_repo.id)
        assert len(family.names) > 50
        # Strict acceptance needs both names published here; a look-alike does not.
        published = sorted(family.names, key=len)
        product = next(
            (n for n in published if sum(1 for o in family.names if family.related(n, o)) > 10),
            None,
        )
        assert product
        assert not family.related(product, f"{product}-fabricated-by-nobody")
        assert family.related_for_retrieval(product, f"{product}-fabricated-by-nobody")


class TestCliAndMcpStillAgree:
    def test_same_answer_from_both_surfaces(self):
        cli = cli_diagnose(REAL_SYMPTOM)
        mcp = mcp_diagnose(REAL_SYMPTOM)
        assert cli["status"] == mcp["status"]
        assert cli["incident"]["matched"] == mcp["incident"]["matched"]
        assert cli["recommended_action"]["type"] == mcp["recommended_action"]["type"]
        assert cli["recommended_action"]["target"] == mcp["recommended_action"]["target"]
        assert {e["id"] for e in cli["evidence"]} == {e["id"] for e in mcp["evidence"]}
