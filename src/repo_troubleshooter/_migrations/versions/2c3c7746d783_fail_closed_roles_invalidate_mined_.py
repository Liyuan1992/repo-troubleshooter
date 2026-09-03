"""fail closed roles invalidate mined signatures

Revision ID: 2c3c7746d783
Revises: bc31fb45e972
Create Date: 2026-09-02 19:57:13.567302
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2c3c7746d783"
down_revision: str | None = "bc31fb45e972"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS_NEW = (
    "'subject_package','subject_dependency','subject_confirmed_non_primary',"
    "'subject_unresolved','subject_path','subject_builtin','subject_module',"
    "'error','structural','behavior','component','cause'"
)
KINDS_OLD = (
    "'subject_package','subject_dependency','subject_mentioned','subject_path',"
    "'subject_builtin','subject_module','error','structural','behavior','component','cause'"
)


def upgrade() -> None:
    """`mentioned` splits into `confirmed_non_primary` and `unresolved_subject`.

    The old kind conflated "the report says this one is fine" with "we could not
    tell", and only the first is safe to ignore. Rows mined under the old
    meaning are deleted and the extractor version cleared, so
    `require_fresh_signatures` refuses to diagnose until
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
