# Curator state ledger — the repository (DAO) over the `items` table.
# This is the load-bearing correctness module: upsert_gap() is the STATE-02 dedup
# primitive. Its ON CONFLICT(arr_app, arr_id) clause refreshes metadata + last_seen_at
# ONLY and CRITICALLY never touches `status` (nor discovered_at) — an item already
# acted on (imported/searching) still shows up in the *arr wanted/cutoff lists; if a
# re-detect upsert reset its status to 'pending', Curator would re-act on a satisfied/
# in-flight item, the #1 STATE-02 pitfall (RESEARCH Pitfall 1).
#
# Security: ALL values are bound via `?` placeholders — never f-string interpolation. The
# status CHECK constraint (schema.sql) rejects bad enum values at the DB layer. [T-02-03]
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, List, Optional


def _now_iso() -> str:
    """ISO8601 UTC timestamp (Z-suffixed) for discovered_at / last_seen_at."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_gap(conn: sqlite3.Connection, item: Any) -> None:
    """Insert a freshly-detected gap as 'pending', or refresh an already-tracked one.

    Dedup is structural: UNIQUE(arr_app, arr_id) + ON CONFLICT DO UPDATE means re-running
    detection on the same *arr identity NEVER grows a second row (STATE-02). The SET clause
    deliberately OMITS `status` and `discovered_at` so a first-seen timestamp and any
    lifecycle progress an item has made (searching/grabbed/.../imported) survive a re-detect
    (RESEARCH Pitfall 1 — the load-bearing STATE-02 rule).

    `item` is duck-typed (a GapItem-shaped object): reads arr_app, arr_id, kind, gap_type,
    title, artist_or_author, foreign_id, quality_profile_id, raw — so the state layer stays
    free of any adapter import (the firewall runs both directions).
    """
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO items (arr_app, arr_id, kind, gap_type, title, artist_or_author,
                           foreign_id, quality_profile_id, status,
                           discovered_at, last_seen_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        ON CONFLICT(arr_app, arr_id) DO UPDATE SET
            gap_type           = excluded.gap_type,
            title              = excluded.title,
            artist_or_author   = excluded.artist_or_author,
            foreign_id         = excluded.foreign_id,
            quality_profile_id = excluded.quality_profile_id,
            last_seen_at       = excluded.last_seen_at,
            raw_json           = excluded.raw_json
        -- NEVER overwrite `status` or `discovered_at` on conflict (STATE-02 / Pitfall 1):
        -- an acted-on/first-seen row must keep its lifecycle status and original sighting.
        """,
        (
            item.arr_app,
            item.arr_id,
            item.kind,
            item.gap_type,
            item.title,
            item.artist_or_author,
            item.foreign_id,
            item.quality_profile_id,
            now,
            now,
            json.dumps(item.raw),
        ),
    )


def get_gap(conn: sqlite3.Connection, arr_app: str, arr_id: str) -> Optional[sqlite3.Row]:
    """Return the ledger row for a stable *arr identity, or None if untracked."""
    return conn.execute(
        "SELECT * FROM items WHERE arr_app = ? AND arr_id = ?",
        (arr_app, arr_id),
    ).fetchone()


def set_status(conn: sqlite3.Connection, arr_app: str, arr_id: str, status: str) -> None:
    """Transition an item's lifecycle status (the only mutator Phase 2 implements).

    The value is bound via `?`; an out-of-enum status is rejected by the schema CHECK
    constraint (raising sqlite3.IntegrityError). The search->import transitions that drive
    this are Phases 4-5; Phase 2 only proves the column round-trips.
    """
    conn.execute(
        "UPDATE items SET status = ? WHERE arr_app = ? AND arr_id = ?",
        (status, arr_app, arr_id),
    )


def list_by_status(conn: sqlite3.Connection, status: str) -> List[sqlite3.Row]:
    """Return all ledger rows currently in the given lifecycle status."""
    return conn.execute(
        "SELECT * FROM items WHERE status = ?",
        (status,),
    ).fetchall()
