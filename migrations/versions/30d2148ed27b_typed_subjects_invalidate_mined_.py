"""typed subjects invalidate mined signatures

Revision ID: 30d2148ed27b
Revises: aa0741b361f3
Create Date: 2026-09-02 06:46:54.253943
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "30d2148ed27b"
down_revision: str | None = "aa0741b361f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS_NEW = "'subject_strong','subject_weak','error','structural','behavior','component','cause'"
KINDS_OLD = "'subject','error','structural','behavior','component','cause'"


def upgrade() -> None:
    """Force a signature rebuild.

    Subjects are now typed (`subject_strong` / `subject_weak`) and module names
    need proof, so every row mined by the previous extractor means something
    different from what this build would mine. Keeping them would silently
    corrupt identity decisions, so they are deleted and the recorded extractor
    version is cleared - which makes `require_fresh_signatures` refuse to
    diagnose until `repo-troubleshooter signatures <repo> --rebuild` has run.
    """
    op.drop_constraint("ck_symptom_signature_kind", "symptom_signature", type_="check")
    op.execute("DELETE FROM symptom_signature")
    op.execute(
        "UPDATE sync_state SET stats = stats - 'extractor_version' WHERE source = 'signatures'"
    )
    op.create_check_constraint(
        "ck_symptom_signature_kind",
        "symptom_signature",
        f"feature_kind IN ({KINDS_NEW})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_symptom_signature_kind", "symptom_signature", type_="check")
    op.execute("DELETE FROM symptom_signature")
    op.execute(
        "UPDATE sync_state SET stats = stats - 'extractor_version' WHERE source = 'signatures'"
    )
    op.create_check_constraint(
        "ck_symptom_signature_kind",
        "symptom_signature",
        f"feature_kind IN ({KINDS_OLD})",
    )
