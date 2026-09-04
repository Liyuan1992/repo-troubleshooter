"""The built wheel has to work, not just import.

A clean install used to pass `--help` and `--check` and then fail on the first
command that touched the database: migrations and repository profiles were
looked up beside the source checkout, which an installed wheel does not have.
`--help` could never have caught that, so this gate builds the wheel, installs
it into a virtual environment of its own, and makes it do real work.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.db, pytest.mark.live, pytest.mark.slow]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO = "deepseek-ai/deepseek-harness"
REAL_SYMPTOM = (
    "dsh web starts but __DSH_BOOT__ has zero entries and zero batches; "
    "client-modules reports HTML did not preload "
    "@deepseek-ai/dsh-client-modules/client.js, and the host throws "
    "TypeError: e.indexOf is not a function"
)


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.fixture(scope="module")
def installed_wheel() -> Path:
    """Build the wheel and install it into a virtual environment of its own.

    The environment goes in a short path on purpose: Windows still refuses to
    open files past 260 characters, and a deep scratch directory turns a
    working install into an `ImportError` that looks like a packaging bug.
    """
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")

    dist = PROJECT_ROOT / "dist"
    build = _run([uv, "build", "--out-dir", str(dist)], cwd=str(PROJECT_ROOT))
    assert build.returncode == 0, build.stdout + build.stderr
    wheels = sorted(dist.glob("repo_troubleshooter-*.whl"))
    assert wheels, "uv build produced no wheel"

    root = Path(tempfile.mkdtemp(prefix="rt-wheel-", dir=tempfile.gettempdir()))
    venv = root / "v"
    created = _run([uv, "venv", str(venv)])
    assert created.returncode == 0, created.stdout + created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install = _run([uv, "pip", "install", "--python", str(python), str(wheels[-1])])
    assert install.returncode == 0, install.stdout + install.stderr

    yield venv / ("Scripts" if os.name == "nt" else "bin")
    shutil.rmtree(root, ignore_errors=True)


class TestAnInstalledWheelCanWork:
    def test_it_ships_its_migrations_and_profiles(self, installed_wheel: Path):
        """Resources are located inside the package, wherever it is installed."""
        python = installed_wheel / ("python.exe" if os.name == "nt" else "python")
        probe = _run(
            [
                str(python),
                "-c",
                "from repo_troubleshooter.store.migrate import migrations_dir;"
                "from repo_troubleshooter.config import get_settings;"
                "print((migrations_dir() / 'versions').is_dir());"
                "print(get_settings().profiles_dir.is_dir())",
            ]
        )
        assert probe.returncode == 0, probe.stdout + probe.stderr
        assert probe.stdout.split() == ["True", "True"], probe.stdout

    def test_db_init_runs_the_migrations(self, installed_wheel: Path):
        """The command that failed with `Path doesn't exist: ...\\Lib\\migrations`."""
        exe = installed_wheel / (
            "repo-troubleshooter.exe" if os.name == "nt" else "repo-troubleshooter"
        )
        result = _run([str(exe), "db", "init"])
        assert result.returncode == 0, result.stdout + result.stderr
        assert "revision" in result.stdout.lower()

    def test_diagnose_answers_from_the_installed_wheel(self, installed_wheel: Path):
        exe = installed_wheel / (
            "repo-troubleshooter.exe" if os.name == "nt" else "repo-troubleshooter"
        )
        result = _run(
            [
                str(exe),
                "diagnose",
                "--repo",
                REPO,
                "--json",
                # Read-only: this gate runs against the tool's real database.
                "--no-persist",
                "--error",
                REAL_SYMPTOM,
                "--version",
                "0.1.2-alpha.1",
            ]
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["incident"]["matched"] is True
        assert payload["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"

    def test_the_mcp_server_answers_from_the_installed_wheel(self, installed_wheel: Path):
        """`database_unavailable` was what the missing migrations looked like."""
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        exe = installed_wheel / (
            "repo-troubleshooter-mcp.exe" if os.name == "nt" else "repo-troubleshooter-mcp"
        )

        async def call() -> dict:
            params = StdioServerParameters(command=str(exe), args=[], env=dict(os.environ))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "diagnose",
                        {"repo": REPO, "error": REAL_SYMPTOM, "core_version": "0.1.2-alpha.1"},
                    )
                    payload = result.structured_content
                    if payload is None:
                        blocks = [b for b in result.content if getattr(b, "type", None) == "text"]
                        payload = json.loads(blocks[0].text)
                    return payload

        payload = asyncio.run(call())
        assert payload["ok"] is True, payload
        answer = payload["result"]
        assert answer["incident"]["matched"] is True
        assert answer["recommended_action"]["target"] == "dsh-v0.1.2-alpha.2"
