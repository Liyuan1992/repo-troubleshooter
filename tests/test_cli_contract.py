"""Black-box CLI tests.

These drive the installed console script in a subprocess, assert the exit code
and parse the public JSON contract - the same way an outside evaluator would.
Nothing here imports the engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "repo-troubleshooter"
REPO = "deepseek-ai/deepseek-harness"

LOADER_ERROR = (
    "dsh web starts but the boot graph is empty: __DSH_BOOT__ has zero entries, and "
    "client-modules reports HTML did not preload "
    "@deepseek-ai/dsh-client-modules/client.js; TypeError: e.indexOf is not a function "
    "raised from packages/loader/src/internal.ts"
)
UNRELATED_ERROR = (
    "PostgreSQL startup failed: connection refused at 127.0.0.1:5432 while applying migrations"
)


def run_cli(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    executable = str(CLI) if CLI.exists() else None
    argv = (
        [executable, *args]
        if executable
        else [sys.executable, "-m", "repo_troubleshooter.cli.main", *args]
    )
    return subprocess.run(  # noqa: S603
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        timeout=timeout,
        check=False,
    )


def diagnose_json(*extra: str) -> dict:
    result = run_cli("diagnose", "--repo", REPO, "--json", *extra)
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module", autouse=True)
def _requires_synced_data():
    status = run_cli("status", REPO)
    if status.returncode != 0 or "nothing synced" in status.stdout:
        pytest.skip("no synced data; run `rt sync deepseek-harness` first")


class TestCommandSurface:
    def test_diagnose_command_exists(self):
        result = run_cli("diagnose", "--help")
        assert result.returncode == 0
        assert "--error" in result.stdout
        assert "--version" in result.stdout

    def test_unknown_repo_still_exits_zero_with_a_contract(self):
        result = run_cli("diagnose", "--repo", "nobody/nothing", "--json", "--error", "boom")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "insufficient_evidence"
        assert payload["recommended_action"]["type"] == "collect_more_info"


class TestPublicContract:
    def test_contract_shape(self):
        payload = diagnose_json("--error", LOADER_ERROR, "--version", "0.1.2-alpha.1")
        for key in (
            "status",
            "environment",
            "incident",
            "claims",
            "recommended_action",
            "conflicts",
            "missing_information",
            "evidence",
            "data_as_of",
            "sync_health",
        ):
            assert key in payload, f"missing contract field: {key}"
        assert payload["status"] in (
            "confirmed",
            "probable",
            "insufficient_evidence",
            "conflicting",
        )

    def test_every_claim_cites_evidence_that_is_listed(self):
        payload = diagnose_json("--error", LOADER_ERROR, "--version", "0.1.2-alpha.1")
        listed = {e["id"] for e in payload["evidence"]}
        for claim in payload["claims"]:
            assert claim["evidence_ids"], f"claim without evidence: {claim['value']}"
            assert set(claim["evidence_ids"]) <= listed

    def test_evidence_ids_resolve_through_get_evidence(self):
        payload = diagnose_json("--error", LOADER_ERROR, "--version", "0.1.2-alpha.1")
        assert payload["evidence"], "expected evidence for a matched incident"
        for ref in payload["evidence"]:
            result = run_cli("get-evidence", REPO, ref["id"], "--json")
            assert result.returncode == 0, f"could not resolve {ref['id']}"
            resolved = json.loads(result.stdout)
            assert resolved["id"] == ref["id"]
            assert resolved["locator"]


class TestEvaluatorCases:
    def test_old_release_recommends_the_first_containing_release(self):
        payload = diagnose_json(
            "--error", LOADER_ERROR, "--version", "0.1.2-alpha.1", "--runtime", "node 24.11.1"
        )
        action = payload["recommended_action"]
        assert action["type"] == "upgrade"
        assert action["target"] == "dsh-v0.1.2-alpha.2"
        types = {e["source_type"] for e in payload["evidence"]}
        assert {"discussion", "commit", "release"} <= types

    def test_contained_release_does_not_repeat_the_upgrade(self):
        payload = diagnose_json(
            "--error", LOADER_ERROR, "--version", "0.1.2-alpha.3", "--runtime", "node 24.11.1"
        )
        assert payload["recommended_action"]["type"] != "upgrade"
        assert "already contains" in payload["recommended_action"]["rationale"].lower()

    def test_unparseable_version_is_unresolved_not_newer(self):
        payload = diagnose_json(
            "--error", LOADER_ERROR, "--version", "nightly-2026-09-01", "--runtime", "node 24.11.1"
        )
        assert payload["applicability"]["status"] == "unresolved_version"
        assert payload["recommended_action"]["type"] != "upgrade"

    def test_runtime_contradiction_is_reported_not_ignored(self):
        payload = diagnose_json(
            "--error", LOADER_ERROR, "--version", "0.1.2-alpha.1", "--runtime", "node 22.19.0"
        )
        assert payload["status"] == "conflicting"
        assert payload["applicability"]["status"] == "hard_contradiction"
        assert payload["conflicts"]
        assert payload["recommended_action"]["type"] != "upgrade"

    def test_negative_control_abstains_with_no_claims(self):
        payload = diagnose_json(
            "--error", UNRELATED_ERROR, "--version", "0.1.2-alpha.1", "--runtime", "node 24.11.1"
        )
        assert payload["incident"]["matched"] is False
        assert payload["claims"] == []
        assert payload["evidence"] == []
        assert payload["recommended_action"]["type"] in ("abstain", "collect_more_info")
        assert payload["recommended_action"]["target"] is None


class TestPrivacy:
    def test_secrets_in_the_error_text_are_redacted_in_the_contract(self):
        payload = diagnose_json(
            "--error",
            "boot failed with api_key=sk-abcdef0123456789abcdef and token ghp_ABCDEFGHIJKLMNOPQRST",
            "--version",
            "0.1.2-alpha.1",
        )
        blob = json.dumps(payload)
        assert "sk-abcdef0123456789abcdef" not in blob
        assert "ghp_ABCDEFGHIJKLMNOPQRST" not in blob
        assert "redacted" in blob
