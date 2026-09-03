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
    """A session that always rolls back.

    The suite runs against the same database the tool has synced, and reads
    real upstream data out of it. A test that commits changes what every later
    test - and every later measurement quoted in the status document - sees. One
    that ran a one-object build overwrote the recorded signature counts, which
    is how a `14 / 0 / 1` build ended up standing where a full rebuild's numbers
    belonged.
    """
    with session_scope() as sess:
        try:
            yield sess
        finally:
            sess.rollback()


#: Tables holding synced upstream data and the bookkeeping over it. A test run
#: must leave every one of them exactly as it found it.
GUARDED_TABLES = (
    "repository",
    "source_object",
    "object_revision",
    "content_unit",
    "relation_assertion",
    "symptom_signature",
    "release",
    "release_containment",
    "git_commit",
    "package_manifest",
    "incident_resolution_record",
)


def _database_snapshot() -> dict[str, object]:
    """Row counts, plus every field of `sync_state` that a build writes."""
    from sqlalchemy import text

    with session_scope() as sess:
        counts = {
            table: sess.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
            for table in GUARDED_TABLES
        }
        rows = sess.execute(
            text(
                "SELECT repo_id, source, status, objects_seen, stats::text,"
                "       last_success_at, last_attempt_at, watermark, cursor"
                "  FROM sync_state ORDER BY repo_id, source"
            )
        ).all()
    return {"counts": counts, "sync_state": [tuple(map(str, row)) for row in rows]}


@pytest.fixture(scope="session", autouse=True)
def database_is_read_only(request):  # noqa: ANN001, ANN201
    """Fail the run if the suite changed the synced database.

    An acceptance gate the reviewer asked for, and the reason is not tidiness:
    the numbers in the status document are read out of this database, so a test
    that writes to it makes the document describe the test run instead of the
    build.
    """
    health = schema_health()
    if not health.ok:
        yield None
        return
    before = _database_snapshot()
    yield None
    after = _database_snapshot()
    changed = [key for key in before if before[key] != after[key]]
    if changed:
        details = "\n".join(
            f"  {key}:\n    before {before[key]}\n    after  {after[key]}" for key in changed
        )
        raise AssertionError(f"the test run modified the synced database:\n{details}")


@pytest.fixture()
def synced_repo(session):  # noqa: ANN001, ANN201
    repo = session.scalar(select(Repository))
    if repo is None:
        pytest.skip("no repository synced; run `rt sync deepseek-harness` first")
    return repo
