"""subject feature kind

Revision ID: aa0741b361f3
Revises: f13e71155147
Create Date: 2026-09-01 20:59:45.676401
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "aa0741b361f3"
down_revision: str | None = "f13e71155147"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KINDS_NEW = "'subject','error','structural','behavior','component','cause'"
KINDS_OLD = "'error','structural','behavior','component','cause'"


def upgrade() -> None:
    # Alembic does not autogenerate CHECK changes; explicit is better here anyway.
    op.drop_constraint("ck_symptom_signature_kind", "symptom_signature", type_="check")
    op.create_check_constraint(
        "ck_symptom_signature_kind",
        "symptom_signature",
        f"feature_kind IN ({KINDS_NEW})",
    )


def downgrade() -> None:
    op.execute("DELETE FROM symptom_signature WHERE feature_kind = 'subject'")
    op.drop_constraint("ck_symptom_signature_kind", "symptom_signature", type_="check")
    op.create_check_constraint(
        "ck_symptom_signature_kind",
        "symptom_signature",
        f"feature_kind IN ({KINDS_OLD})",
    )
