"""claim strength follows target source

Revision ID: 4b4507bd30a7
Revises: 8f2c496d97c2
Create Date: 2026-09-03 18:41:02.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "4b4507bd30a7"
down_revision: str | None = "8f2c496d97c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extractor 12 -> 13: how firmly a claim attaches is decided differently.

    An unread claim's strength now follows where its target came from - the
    clause naming the package, a reference back to one, or a guess at the
    nearest mention - instead of whether its subject matched a pronoun list. A
    relation verb only exempts a clause when it is the main predicate, and an
    inline span is read as quoted prose rather than skipped as code. Package
    states mined under the old rules differ, so the rows go and the recorded
    version is cleared: an installation that kept them would compare live
    queries against candidate features built with the old semantics.
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
