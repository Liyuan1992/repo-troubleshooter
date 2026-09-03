"""Alembic driver and schema guard.

Alembic is the single authority for schema shape. ``rt db init`` runs migrations
rather than ``create_all`` so a developer database and a deployed one cannot
diverge.

The guard exists because of a real incident: a sibling project on this machine
ran its own migrations into a PostgreSQL instance this project was sharing, and
silently replaced the schema. Every command that touches the database now checks
that the database it reached is actually this project's, and says exactly how to
fix it when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from repo_troubleshooter.config import PACKAGE_ROOT, get_settings
from repo_troubleshooter.store.db import get_engine

# A column no other artifact's schema has. Cheap, decisive ownership signal.
SIGNATURE_TABLE = "repository"
SIGNATURE_COLUMN = "host"


class SchemaMismatch(RuntimeError):
    """The reachable database is not this project's database."""


def migrations_dir() -> Path:
    """The migration scripts, as shipped inside the package."""
    return PACKAGE_ROOT / "_migrations"


def alembic_config() -> Config:
    """Alembic configured from the installed package.

    Built in memory rather than from `alembic.ini`: the ini file is a
    developer convenience at the top of the checkout, and an installed wheel
    has no checkout. The only options that matter are where the scripts are and
    which database to talk to, and both are known here.
    """
    config = Config()
    config.set_main_option("script_location", str(migrations_dir()))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


def known_revisions() -> set[str]:
    script = ScriptDirectory.from_config(alembic_config())
    return {rev.revision for rev in script.walk_revisions()}


def head_revision() -> str | None:
    script = ScriptDirectory.from_config(alembic_config())
    return script.get_current_head()


def current_revision() -> str | None:
    inspector = inspect(get_engine())
    if "alembic_version" not in inspector.get_table_names():
        return None
    with get_engine().connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


@dataclass
class SchemaHealth:
    reachable: bool
    empty: bool
    owned: bool
    revision: str | None
    head: str | None
    tables: int
    detail: str

    @property
    def ok(self) -> bool:
        return self.reachable and self.owned and self.revision == self.head

    def remediation(self) -> str:
        url = get_settings().database_url
        if not self.reachable:
            return (
                f"cannot reach PostgreSQL at {url}\n"
                "  start it with:  docker compose up -d\n"
                "  this project uses its own container (rt-claude-postgres) on 127.0.0.1:55447"
            )
        if self.empty:
            return f"database at {url} is empty\n  initialise it with:  rt db init"
        if not self.owned:
            return (
                f"the database at {url} holds a DIFFERENT project's schema "
                f"({self.tables} tables, no {SIGNATURE_TABLE}.{SIGNATURE_COLUMN} column).\n"
                "  another artifact is sharing this database. This project must use its own:\n"
                "    docker compose up -d           # rt-claude-postgres, 127.0.0.1:55447\n"
                "    RT_DATABASE_URL=postgresql+psycopg://rt_claude:rt_claude"
                "@127.0.0.1:55447/rt_claude\n"
                "  then:  rt db init"
            )
        return (
            f"schema revision {self.revision} != head {self.head}\n"
            "  bring it up to date with:  rt db init"
        )


def schema_health() -> SchemaHealth:
    try:
        inspector = inspect(get_engine())
        tables = set(inspector.get_table_names())
    except SQLAlchemyError as exc:
        return SchemaHealth(False, False, False, None, head_revision(), 0, str(exc))

    if not tables:
        return SchemaHealth(True, True, False, None, head_revision(), 0, "empty database")

    owned = False
    if SIGNATURE_TABLE in tables:
        columns = {c["name"] for c in inspector.get_columns(SIGNATURE_TABLE)}
        owned = SIGNATURE_COLUMN in columns

    revision = current_revision()
    head = head_revision()
    if owned and revision is not None and revision not in known_revisions():
        owned = False  # our table shape, but a revision this codebase does not know

    return SchemaHealth(
        reachable=True,
        empty=False,
        owned=owned,
        revision=revision,
        head=head,
        tables=len(tables),
        detail="ok" if owned and revision == head else "schema check failed",
    )


def require_schema() -> SchemaHealth:
    health = schema_health()
    if not health.ok:
        raise SchemaMismatch(health.remediation())
    return health


def upgrade_to_head() -> str:
    """Bring the database to head. Returns what was done.

    A database holding our tables but no ``alembic_version`` (an early
    ``create_all``) is stamped rather than migrated. A database holding some
    other project's schema is refused outright - migrating on top of it is how
    the original collision happened.
    """
    inspector = inspect(get_engine())
    tables = set(inspector.get_table_names())
    config = alembic_config()

    if tables and SIGNATURE_TABLE in tables:
        columns = {c["name"] for c in inspector.get_columns(SIGNATURE_TABLE)}
        if SIGNATURE_COLUMN not in columns:
            raise SchemaMismatch(schema_health().remediation())

    if "alembic_version" not in tables and SIGNATURE_TABLE in tables:
        command.stamp(config, "head")
        return "stamped existing schema at head"

    command.upgrade(config, "head")
    return "upgraded to head"


def migration_scripts_dir() -> Path:
    return migrations_dir() / "versions"
