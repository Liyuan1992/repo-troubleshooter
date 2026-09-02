"""Cue scope: a cue speaks only for the mention it is attached to.

Three surprise cases from review, all of which used to end in the wrong answer:

1. `@nebula/theme-engine is not working` while it imports a healthy DSH
   dependency - `not working` was read as `working`, Nebula lost the primary
   role, and the DSH incident was matched and an upgrade recommended.
2. `@deepseek-ai/dsh-client-modules is healthy; @nebula/theme-engine crashes` -
   the first package's health cue reached across to the second, both became
   `mentioned`, and the DSH incident matched again.
3. `peer dependency @scope/lib is not up to date` - read as `up to date`.

The fixes are structural, not vocabulary: negation is resolved before any bare
health cue, the negated thing is classified by what it is, and every cue is
anchored to its own clause and stops at another package mention.

Every case runs through the identity gate, the installed CLI subprocess, and a
freshly launched `repo-troubleshooter-mcp` stdio process.
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

BOOT_SYMPTOM = (
    "The client boot graph has no entries or batches and packages/loader/src/internal.ts "
    "throws TypeError: e.indexOf is not a function on Windows"
)

CASE_NOT_WORKING = (
    "@nebula/theme-engine is not working. It only imports the healthy "
    "@deepseek-ai/dsh-client-modules dependency. " + BOOT_SYMPTOM
)
CASE_HEALTH_BLEED = (
    "@deepseek-ai/dsh-client-modules is healthy; @nebula/theme-engine crashes. " + BOOT_SYMPTOM
)
CASE_NOT_UP_TO_DATE = "peer dependency @scope/lib is not up to date"

SURPRISE_CASES = [
    pytest.param(CASE_NOT_WORKING, id="not-working"),
    pytest.param(CASE_HEALTH_BLEED, id="health-bleed"),
]


# --- surfaces ---------------------------------------------------------------


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
    """Launch the installed console script as a real stdio subprocess.

    Not `Client(server)`: that runs the server in this process. This starts
    `repo-troubleshooter-mcp` the way an MCP host would, and talks to it over
    its stdin/stdout.
    """
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    executable = BIN / f"repo-troubleshooter-mcp{EXE}"
    if not executable.exists():
        pytest.skip("repo-troubleshooter-mcp console script is not installed")

    params = StdioServerParameters(
        command=str(executable),
        args=[],
        env=dict(os.environ),
        cwd=str(PROJECT_ROOT),
    )

    async def run() -> dict[str, Any]:
        async with Client(params) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"diagnose", "get_evidence"} <= tools, tools
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


# --- the rule itself --------------------------------------------------------


class TestNegationIsResolvedBeforeHealth:
    def test_not_working_is_a_failure_not_health(self):
        mentions = {m.name: m for m in classify(CASE_NOT_WORKING).package_mentions}
        nebula = mentions["@nebula/theme-engine"]
        assert nebula.role is PackageRole.PRIMARY
        assert "not working" in nebula.cue

    @pytest.mark.parametrize(
        "text",
        [
            "@scope/lib is not working",
            "@scope/lib is not healthy",
            "@scope/lib is not okay",
            "@scope/lib is not stable",
            "@scope/lib is not up to date",
            "@scope/lib is not fixed",
            "@scope/lib is not resolved",
            "@scope/lib is not passing",
        ],
    )
    def test_every_negated_positive_state_is_a_failure(self, text):
        mention = classify(text).package_mentions[0]
        assert mention.role is PackageRole.PRIMARY, mention.cue

    @pytest.mark.parametrize(
        "text",
        ["@scope/lib does not crash", "@scope/lib is not failing", "@scope/lib did not throw"],
    )
    def test_negated_failure_verbs_stay_health(self, text):
        mention = classify(text).package_mentions[0]
        assert mention.role is PackageRole.CONFIRMED_NON_PRIMARY

    @pytest.mark.parametrize(
        "text",
        ["@scope/lib did not load", "@scope/lib never started", "could not resolve @scope/lib"],
    )
    def test_negated_expected_actions_are_failures(self, text):
        mention = classify(text).package_mentions[0]
        assert mention.role is PackageRole.PRIMARY, mention.cue

    def test_not_up_to_date_produces_no_health_cue(self):
        mentions = classify(CASE_NOT_UP_TO_DATE).package_mentions
        assert mentions
        for mention in mentions:
            assert mention.role is not PackageRole.CONFIRMED_NON_PRIMARY, mention.cue


class TestCueDoesNotCrossMentions:
    def test_health_does_not_bleed_onto_the_next_package(self):
        mentions = {m.name: m for m in classify(CASE_HEALTH_BLEED).package_mentions}
        assert mentions["@deepseek-ai/dsh-client-modules"].role is PackageRole.CONFIRMED_NON_PRIMARY
        nebula = mentions["@nebula/theme-engine"]
        assert nebula.role is PackageRole.PRIMARY

    def test_a_failure_does_not_bleed_backwards_either(self):
        text = "@scope/first is fine. @scope/second crashes"
        mentions = {m.name: m for m in classify(text).package_mentions}
        assert mentions["@scope/first"].role is PackageRole.CONFIRMED_NON_PRIMARY
        assert mentions["@scope/second"].role is PackageRole.PRIMARY

    def test_the_external_package_keeps_the_primary_role(self):
        """Which is what lets the package conflict refuse the DSH incident."""
        for case in (CASE_NOT_WORKING, CASE_HEALTH_BLEED):
            features = feat.extract(case)
            assert "@nebula/theme-engine" in features.subject_packages, case
            assert "@deepseek-ai/dsh-client-modules" not in features.subject_packages, case


# --- the product, through both surfaces -------------------------------------


@pytest.mark.db
@pytest.mark.live
class TestSurpriseCasesEndToEnd:
    @pytest.mark.parametrize("error", SURPRISE_CASES)
    def test_cli_refuses_the_dsh_incident(self, error):
        payload = cli_diagnose(error)
        assert payload["incident"]["matched"] is False, payload["incident"]["title"]
        assert payload["recommended_action"]["type"] not in UNSAFE
        assert payload["recommended_action"]["target"] is None

    @pytest.mark.parametrize("error", SURPRISE_CASES)
    def test_stdio_mcp_refuses_the_dsh_incident(self, error):
        payload = stdio_mcp_diagnose(error)
        assert payload["incident"]["matched"] is False, payload["incident"]["title"]
        assert payload["recommended_action"]["type"] not in UNSAFE
        assert payload["recommended_action"]["target"] is None

    @pytest.mark.parametrize("error", SURPRISE_CASES)
    def test_cli_and_stdio_mcp_agree(self, error):
        cli = cli_diagnose(error)
        mcp = stdio_mcp_diagnose(error)
        assert cli["status"] == mcp["status"]
        assert cli["incident"]["matched"] == mcp["incident"]["matched"]
        assert cli["recommended_action"]["type"] == mcp["recommended_action"]["type"]
        assert cli["recommended_action"]["target"] == mcp["recommended_action"]["target"]
        assert cli["stages"]["stopped_at"] == mcp["stages"]["stopped_at"]

    def test_not_up_to_date_has_no_health_cue_through_the_cli(self):
        payload = cli_diagnose(CASE_NOT_UP_TO_DATE, debug=True)
        mentions = payload["debug"]["features"]["package_mentions"]
        assert mentions
        for mention in mentions:
            assert "healthy" not in mention["cue"], mention
        assert payload["recommended_action"]["type"] not in UNSAFE

    def test_the_real_incident_still_works_on_both_surfaces(self):
        """The fix must not cost recall on the case that should match."""
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


@pytest.mark.db
@pytest.mark.live
class TestStdioMcpWritesNothing:
    TABLES = (
        "incident_resolution_record",
        "source_object",
        "object_revision",
        "content_unit",
        "relation_assertion",
        "release",
        "release_containment",
        "git_commit",
        "symptom_signature",
        "package_manifest",
        "repository",
        "sync_state",
    )

    def _counts(self) -> dict[str, int]:
        from sqlalchemy import text

        from repo_troubleshooter.store.db import get_engine

        with get_engine().connect() as conn:
            return {
                table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in self.TABLES
            }

    def test_row_counts_are_identical_around_a_stdio_session(self):
        before = self._counts()
        for error in (CASE_NOT_WORKING, CASE_HEALTH_BLEED, CASE_NOT_UP_TO_DATE):
            stdio_mcp_diagnose(error)
        after = self._counts()
        assert after == before, f"stdio MCP changed rows: {before} -> {after}"
