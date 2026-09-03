"""cue scope invalidates mined signatures

Revision ID: bc31fb45e972
Revises: cb96f46ea9e1
Create Date: 2026-09-02 18:51:46.830870
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "bc31fb45e972"
down_revision: str | None = "cb96f46ea9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Force a rebuild: cue scope and negation semantics changed.

    Previously a health cue was searched for anywhere in a fixed character
    window, so one package's `is healthy` could describe the next mention, and
    `is not working` was read as `working`. Roles mined under those rules mean
    something different from what this build would mine, so the rows are deleted
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
