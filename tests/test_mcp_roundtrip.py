"""MCP round-trip through the real SDK client.

Two things are being proven:

1. The server actually speaks MCP - a genuine SDK client connects, lists tools
   and calls them. It connects to the server object **in this process**, which
   exercises the protocol but not the installed binary; `tests/test_cue_scope.py`
   covers that by launching `repo-troubleshooter-mcp` as a stdio subprocess.
2. The MCP facade is thin - for the same request, MCP and the CLI produce the
   same structured decision. If they ever diverge, one of them has grown its own
   logic, which is the failure this test exists to catch.
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
EXE_SUFFIX = ".exe" if os.name == "nt" else ""


def console_script(name: str) -> Path:
    return BIN / f"{name}{EXE_SUFFIX}"


REPO = "deepseek-ai/deepseek-harness"

LOADER_ERROR = (
    "dsh web starts but the boot graph is empty: __DSH_BOOT__ has zero entries, and "
    "client-modules reports HTML did not preload "
    "@deepseek-ai/dsh-client-modules/client.js; TypeError: e.indexOf is not a function "
    "raised from packages/loader/src/internal.ts"
)
UNRELATED_ERROR = "Redis connection lost: READONLY You can't write against a read only replica"


def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Start the stdio server in-process via the SDK client and call one tool."""
    from mcp import Client

    from repo_troubleshooter.mcp.server import mcp as server

    async def run() -> dict[str, Any]:
        async with Client(server) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"diagnose", "get_evidence"} <= tools, f"tools exposed: {tools}"
            result = await client.call_tool(tool, arguments)
            payload = result.structured_content
            if payload is None:
                blocks = [b for b in result.content if getattr(b, "type", None) == "text"]
                assert blocks, f"no content returned for {tool}"
                payload = json.loads(blocks[0].text)
            return payload

    return asyncio.run(run())


def _cli_json(*args: str) -> dict[str, Any]:
    executable = console_script("repo-troubleshooter")
    argv = (
        [str(executable), *args]
        if executable.exists()
        else [sys.executable, "-m", "repo_troubleshooter.cli.main", *args]
    )
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


@pytest.fixture(scope="module", autouse=True)
def _requires_synced_data(db_ready):  # noqa: ANN001
    payload = _cli_json("diagnose", "--repo", REPO, "--json", "--no-persist", "--error", "ping")
    if payload["sync_health"] == "stale":
        pytest.skip("no synced data; run `rt sync deepseek-harness` first")


class TestServerSurface:
    def test_entry_point_answers_without_starting_a_session(self):
        executable = console_script("repo-troubleshooter-mcp")
        if not executable.exists():
            pytest.skip("console script not installed")
        proc = subprocess.run(  # noqa: S603
            [str(executable), "--check"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode == 0
        described = json.loads(proc.stdout)
        assert {t["name"] for t in described["tools"]} == {"diagnose", "get_evidence"}
        assert all(t["read_only"] for t in described["tools"])

    def test_list_tools_over_the_protocol(self):
        payload = _call("diagnose", {"repo": REPO, "error": "ping"})
        assert payload["ok"] is True


class TestContractParity:
    def test_mcp_declared_question_stops_before_candidate_retrieval(self):
        payload = _call(
            "diagnose",
            {
                "repo": REPO,
                "question": "Can I configure the retry timeout?",
                "report_kind": "question",
            },
        )["result"]

        assert payload["report_assessment"]["kind"] == "question"
        assert payload["report_assessment"]["retrieval_allowed"] is False
        assert payload["stages"]["retrieved_candidates"] == 0
        assert payload["incident"]["matched"] is False
        assert payload["recommended_action"]["type"] == "abstain"

    def test_mcp_and_cli_agree_on_the_positive_case(self):
        mcp_payload = _call(
            "diagnose",
            {
                "repo": REPO,
                "error": LOADER_ERROR,
                "core_version": "0.1.2-alpha.1",
                "runtime": "node 24.11.1",
                "os_name": "windows",
            },
        )["result"]
        cli_payload = _cli_json(
            "diagnose",
            "--repo",
            REPO,
            "--json",
            # Read-only, like the MCP side it is compared against.
            "--no-persist",
            "--error",
            LOADER_ERROR,
            "--version",
            "0.1.2-alpha.1",
            "--runtime",
            "node 24.11.1",
            "--os",
            "windows",
        )

        assert mcp_payload["status"] == cli_payload["status"]
        assert (
            mcp_payload["recommended_action"]["type"] == cli_payload["recommended_action"]["type"]
        )
        assert (
            mcp_payload["recommended_action"]["target"]
            == cli_payload["recommended_action"]["target"]
        )
        assert mcp_payload["incident"]["matched"] == cli_payload["incident"]["matched"]
        assert mcp_payload["stages"]["stopped_at"] == cli_payload["stages"]["stopped_at"]
        assert {e["id"] for e in mcp_payload["evidence"]} == {
            e["id"] for e in cli_payload["evidence"]
        }

    def test_mcp_and_cli_agree_on_a_negative(self):
        mcp_payload = _call(
            "diagnose",
            {
                "repo": REPO,
                "error": UNRELATED_ERROR,
                "core_version": "0.1.2-alpha.1",
                "runtime": "node 24.11.1",
                "os_name": "windows",
            },
        )["result"]
        cli_payload = _cli_json(
            "diagnose",
            "--repo",
            REPO,
            "--json",
            # Read-only, like the MCP side it is compared against.
            "--no-persist",
            "--error",
            UNRELATED_ERROR,
            "--version",
            "0.1.2-alpha.1",
            "--runtime",
            "node 24.11.1",
        )
        assert mcp_payload["incident"]["matched"] is False
        assert cli_payload["incident"]["matched"] is False
        assert (
            mcp_payload["recommended_action"]["type"] == cli_payload["recommended_action"]["type"]
        )
        assert mcp_payload["recommended_action"]["target"] is None


class TestEvidenceTool:
    def test_evidence_ids_resolve_and_are_marked_untrusted(self):
        diagnosis = _call(
            "diagnose",
            {
                "repo": REPO,
                "error": LOADER_ERROR,
                "core_version": "0.1.2-alpha.1",
                "runtime": "node 24.11.1",
            },
        )["result"]
        assert diagnosis["evidence"], "expected evidence for the positive case"
        for ref in diagnosis["evidence"]:
            payload = _call("get_evidence", {"repo": REPO, "evidence_id": ref["id"]})
            assert payload["ok"] is True, payload
            assert payload["result"]["id"] == ref["id"]
            # Upstream text is data. The flag says so on every hop.
            assert payload["result"]["untrusted"] is True

    def test_unknown_evidence_id_is_a_structured_error_not_a_crash(self):
        payload = _call("get_evidence", {"repo": REPO, "evidence_id": "ev:release:does-not-exist"})
        assert payload["ok"] is False
        assert payload["error"]["code"] == "evidence_not_found"
        assert payload["error"]["remediation"]

    def test_unsynced_repository_is_a_structured_error(self):
        payload = _call("get_evidence", {"repo": "nobody/nothing", "evidence_id": "ev:release:x"})
        assert payload["ok"] is False
        assert payload["error"]["code"] == "repository_not_synced"
        assert "sync" in payload["error"]["remediation"]


class TestReadOnly:
    """An MCP tool call must not change a single row.

    `diagnose` on the CLI may cache a derived incident record; over MCP it may
    not, so the tool passes `persist=False`. This test counts every business
    table before and after real protocol calls and requires them identical.
    """

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

    def test_tools_are_annotated_read_only_in_the_protocol(self):
        import asyncio

        from mcp import Client

        from repo_troubleshooter.mcp.server import mcp as server

        async def run():  # noqa: ANN202
            async with Client(server) as client:
                return (await client.list_tools()).tools

        for tool in asyncio.run(run()):
            annotations = tool.annotations
            assert annotations is not None, f"{tool.name} has no annotations"
            assert annotations.read_only_hint is True, f"{tool.name} is not marked read-only"
            assert annotations.destructive_hint is False
            assert annotations.open_world_hint is False

    def test_diagnose_writes_nothing(self):
        before = self._counts()
        payload = _call(
            "diagnose",
            {
                "repo": REPO,
                "error": LOADER_ERROR,
                "core_version": "0.1.2-alpha.1",
                "runtime": "node 24.11.1",
                "os_name": "windows",
            },
        )
        assert payload["ok"] is True
        # A matched, actionable incident is exactly the case that would tempt a write.
        assert payload["result"]["incident"]["matched"] is True
        after = self._counts()
        assert after == before, f"MCP diagnose changed rows: {before} -> {after}"

    def test_repeated_calls_and_get_evidence_write_nothing(self):
        before = self._counts()
        for _ in range(2):
            result = _call(
                "diagnose",
                {
                    "repo": REPO,
                    "error": LOADER_ERROR,
                    "core_version": "0.1.2-alpha.1",
                    "runtime": "node 24.11.1",
                },
            )["result"]
            for ref in result["evidence"]:
                _call("get_evidence", {"repo": REPO, "evidence_id": ref["id"]})
        _call("diagnose", {"repo": REPO, "error": UNRELATED_ERROR, "core_version": "0.1.2-alpha.1"})
        after = self._counts()
        assert after == before, f"MCP calls changed rows: {before} -> {after}"

    def test_the_incident_table_specifically_is_untouched(self):
        """Called out separately: this is the table the engine can persist to."""
        before = self._counts()["incident_resolution_record"]
        _call(
            "diagnose",
            {
                "repo": REPO,
                "error": LOADER_ERROR,
                "core_version": "0.1.1-rc.2",
                "runtime": "node 24.11.1",
            },
        )
        after = self._counts()["incident_resolution_record"]
        assert after == before
