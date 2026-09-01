"""Fresh migration + idempotent replay.

Creates a throwaway database, migrates it from zero, then ingests the same
source objects twice and asserts that nothing duplicates: one object, one
revision per distinct body, one content unit per chunk. This is the property
that makes an interrupted sync safe to re-run.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from repo_troubleshooter.config import get_settings
from repo_troubleshooter.store.migrate import alembic_config
from repo_troubleshooter.store.models import (
    ContentUnit,
    ObjectRevision,
    RelationAssertion,
    Release,
    SourceObject,
)
from repo_troubleshooter.sync import upsert

pytestmark = pytest.mark.db

BODY = """Startup fails on this build.

```
Error: Service `llm` not found
    at Registry.get (C:\\Users\\someone\\project\\foo.ts:137:11)
```

Config:

```json
{"plugins": {"foo": {"version": "1.4"}}}
```
"""

EDITED_BODY = BODY + "\nEdit: also happens with the plugin disabled.\n"


@pytest.fixture(scope="module")
def fresh_database(db_ready):  # noqa: ANN001, ANN201
    """A new database, migrated from zero by Alembic."""
    from alembic import command

    settings = get_settings()
    name = f"rt_test_{uuid.uuid4().hex[:10]}"
    admin_url = settings.database_url.rsplit("/", 1)[0] + "/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    target_url = settings.database_url.rsplit("/", 1)[0] + f"/{name}"
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", target_url)
    command.upgrade(config, "head")

    engine = create_engine(target_url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def ingest(session: Session, repo_id: int, body: str) -> None:
    """One full ingest pass, exactly as the sync orchestrator does it."""
    obj = upsert.upsert_source_object(
        session,
        repo_id=repo_id,
        kind="discussion",
        native_id="D_test_1",
        number=42,
        title="Service llm not found after upgrade",
        url="https://example.invalid/discussions/42",
        source_created_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
        source_updated_at=dt.datetime(2026, 8, 2, tzinfo=dt.UTC),
    )
    revision, changed = upsert.record_revision(
        session, obj=obj, body=body, source_updated_at=dt.datetime(2026, 8, 2, tzinfo=dt.UTC)
    )
    if changed:
        upsert.rebuild_content_units(session, repo_id=repo_id, obj=obj, revision=revision)
    upsert.assert_relation(
        session,
        repo_id=repo_id,
        relation_type="REFERENCES",
        src_object_id=obj.id,
        dst_ref="commit:0a53fb55bea101816fa226bb964ae2bed71c343b",
        derivation="text_explicit",
        evidence={"raw": "0a53fb55be"},
    )
    upsert.upsert_release(
        session,
        repo_id=repo_id,
        tag_name="dsh-v0.1.2-alpha.2",
        version_norm="0.1.2a2",
        is_prerelease=True,
        commit_sha="0a53fb55bea101816fa226bb964ae2bed71c343b",
    )
    session.commit()


class TestFreshMigration:
    def test_schema_is_created_from_zero(self, fresh_database):
        with fresh_database.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
        assert {
            "repository",
            "source_object",
            "content_unit",
            "incident_resolution_record",
        } <= tables

    def test_extensions_present(self, fresh_database):
        with fresh_database.connect() as conn:
            extensions = {row[0] for row in conn.execute(text("SELECT extname FROM pg_extension"))}
        assert {"pg_trgm", "vector"} <= extensions


class TestIdempotentReplay:
    def test_replaying_the_same_source_changes_nothing(self, fresh_database):
        with Session(fresh_database) as session:
            repo = upsert.upsert_repository(session, owner="acme", name="widget")
            session.commit()
            repo_id = repo.id

            ingest(session, repo_id, BODY)
            counts_first = self._counts(session, repo_id)

            ingest(session, repo_id, BODY)
            counts_second = self._counts(session, repo_id)

        assert counts_first == counts_second, (
            f"replay changed counts: {counts_first} -> {counts_second}"
        )
        assert counts_first["objects"] == 1
        assert counts_first["revisions"] == 1
        assert counts_first["releases"] == 1
        assert counts_first["relations"] == 1
        assert counts_first["units"] >= 3  # prose + log + config

    def test_an_upstream_edit_adds_a_revision_without_duplicating_the_object(self, fresh_database):
        with Session(fresh_database) as session:
            repo = upsert.upsert_repository(session, owner="acme", name="widget2")
            session.commit()
            repo_id = repo.id

            ingest(session, repo_id, BODY)
            before = self._counts(session, repo_id)

            ingest(session, repo_id, EDITED_BODY)
            after = self._counts(session, repo_id)

            current = session.scalars(
                select(ObjectRevision)
                .join(SourceObject, SourceObject.id == ObjectRevision.object_id)
                .where(SourceObject.repo_id == repo_id, ObjectRevision.is_current.is_(True))
            ).all()

        assert after["objects"] == before["objects"] == 1
        assert after["revisions"] == before["revisions"] + 1
        assert len(current) == 1, "exactly one revision may be current"
        assert "plugin disabled" in current[0].body

    @staticmethod
    def _counts(session: Session, repo_id: int) -> dict[str, int]:
        def count(model, where) -> int:  # noqa: ANN001
            return session.scalar(select(func.count()).select_from(model).where(where)) or 0

        return {
            "objects": count(SourceObject, SourceObject.repo_id == repo_id),
            "revisions": session.scalar(
                select(func.count())
                .select_from(ObjectRevision)
                .join(SourceObject, SourceObject.id == ObjectRevision.object_id)
                .where(SourceObject.repo_id == repo_id)
            )
            or 0,
            "units": count(ContentUnit, ContentUnit.repo_id == repo_id),
            "releases": count(Release, Release.repo_id == repo_id),
            "relations": count(RelationAssertion, RelationAssertion.repo_id == repo_id),
        }
