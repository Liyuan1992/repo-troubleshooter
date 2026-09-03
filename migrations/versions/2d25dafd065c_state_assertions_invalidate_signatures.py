"""state assertions invalidate signatures

Revision ID: 2d25dafd065c
Revises: ffd418b9a4ea
Create Date: 2026-09-03 08:34:21.815036
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2d25dafd065c"
down_revision: str | None = "ffd418b9a4ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Condition claims are now bound across sentences.

    A claim like "It crashes." in the next sentence is attached to the package
    it refers to, when the antecedent is unique, and recorded as unbound when it
    is not. Package states mined before this change were read only inside each
    mention's own window, so they are wrong now. Rows are deleted and the
    extractor version cleared.
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
