"""quotation regions and predicate position

Revision ID: 02fc7f6885ae
Revises: 4b4507bd30a7
Create Date: 2026-09-03 21:12:44.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from repo_troubleshooter.store.signature_invalidation import invalidate_signatures

revision: str = "02fc7f6885ae"
down_revision: str | None = "4b4507bd30a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extractor 13 -> 14: what a clause asserts, and where it asserts it.

    Predicate position is decided by morphology rather than by counting words;
    a predicate we cannot classify is recorded as a claim instead of dropped;
    a package named inside a fenced block is quoted material and no longer a
    subject of the report. Signatures mined under the old rules differ.

    This also repairs the bookkeeping the previous two invalidations left
    behind: they deleted the rows and cleared the version but left `sync_state`
    reading `complete` with the old row counts, so `rt status` reported a
    finished build over an empty table.
    """
    invalidate_signatures()


def downgrade() -> None:
    invalidate_signatures()
