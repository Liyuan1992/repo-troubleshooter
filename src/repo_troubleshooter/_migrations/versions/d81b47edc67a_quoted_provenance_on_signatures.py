"""quoted provenance on signatures

Revision ID: d81b47edc67a
Revises: 02fc7f6885ae
Create Date: 2026-09-04 10:04:11.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from repo_troubleshooter.store.signature_invalidation import invalidate_signatures

revision: str = "d81b47edc67a"
down_revision: str | None = "02fc7f6885ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extractor 14 -> 15: quotation is recorded on the candidate side too.

    A mined feature now carries whether the source text evidences it only
    inside quoted material - a fence, a `>` reply, an indented block. Without
    the column the check was one-sided: a query that quoted an old ticket was
    refused, while an upstream thread that quoted one could still identify a
    query on the strength of what it had quoted.

    Blockquotes and indented blocks also became quotation in this revision, and
    a clause whose subject is a package is no longer read as wiring, so what
    each object mines differs. The rows go.
    """
    op.add_column(
        "symptom_signature",
        sa.Column("quoted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    invalidate_signatures()


def downgrade() -> None:
    invalidate_signatures()
    op.drop_column("symptom_signature", "quoted")
