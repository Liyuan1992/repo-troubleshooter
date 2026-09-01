"""Stale signatures must stop a diagnosis, not quietly degrade it.

Changing how features are extracted invalidates every mined row: stored
candidate features stop meaning what query features mean, and identity
comparisons silently become nonsense. So the extractor version is stamped on the
mined data, checked before every diagnosis, and the upgrade that changed
extraction deletes the old rows.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from repo_troubleshooter.diagnosis.contract import DiagnosisRequest
from repo_troubleshooter.diagnosis.engine import diagnose
from repo_troubleshooter.fingerprint.subjects import FEATURE_EXTRACTOR_VERSION
from repo_troubleshooter.relations.signatures import (
    SignaturesStale,
    require_fresh_signatures,
    signature_state,
)
from repo_troubleshooter.store.models import SyncState

pytestmark = [pytest.mark.db, pytest.mark.live]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BIN = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin")
EXE = ".exe" if os.name == "nt" else ""
REPO = "deepseek-ai/deepseek-harness"


class TestFreshState:
    def test_current_database_is_stamped_with_this_extractor(self, session, synced_repo):
        state = signature_state(session, synced_repo)
        assert state.rows > 0, "no signatures mined; run `rt signatures <repo>`"
        assert state.stored_version == FEATURE_EXTRACTOR_VERSION
        assert state.ok

    def test_require_fresh_passes_when_stamped(self, session, synced_repo):
        assert require_fresh_signatures(session, synced_repo).ok


class TestStaleIsRefused:
    """Every path that can answer a question must refuse a stale corpus."""

    @pytest.fixture
    def stamped_old(self, session, synced_repo):  # noqa: ANN001, ANN201
        """Temporarily rewind the stored extractor version, then restore it."""
        state = session.scalar(
            select(SyncState).where(
                SyncState.repo_id == synced_repo.id, SyncState.source == "signatures"
            )
        )
        assert state is not None
        original = dict(state.stats or {})
        state.stats = {**original, "extractor_version": FEATURE_EXTRACTOR_VERSION - 1}
        session.commit()
        yield synced_repo
        state.stats = original
        session.commit()

    def test_state_reports_it(self, session, stamped_old):
        state = signature_state(session, stamped_old)
        assert not state.ok
        assert state.stored_version == FEATURE_EXTRACTOR_VERSION - 1

    def test_require_fresh_raises_with_a_runnable_command(self, session, stamped_old):
        with pytest.raises(SignaturesStale) as excinfo:
            require_fresh_signatures(session, stamped_old)
        message = str(excinfo.value)
        assert "--rebuild" in message
        assert REPO in message

    def test_diagnosis_refuses_rather_than_answering(self, session, stamped_old):
        request = DiagnosisRequest(
            repo=REPO,
            error="__DSH_BOOT__ has zero entries and client-modules did not preload",
            core_version="0.1.2-alpha.1",
        )
        with pytest.raises(SignaturesStale):
            diagnose(request, session, persist=False)

    def test_cli_exits_non_zero_without_a_traceback(self, session, stamped_old):
        executable = BIN / f"repo-troubleshooter{EXE}"
        argv = (
            [str(executable)]
            if executable.exists()
            else [sys.executable, "-m", "repo_troubleshooter.cli.main"]
        )
        proc = subprocess.run(  # noqa: S603
            [*argv, "diagnose", "--repo", REPO, "--json", "--error", "anything"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(PROJECT_ROOT),
            timeout=300,
            check=False,
        )
        assert proc.returncode != 0
        combined = proc.stdout + proc.stderr
        assert "Traceback" not in combined
        assert "--rebuild" in combined

    def test_mcp_returns_a_structured_error(self, session, stamped_old):
        import asyncio

        from mcp import Client

        from repo_troubleshooter.mcp.server import mcp as server

        async def run():  # noqa: ANN202
            async with Client(server) as client:
                result = await client.call_tool(
                    "diagnose", {"repo": REPO, "error": "anything", "core_version": "0.1.2-alpha.1"}
                )
                payload = result.structured_content
                if payload is None:
                    blocks = [b for b in result.content if getattr(b, "type", None) == "text"]
                    payload = json.loads(blocks[0].text)
                return payload

        payload = asyncio.run(run())
        assert payload["ok"] is False
        assert payload["error"]["code"] == "signatures_stale"
        assert "--rebuild" in payload["error"]["remediation"]


class TestMissingIsRefused:
    def test_a_repository_with_no_signatures_cannot_be_diagnosed(self, session, synced_repo):
        """An empty corpus is refused for the same reason a stale one is."""
        from repo_troubleshooter.relations.signatures import SignatureState

        empty = SignatureState(rows=0, stored_version=None)
        assert not empty.ok
        message = empty.remediation(REPO)
        assert "no symptom signatures" in message
        assert "signatures" in message
