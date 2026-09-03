"""conflicted subject role invalidates signatures

Revision ID: f55ff3fee9f0
Revises: 2c3c7746d783
Create Date: 2026-09-02 22:26:50.837060
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f55ff3fee9f0"
down_revision: str | None = "2c3c7746d783"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS_NEW = (
    "'subject_package','subject_dependency','subject_confirmed_non_primary',"
    "'subject_conflicted','subject_unresolved','subject_path','subject_builtin',"
    "'subject_module','error','structural','behavior','component','cause'"
)
KINDS_OLD = (
    "'subject_package','subject_dependency','subject_confirmed_non_primary',"
    "'subject_unresolved','subject_path','subject_builtin','subject_module',"
    "'error','structural','behavior','component','cause'"
)


def upgrade() -> None:
    """`conflicted_subject` splits out of `unresolved`, and roles were reordered.

    A predicate stated before a mention now outranks a dependency cue, and a
    bare `name@version` no longer becomes a dependency without an install
    context. Rows mined under the old rules mean something different, so they
    are deleted and the extractor version cleared: `require_fresh_signatures`
    then refuses to diagnose until a rebuild has run.
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
