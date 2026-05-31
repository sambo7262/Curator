"""Phase-5 eligibility-select proof (GAP-03 + D-08 + D-09): select_eligible returns ONLY the items
the scheduler may act on this cycle —

  * pending/stuck/quarantined whose grace window has elapsed (discovered_at <= grace_cutoff)
    AND whose backoff has elapsed (next_attempt_at IS NULL OR <= now), OR
  * permanently-unavailable whose 30-day dormant TTL has elapsed (last_checked_at IS NULL OR
    <= dormant_cutoff),

ordered oldest-first and capped at `room` (the per-cycle flood control over the ~1493-gap backlog).

Imports ONLY from state.* (firewall). Stdlib sqlite3 only — runs on the offline 3.9 box.
"""
from state.db import connect, run_migrations
from state.repo import select_eligible, upsert_gap, set_status
from types import SimpleNamespace


def _gap(arr_id, **ov):
    base = dict(
        arr_app="lidarr", arr_id=arr_id, kind="album", gap_type="missing",
        title=f"Album {arr_id}", artist_or_author="Artist",
        foreign_id=f"mbid-{arr_id}", quality_profile_id=3, raw={"id": arr_id},
    )
    base.update(ov)
    return SimpleNamespace(**base)


def _set(conn, arr_id, *, discovered_at=None, status=None,
         next_attempt_at="__keep__", last_checked_at=None):
    """Direct column tweaks so each test pins deterministic eligibility inputs."""
    if status is not None:
        set_status(conn, "lidarr", arr_id, status)
    if discovered_at is not None:
        conn.execute("UPDATE items SET discovered_at = ? WHERE arr_id = ?",
                     (discovered_at, arr_id))
    if next_attempt_at != "__keep__":
        conn.execute("UPDATE items SET next_attempt_at = ? WHERE arr_id = ?",
                     (next_attempt_at, arr_id))
    if last_checked_at is not None:
        conn.execute("UPDATE items SET last_checked_at = ? WHERE arr_id = ?",
                     (last_checked_at, arr_id))


# A frozen "now" + the derived cutoffs the scheduler would compute (now - grace / now - dormant).
NOW = "2026-01-31T00:00:00Z"
GRACE_CUTOFF = "2026-01-28T00:00:00Z"      # now - 3 days
DORMANT_CUTOFF = "2026-01-01T00:00:00Z"    # now - 30 days


def _fresh_conn(tmp_db_path):
    conn = connect(tmp_db_path)
    run_migrations(conn)
    return conn


def test_grace_elapsed_pending_is_selected(tmp_db_path):
    """A pending item discovered before the grace cutoff is eligible (GAP-03)."""
    conn = _fresh_conn(tmp_db_path)
    upsert_gap(conn, _gap("1"))
    _set(conn, "1", discovered_at="2020-01-01T00:00:00Z", status="pending")
    rows = select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10)
    assert [r["arr_id"] for r in rows] == ["1"]
    conn.close()


def test_fresh_item_within_grace_is_not_selected(tmp_db_path):
    """An item discovered AFTER the grace cutoff is held back (grace not elapsed, GAP-03)."""
    conn = _fresh_conn(tmp_db_path)
    upsert_gap(conn, _gap("1"))
    _set(conn, "1", discovered_at="2026-01-30T00:00:00Z", status="pending")  # only 1 day old
    rows = select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10)
    assert rows == []
    conn.close()


def test_backoff_future_not_selected_past_selected(tmp_db_path):
    """A stuck item with next_attempt_at in the future waits; once it's in the past it's eligible (D-08)."""
    conn = _fresh_conn(tmp_db_path)
    upsert_gap(conn, _gap("1"))
    _set(conn, "1", discovered_at="2020-01-01T00:00:00Z", status="stuck",
         next_attempt_at="2026-02-15T00:00:00Z")  # future -> still backing off
    assert select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10) == []
    # Move the backoff into the past -> now eligible.
    _set(conn, "1", next_attempt_at="2026-01-01T00:00:00Z")
    rows = select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10)
    assert [r["arr_id"] for r in rows] == ["1"]
    conn.close()


def test_quarantined_is_retry_eligible(tmp_db_path):
    """OQ-2 resolved: quarantined items ARE retry-eligible once grace+backoff elapse (D-08)."""
    conn = _fresh_conn(tmp_db_path)
    upsert_gap(conn, _gap("1"))
    _set(conn, "1", discovered_at="2020-01-01T00:00:00Z", status="quarantined",
         next_attempt_at=None)
    rows = select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10)
    assert [r["arr_id"] for r in rows] == ["1"]
    conn.close()


def test_permanently_unavailable_dormant_recheck(tmp_db_path):
    """A permanently-unavailable item last-checked before the dormant cutoff re-enters the pool (D-09)."""
    conn = _fresh_conn(tmp_db_path)
    upsert_gap(conn, _gap("1"))
    _set(conn, "1", discovered_at="2020-01-01T00:00:00Z", status="permanently-unavailable",
         last_checked_at="2025-01-01T00:00:00Z")  # well before the 30-day dormant cutoff
    rows = select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10)
    assert [r["arr_id"] for r in rows] == ["1"]
    conn.close()


def test_permanently_unavailable_recently_checked_not_selected(tmp_db_path):
    """A permanently-unavailable item checked recently (after the dormant cutoff) stays dormant (D-09)."""
    conn = _fresh_conn(tmp_db_path)
    upsert_gap(conn, _gap("1"))
    _set(conn, "1", discovered_at="2020-01-01T00:00:00Z", status="permanently-unavailable",
         last_checked_at="2026-01-20T00:00:00Z")  # after the dormant cutoff
    assert select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10) == []
    conn.close()


def test_terminal_and_inflight_states_never_selected(tmp_db_path):
    """imported/searching/downloading/importing are NEVER eligible (no re-acting on satisfied/in-flight)."""
    conn = _fresh_conn(tmp_db_path)
    for i, status in enumerate(("imported", "searching", "downloading", "importing"), start=1):
        arr_id = str(i)
        upsert_gap(conn, _gap(arr_id))
        _set(conn, arr_id, discovered_at="2020-01-01T00:00:00Z", status=status)
    assert select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 10) == []
    conn.close()


def test_order_oldest_first_and_room_limit(tmp_db_path):
    """Eligible rows come back oldest-first and are capped at `room` (per-cycle flood control)."""
    conn = _fresh_conn(tmp_db_path)
    # Three eligible pending items with ascending discovered_at.
    upsert_gap(conn, _gap("a"))
    upsert_gap(conn, _gap("b"))
    upsert_gap(conn, _gap("c"))
    _set(conn, "a", discovered_at="2020-03-01T00:00:00Z", status="pending")
    _set(conn, "b", discovered_at="2020-01-01T00:00:00Z", status="pending")
    _set(conn, "c", discovered_at="2020-02-01T00:00:00Z", status="pending")
    rows = select_eligible(conn, GRACE_CUTOFF, NOW, DORMANT_CUTOFF, 2)
    assert [r["arr_id"] for r in rows] == ["b", "c"]   # oldest two, in order
    conn.close()
