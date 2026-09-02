"""subject roles invalidate mined signatures

Revision ID: 471cfb94a866
Revises: 30d2148ed27b
Create Date: 2026-09-02 12:55:55.681350
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "471cfb94a866"
down_revision: str | None = "30d2148ed27b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS_NEW = (
    "'subject_package','subject_path','subject_dependency','subject_builtin',"
    "'subject_module','error','structural','behavior','component','cause'"
)
KINDS_OLD = "'subject_strong','subject_weak','error','structural','behavior','component','cause'"


def upgrade() -> None:
    """Force another rebuild: subjects now carry a role.

    `subject_strong` conflated packages, paths and runtime builtins, which let a
    shared `node:path` cancel a conflict between two different packages. The
    roles are now separate kinds, so every previously mined row is meaningless
    to the new gate. They are deleted and the recorded extractor version is
    cleared, which makes `require_fresh_signatures` refuse to diagnose until
    `repo-troubleshooter signatures <repo> --rebuild` has run.
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
