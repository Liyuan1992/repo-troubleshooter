"""Shared fixtures.

Tests are split by what they need:

* no marker  - pure logic, no database, no network
* ``db``     - needs this project's PostgreSQL (docker compose up -d)
* ``live``   - needs synced upstream data as well

The ``db`` fixtures refuse to run against a database that is not this project's,
which is the same guard the CLI uses.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from repo_troubleshooter.store.db import session_scope
from repo_troubleshooter.store.migrate import schema_health
from repo_troubleshooter.store.models import Repository


@pytest.fixture(scope="session")
def db_ready() -> bool:
    health = schema_health()
    if not health.ok:
        pytest.skip(f"database not usable for tests: {health.remediation().splitlines()[0]}")
    return True


@pytest.fixture()
def session(db_ready):  # noqa: ANN001, ANN201
    with session_scope() as sess:
        yield sess


@pytest.fixture()
def synced_repo(session):  # noqa: ANN001, ANN201
    repo = session.scalar(select(Repository))
    if repo is None:
        pytest.skip("no repository synced; run `rt sync deepseek-harness` first")
    return repo
