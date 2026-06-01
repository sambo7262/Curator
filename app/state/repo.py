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
from typing import Any, Dict, List, Optional


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


# The dead-end / backed-off retry states a boot re-arm clears (reconcile.rearm_stuck_on_start).
# `stuck`/`quarantined` are already retry-eligible but gated by their backoff `next_attempt_at`;
# `permanently-unavailable` only re-enters after the 30-day dormant TTL. Re-arming nulls the gates so
# a container rebuild re-attempts the whole backlog immediately (owner-driven, testing-friendly).
REARMABLE_STATES = ("stuck", "quarantined", "permanently-unavailable")


def rearm_retryable(conn: sqlite3.Connection) -> int:
    """Reset every stuck/quarantined/permanently-unavailable row to a clean `pending` retry slate.

    Sets status='pending', attempt_count=0, next_attempt_at=NULL so select_eligible picks the row up
    THIS cycle (subject only to the grace window) instead of waiting out an 8h/24h backoff or the
    30-day dormant TTL. Returns the number of rows re-armed. Single UPDATE (autocommit connection);
    the caller serializes it through the shared writer lock (D-16).
    """
    cur = conn.execute(
        "UPDATE items SET status = 'pending', attempt_count = 0, next_attempt_at = NULL"
        " WHERE status IN ('stuck', 'quarantined', 'permanently-unavailable')"
    )
    return cur.rowcount


def record_staged_file(conn: sqlite3.Connection, item_id: int, staging_path: str) -> int:
    """Insert a staged_files row when a download begins; return its rowid (D-05/D-06 anchor).

    `staging_path` is the absolute /data staging dir slskd writes into. In later waves it is
    DERIVED from peer-influenced filenames, so — like every write in this layer — it is bound
    via a `?` placeholder, never f-stringed into SQL (T-04-01 / repo.py security note).
    """
    cur = conn.execute(
        "INSERT INTO staged_files (item_id, staging_path, created_at) VALUES (?, ?, ?)",
        (item_id, staging_path, _now_iso()),
    )
    return cur.lastrowid


def record_quarantine(
    conn: sqlite3.Connection, staged_file_id: int, quarantine_path: str, reason: str
) -> None:
    """D-06: on a terminal/ambiguous failure, stamp the staged_files row with the quarantine
    destination, the human-readable failure reason, and the quarantine timestamp.

    All values are `?`-bound (peer-derived paths never reach SQL as literals — T-04-01)."""
    conn.execute(
        "UPDATE staged_files"
        " SET quarantine_path = ?, failure_reason = ?, quarantined_at = ?"
        " WHERE id = ?",
        (quarantine_path, reason, _now_iso(), staged_file_id),
    )


# ---------------------------------------------------------------------------
# Phase 5 — autonomy DAOs: eligibility select, backoff schedule, attempt mutator,
# and the status-page counters. The scheduler READS/WRITES exclusively through these.
# Every value below is bound via `?` placeholders (never f-stringed) and every timestamp
# flows through _now_iso() so the eligibility comparisons stay lexicographically correct
# against the Z-suffixed ISO8601 strings stored in the ledger. [T-05-02]
# ---------------------------------------------------------------------------

# D-08: exponential retry backoff, capped. attempt 1 -> 1h, attempt 2 -> 6h, attempt 3+ -> 24h.
BACKOFF_SECONDS: List[int] = [3600, 21600, 86400]


def backoff_for(attempt_count: int) -> int:
    """Return the next-retry delay (seconds) for a given attempt count (D-08, capped 1h->6h->24h).

    Pure function: clamps the attempt into [1, len(BACKOFF_SECONDS)] so a 0/negative count maps to
    the first rung and any count past the ladder length stays pinned at the 24h ceiling — it never
    raises and never grows unbounded.
    """
    idx = min(max(attempt_count, 1), len(BACKOFF_SECONDS)) - 1
    return BACKOFF_SECONDS[idx]


def select_eligible(
    conn: sqlite3.Connection,
    grace_cutoff: str,
    now: str,
    dormant_cutoff: str,
    room: int,
) -> List[sqlite3.Row]:
    """Return the items the scheduler may act on THIS cycle, oldest-first, capped at `room`.

    Two eligibility branches (GAP-03 grace + D-08 backoff + D-09 dormant re-check):

    1. A retryable item (`pending`/`stuck`/`quarantined`) is eligible iff its grace window has
       elapsed (`discovered_at <= grace_cutoff`, GAP-03 — the existing ~1493-row backlog is already
       past grace at launch because the upsert never clobbers discovered_at) AND its backoff has
       elapsed (`next_attempt_at IS NULL OR next_attempt_at <= now`, D-08). `stuck` AND `quarantined`
       ARE retry-eligible — that retry IS the backoff mechanism (OQ-2 resolved per D-08).
    2. A `permanently-unavailable` item re-enters the pool once its 30-day dormant TTL has elapsed
       (`last_checked_at IS NULL OR last_checked_at <= dormant_cutoff`, D-09) — a new uploader may
       have appeared.

    Ordered `discovered_at ASC` (oldest gaps drain first, fairly). `LIMIT room` is the per-cycle
    flood control over the backlog (T-05-03): the SQL itself caps the result set so a single cycle
    can never fan out beyond the caller's available concurrency budget.

    Terminal/in-flight states (`imported`/`searching`/`downloading`/`importing`/`unavailable`/
    `blacklisted`/`grabbed`/`downloaded`) are never selected — Curator never re-acts on a satisfied
    or in-flight item.
    """
    return conn.execute(
        """
        SELECT * FROM items
        WHERE (
            status IN ('pending', 'stuck', 'quarantined')
            AND discovered_at <= ?
            AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
        ) OR (
            status = 'permanently-unavailable'
            AND (last_checked_at IS NULL OR last_checked_at <= ?)
        )
        ORDER BY discovered_at ASC
        LIMIT ?
        """,
        (grace_cutoff, now, dormant_cutoff, room),
    ).fetchall()


def record_attempt(
    conn: sqlite3.Connection,
    arr_app: str,
    arr_id: str,
    attempt_count: int,
    next_attempt_at: Optional[str],
    status: str,
) -> None:
    """Stamp an item's attempt counter, backoff gate, status, and last-checked time (D-07/D-08/D-09).

    `last_checked_at` is set to `_now_iso()` (the dormant re-check anchor). The caller computes
    `attempt_count` (+1 on a genuine failure, reset to 0 on import), `next_attempt_at`
    (now + backoff_for(attempt) or the 30-day dormant anchor at give-up), and the resulting `status`.
    All values `?`-bound; an out-of-enum status is rejected by the schema CHECK (IntegrityError).
    """
    conn.execute(
        "UPDATE items"
        " SET attempt_count = ?, next_attempt_at = ?, last_checked_at = ?, status = ?"
        " WHERE arr_app = ? AND arr_id = ?",
        (attempt_count, next_attempt_at, _now_iso(), status, arr_app, arr_id),
    )


def status_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Return {status: count} across the ledger — the header counts for the status page (REL-03)."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM items GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def imported_recent(conn: sqlite3.Connection, since_iso: str) -> int:
    """Count items imported since `since_iso` — the healthy-throughput signal for the status page."""
    return conn.execute(
        "SELECT COUNT(*) FROM items WHERE status = 'imported' AND last_seen_at >= ?",
        (since_iso,),
    ).fetchone()[0]
