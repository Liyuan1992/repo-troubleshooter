"""widen feature kind column

Revision ID: ef5cca6e9e3f
Revises: 471cfb94a866
Create Date: 2026-09-02 12:57:19.769017
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ef5cca6e9e3f"
down_revision: str | None = "471cfb94a866"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """`subject_dependency` is 18 characters; the column held 16."""
    op.alter_column(
        "symptom_signature",
        "feature_kind",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM symptom_signature WHERE length(feature_kind) > 16")
    op.alter_column(
        "symptom_signature",
        "feature_kind",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
