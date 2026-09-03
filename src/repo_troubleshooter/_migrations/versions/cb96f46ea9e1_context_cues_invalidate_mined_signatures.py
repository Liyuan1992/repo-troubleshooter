"""context cues invalidate mined signatures

Revision ID: cb96f46ea9e1
Revises: 96160839f3e8
Create Date: 2026-09-02 17:08:02.862081
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "cb96f46ea9e1"
down_revision: str | None = "96160839f3e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Force a rebuild: negation and health cues changed what a role means.

    A mention classified by the previous extractor could be `primary` because a
    bare "does not" stood next to it, or despite the report saying the package
    was healthy. Those rows now mean something different, so they are deleted
    and the recorded extractor version cleared - which makes
    `require_fresh_signatures` refuse to diagnose until
    `repo-troubleshooter signatures <repo> --rebuild` has run.
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
