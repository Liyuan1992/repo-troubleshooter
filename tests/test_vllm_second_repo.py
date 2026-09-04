"""Independent acceptance for the structurally different second repository.

The case is frozen public evidence, not a synthetic self-test:

* vLLM issue #6461 reports that /metrics is 404 on 0.5.2 and worked on 0.5.1;
* PR #6463 natively closes it and has a merge commit;
* v0.5.3 is the first tag containing that commit.

Every assertion goes through the installed CLI or a fresh installed MCP stdio
process.  Free text may reach a proposal; the version action appears only after
the caller confirms the echoed understanding digest.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from repo_troubleshooter.store.db import session_scope
from repo_troubleshooter.store.models import IncidentResolutionRecord, Repository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BIN = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
EXE = ".exe" if os.name == "nt" else ""
CLI = BIN / f"repo-troubleshooter{EXE}"
MCP = BIN / f"repo-troubleshooter-mcp{EXE}"
REPO = "vllm-project/vllm"
SYMPTOM = (
    "No Prometheus metrics are exposed at /metrics after upgrading to vLLM 0.5.2; "
    "GET /metrics returns 404 Not Found."
)
NEGATIVE = "CUDA out of memory while allocating KV cache during model startup"
VERSION_ACTIONS = {"upgrade", "downgrade", "migrate", "config_change", "workaround"}


def _require_vllm() -> None:
    if not CLI.exists() or not MCP.exists():
        pytest.skip("installed CLI/MCP console scripts are required")
    with session_scope() as session:
        if session.scalar(select(Repository).where(Repository.full_name == REPO)) is None:
            pytest.skip("vLLM evidence is not synced")


def _cli(error: str, version: str, *, confirm: str | None = None) -> dict[str, Any]:
    argv = [
        str(CLI),
        "diagnose",
        "--repo",
        REPO,
        "--error",
        error,
        "--version",
        version,
        "--runtime",
        "python 3.10.12",
        "--os",
        "linux",
        "--json",
        "--no-persist",
    ]
    if confirm:
        argv += ["--confirm", confirm]
    proc = subprocess.run(  # noqa: S603
        argv,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _assert_old_version_proposal(payload: dict[str, Any]) -> None:
    assert payload["incident"]["matched"] is True
    assert payload["incident"]["url"].endswith("/issues/6461")
    assert payload["authorization"]["proposed_action"] == "upgrade"
    assert payload["authorization"]["proposed_target"] == "v0.5.3"
    assert payload["recommended_action"]["type"] == "collect_more_info"
    assert payload["stages"]["stopped_at"] == "accepted_same_incident"


def _assert_confirmed_upgrade(payload: dict[str, Any]) -> None:
    assert payload["status"] == "probable"
    assert payload["authorization"] == {
        "authorized": True,
        "source": "confirmed",
        "proposed_action": None,
        "proposed_target": None,
        "missing": [],
        "requires_confirmation": False,
    }
    assert payload["recommended_action"]["type"] == "upgrade"
    assert payload["recommended_action"]["target"] == "v0.5.3"
    assert payload["stages"]["stopped_at"] == "actionable_incident"
    evidence = {item["source_type"] for item in payload["evidence"]}
    assert {"issue", "pull_request", "commit", "release"} <= evidence


@pytest.mark.db
@pytest.mark.live
class TestInstalledCli:
    def test_old_version_requires_echo_confirmation_then_upgrades(self) -> None:
        _require_vllm()
        proposal = _cli(SYMPTOM, "0.5.2")
        _assert_old_version_proposal(proposal)
        confirmed = _cli(SYMPTOM, "0.5.2", confirm=proposal["understood"]["digest"])
        _assert_confirmed_upgrade(confirmed)

    def test_new_version_does_not_receive_the_stale_upgrade(self) -> None:
        _require_vllm()
        payload = _cli(SYMPTOM, "0.5.3")
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["type"] == "collect_more_info"
        assert payload["recommended_action"]["target"] is None
        assert "already contains" in payload["recommended_action"]["rationale"]

    def test_unrelated_failure_abstains(self) -> None:
        _require_vllm()
        payload = _cli(NEGATIVE, "0.5.2")
        assert payload["incident"]["matched"] is False
        assert payload["recommended_action"]["type"] not in VERSION_ACTIONS
        assert payload["recommended_action"]["target"] is None


def _mcp_triplet() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(
        command=str(MCP), args=[], env=dict(os.environ), cwd=str(PROJECT_ROOT)
    )

    async def run() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with Client(params) as client:

            async def diagnose(
                error: str, version: str, confirm: str | None = None
            ) -> dict[str, Any]:
                result = await client.call_tool(
                    "diagnose",
                    {
                        "repo": REPO,
                        "error": error,
                        "core_version": version,
                        "runtime": "python 3.10.12",
                        "os_name": "linux",
                        "confirm": confirm,
                    },
                )
                payload = result.structured_content
                if payload is None:
                    blocks = [b for b in result.content if getattr(b, "type", None) == "text"]
                    payload = json.loads(blocks[0].text)
                assert payload["ok"] is True, payload
                return payload["result"]

            proposal = await diagnose(SYMPTOM, "0.5.2")
            confirmed = await diagnose(SYMPTOM, "0.5.2", confirm=proposal["understood"]["digest"])
            current = await diagnose(SYMPTOM, "0.5.3")
            negative = await diagnose(NEGATIVE, "0.5.2")
            return proposal, confirmed, current, negative

    return asyncio.run(run())


@pytest.mark.db
@pytest.mark.live
def test_fresh_stdio_mcp_process_obeys_the_same_triplet() -> None:
    _require_vllm()
    proposal, confirmed, current, negative = _mcp_triplet()
    _assert_old_version_proposal(proposal)
    _assert_confirmed_upgrade(confirmed)
    assert current["incident"]["matched"] is True
    assert current["recommended_action"]["type"] == "collect_more_info"
    assert current["recommended_action"]["target"] is None
    assert negative["incident"]["matched"] is False
    assert negative["recommended_action"]["type"] not in VERSION_ACTIONS


@pytest.mark.db
@pytest.mark.live
def test_curated_chain_is_reviewed_but_not_runtime_verified() -> None:
    _require_vllm()
    with session_scope() as session:
        repo = session.scalar(select(Repository).where(Repository.full_name == REPO))
        assert repo is not None
        record = session.scalar(
            select(IncidentResolutionRecord).where(
                IncidentResolutionRecord.repo_id == repo.id,
                IncidentResolutionRecord.incident_key == "reviewed:metrics-endpoint-missing-0.5.2",
            )
        )
        assert record is not None
        assert record.review_state == "reviewed"
        assert record.maintainer_confirmed is True
        assert record.release_contains_change is True
        assert record.runtime_verified is False
        assert record.first_release_containing_change == "v0.5.3"
