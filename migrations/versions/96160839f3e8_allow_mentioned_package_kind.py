"""allow mentioned package kind

Revision ID: 96160839f3e8
Revises: f10b0a5f098e
Create Date: 2026-09-02 16:27:20.578853
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "96160839f3e8"
down_revision: str | None = "f10b0a5f098e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS_NEW = (
    "'subject_package','subject_dependency','subject_mentioned','subject_path',"
    "'subject_builtin','subject_module','error','structural','behavior','component','cause'"
)
KINDS_OLD = (
    "'subject_package','subject_path','subject_dependency','subject_builtin',"
    "'subject_module','error','structural','behavior','component','cause'"
)


def upgrade() -> None:
    """A package named with no cue either way gets its own kind.

    Alembic does not autogenerate CHECK changes, and the role rework added
    `subject_mentioned`: a package the report names without saying it failed or
    that anything uses it. It helps retrieval and can never veto.
    """
    op.drop_constraint("ck_symptom_signature_kind", "symptom_signature", type_="check")
    op.create_check_constraint(
        "ck_symptom_signature_kind",
        "symptom_signature",
        f"feature_kind IN ({KINDS_NEW})",
    )


def downgrade() -> None:
    op.execute("DELETE FROM symptom_signature WHERE feature_kind = 'subject_mentioned'")
    op.drop_constraint("ck_symptom_signature_kind", "symptom_signature", type_="check")
    op.create_check_constraint(
        "ck_symptom_signature_kind",
        "symptom_signature",
        f"feature_kind IN ({KINDS_OLD})",
    )
