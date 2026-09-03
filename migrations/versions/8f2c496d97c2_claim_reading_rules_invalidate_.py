"""claim reading rules invalidate signatures

Revision ID: 8f2c496d97c2
Revises: 2d25dafd065c
Create Date: 2026-09-03 15:30:48.738413
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8f2c496d97c2"
down_revision: str | None = "2d25dafd065c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extractor 11 -> 12: how claims are read and bound changed again.

    Claims are now parsed before a clause is judged to be code, relation
    statements no longer produce unread claims, and `COPULA_RE` matches a real
    word boundary. Package states mined under the old rules differ, so the rows
    are deleted and the recorded version cleared. Without this an existing
    installation would consider its signatures fresh and keep serving candidate
    features built with the old semantics.
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
