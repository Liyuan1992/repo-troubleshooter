"""HTTP routes identify incidents.

Revision ID: a71c9e1b4d22
Revises: d81b47edc67a
Create Date: 2026-09-04 20:37:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from repo_troubleshooter.store.signature_invalidation import invalidate_signatures

revision: str = "a71c9e1b4d22"
down_revision: str | None = "d81b47edc67a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extractor 15 -> 16: API routes such as /metrics are structural."""
    invalidate_signatures()


def downgrade() -> None:
    invalidate_signatures()
