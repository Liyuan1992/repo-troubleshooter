"""two axis package facts invalidate signatures

Revision ID: ffd418b9a4ea
Revises: f55ff3fee9f0
Create Date: 2026-09-03 06:50:31.775508
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "ffd418b9a4ea"
down_revision: str | None = "f55ff3fee9f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """A package mention now carries a relation *and* a state.

    `dependency + healthy` used to collapse to `dependency`, destroying the
    health fact before it reached the gate. Mentions are also aggregated by
    canonical package name, so a package called healthy in one sentence and
    blamed in another becomes a contradiction rather than two separate facts.
    Rows mined under the old model mean something different, so they are deleted
    and the extractor version cleared.
    """
    op.execute("DELETE FROM symptom_signature")
    op.execute(
        "UPDATE sync_state SET stats = stats - 'extractor_version' WHERE source = 'signatures'"
    )


def downgrade() -> None:
    op.execute("DELETE FROM symptom_signature")
    op.execute(
        "UPDATE sync_state SET stats = stats - 'extractor_version' WHERE source = 'signatures'"
    )
