"""Invalidating mined signatures, in one place.

Every change to how claims are read makes the stored features incomparable with
the ones a live query produces, so the rows have to go and the recorded
extractor version has to be cleared. Doing only that leaves ``sync_state``
saying the source is ``complete`` with the row counts of the rows just deleted,
so ``rt status`` reports a finished build over an empty table. The bookkeeping
is part of the invalidation, not an afterthought.
"""

from __future__ import annotations

from alembic import op

#: Everything a signature build records. All of it describes rows that a
#: version bump has just deleted.
BUILD_STATS = (
    "extractor_version",
    "rows_attempted",
    "rows_inserted",
    "rows_stored_total",
    "rows_written",
    "objects",
    "skipped_empty",
    "passes",
)


def invalidate_signatures() -> None:
    """Delete every mined signature and mark the source as needing a rebuild."""
    op.execute("DELETE FROM symptom_signature")
    dropped = " ".join(f"- '{key}'" for key in BUILD_STATS)
    op.execute(
        f"""
        UPDATE sync_state
           SET stats = (stats {dropped}),
               status = 'stale',
               objects_seen = 0
         WHERE source = 'signatures'
        """  # noqa: S608 - BUILD_STATS is a module constant, not input
    )
