"""Phase-2 state-layer proofs: restart-durability (STATE-01) + dedup & status-preservation
(STATE-02) + the lifecycle enum CHECK + idempotent migrations.

Imports ONLY from state.* — never from adapters.* — to keep the firewall intact (the state
layer must not couple to the adapter layer). The GapItem-shaped input is a tiny local
SimpleNamespace stand-in so this plan stays independent of Plan 03's adapters.base.GapItem.

Sandbox note: the dev box is Python 3.9 + offline, but these tests touch only stdlib sqlite3
(no new deps), so they run locally; the authoritative green gate is Python 3.12 at CI/NAS.
"""
import sqlite3
from types import SimpleNamespace

import pytest

from state.db import connect, run_migrations
from state.repo import (
    upsert_gap, get_gap, set_status, list_by_status,
    record_staged_file, record_quarantine,
)

# Phase-2 lifecycle statuses (migration 0001).
PHASE2_STATUSES = (
    "pending", "searching", "grabbed", "downloaded",
    "imported", "unavailable", "blacklisted",
)
# Phase-4 acquisition states added by migration 0002 (RESEARCH Pitfall 6).
PHASE4_STATUSES = ("downloading", "importing", "quarantined", "stuck")
VALID_STATUSES = PHASE2_STATUSES + PHASE4_STATUSES


def _gap(**overrides):
    """A GapItem-shaped stand-in (duck-typed) for upsert_gap — NOT an adapters import."""
    base = dict(
        arr_app="lidarr",
        arr_id="42",
        kind="album",
        gap_type="missing",
        title="Some Album",
        artist_or_author="Some Artist",
        foreign_id="mbid-release-group-1",
        quality_profile_id=3,
        raw={"id": 42, "foreignAlbumId": "mbid-release-group-1"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_persists_across_reconnect(tmp_db_path):
    """STATE-01 restart-durability proxy: a gap survives a connection close + reopen."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    upsert_gap(conn, _gap())
    set_status(conn, "lidarr", "42", "grabbed")
    conn.close()

    # Reopen the SAME file (the restart proxy) and re-run migrations (must be a no-op).
    conn2 = connect(tmp_db_path)
    run_migrations(conn2)
    row = get_gap(conn2, "lidarr", "42")
    assert row is not None
    assert row["status"] == "grabbed"
    assert row["title"] == "Some Album"
    assert row["foreign_id"] == "mbid-release-group-1"
    conn2.close()


def test_status_enum(tmp_db_path):
    """Each of the 7 valid statuses round-trips; an out-of-enum value raises IntegrityError."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    upsert_gap(conn, _gap())
    for status in VALID_STATUSES:
        set_status(conn, "lidarr", "42", status)
        assert get_gap(conn, "lidarr", "42")["status"] == status
    with pytest.raises(sqlite3.IntegrityError):
        set_status(conn, "lidarr", "42", "not-a-real-status")
    conn.close()


def test_dedup_no_duplicate(tmp_db_path):
    """STATE-02: re-upserting the SAME (arr_app, arr_id) yields exactly one row, refreshed."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    upsert_gap(conn, _gap())
    upsert_gap(conn, _gap(title="Renamed Album", gap_type="cutoff"))
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 1
    row = get_gap(conn, "lidarr", "42")
    assert row["title"] == "Renamed Album"   # metadata refreshed
    assert row["gap_type"] == "cutoff"
    conn.close()


def test_upsert_preserves_status(tmp_db_path):
    """STATE-02 / Pitfall 1: a re-detect upsert must NOT reset an acted-on row's status."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    upsert_gap(conn, _gap())
    set_status(conn, "lidarr", "42", "imported")
    # Re-detect the same identity (as a periodic run would) — status must survive.
    upsert_gap(conn, _gap(title="Still Shows In Wanted"))
    row = get_gap(conn, "lidarr", "42")
    assert row["status"] == "imported"        # NOT reset to 'pending'
    assert row["title"] == "Still Shows In Wanted"  # but metadata still refreshed
    assert list_by_status(conn, "pending") == []
    conn.close()


def test_migrations_idempotent(tmp_db_path):
    """Re-running run_migrations on an existing DB is a no-op: user_version unchanged, no error."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    version_after_first = conn.execute("PRAGMA user_version").fetchone()[0]
    run_migrations(conn)
    version_after_second = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version_after_first == version_after_second
    assert version_after_first >= 1
    conn.close()


def test_migration_and_version_bump_commit_together(tmp_db_path, monkeypatch):
    """WR-02: a migration's DDL and its user_version bump must commit ATOMICALLY. If ANY statement
    in the migration fails, the WHOLE migration (DDL + version bump) rolls back — no half-applied
    schema can coexist with a stale user_version (which would re-run a non-idempotent migration)."""
    import state.db as db

    # A self-contained migration that creates a table then issues an invalid statement, so the
    # transaction fails AFTER some DDL has run — exactly the crash WR-02 must survive.
    bad_migration = (
        "9999",
        "CREATE TABLE migration_probe (x INTEGER);\n"
        "THIS IS NOT VALID SQL;",
    )
    monkeypatch.setattr(db, "MIGRATIONS", [bad_migration], raising=True)

    conn = connect(tmp_db_path)
    with pytest.raises(sqlite3.OperationalError):
        run_migrations(conn)

    # Rolled back atomically: neither the probe table nor a version bump survived.
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 0
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='migration_probe'"
    ).fetchall() == []
    conn.close()

    # Restore the real MIGRATIONS and confirm a clean run applies fully + atomically.
    monkeypatch.undo()
    conn2 = connect(tmp_db_path)
    run_migrations(conn2)
    assert conn2.execute("PRAGMA user_version;").fetchone()[0] >= 1
    assert conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchall() != []
    conn2.close()


# ---------------------------------------------------------------------------
# Phase 4 — migration 0002: widened acquisition state machine + staged_files.
# ---------------------------------------------------------------------------

def test_migration_0002_bumps_user_version_to_2(tmp_db_path):
    """A fresh DB migrates all the way to user_version 2 (0001 then 0002 applied in order)."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 2
    # staged_files table now exists.
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='staged_files'"
    ).fetchall() != []
    conn.close()


def test_new_acquisition_statuses_write_without_integrityerror(tmp_db_path):
    """RESEARCH Pitfall 6: set_status with each Phase-4 state succeeds once 0002 widens the CHECK.

    The pre-0002 enum would raise sqlite3.IntegrityError on 'downloading'/'quarantined'/'stuck';
    after the rebuild they all round-trip, while a still-bogus value is still rejected.
    """
    conn = connect(tmp_db_path)
    run_migrations(conn)
    upsert_gap(conn, _gap())
    for status in PHASE4_STATUSES:
        set_status(conn, "lidarr", "42", status)  # must NOT raise
        assert get_gap(conn, "lidarr", "42")["status"] == status
    with pytest.raises(sqlite3.IntegrityError):
        set_status(conn, "lidarr", "42", "not-a-real-status")
    conn.close()


def test_existing_rows_survive_the_items_rebuild(tmp_db_path):
    """The table-rebuild that widens the CHECK must preserve existing rows (count + identity).

    Simulates a Phase-2 DB (only migration 0001 applied) carrying a row, THEN applies the rest
    of the migrations — the row must survive the RENAME/INSERT SELECT */DROP rebuild intact,
    including a lifecycle status it had already reached.
    """
    import state.db as db

    # Stand up a v1-only DB and seed a row with a non-default status.
    conn = connect(tmp_db_path)
    only_0001 = [m for m in db.MIGRATIONS if m[0] == "0001"]
    monkeypatch_migrations(conn, db, only_0001)
    upsert_gap(conn, _gap(arr_id="77", title="Pre-existing Album"))
    set_status(conn, "lidarr", "77", "imported")
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 1
    conn.close()

    # Now reconnect with the FULL migration list — 0002 rebuilds items beneath the row.
    conn2 = connect(tmp_db_path)
    run_migrations(conn2)
    assert conn2.execute("PRAGMA user_version;").fetchone()[0] == 2
    assert conn2.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    row = get_gap(conn2, "lidarr", "77")
    assert row is not None
    assert row["title"] == "Pre-existing Album"
    assert row["status"] == "imported"          # lifecycle status preserved across the rebuild
    assert row["foreign_id"] == "mbid-release-group-1"
    conn2.close()


def monkeypatch_migrations(conn, db_module, migrations):
    """Apply a restricted migration list against `conn` without pytest's monkeypatch fixture.

    Temporarily swaps db.MIGRATIONS, runs, and restores — lets a test stand up a v1-only DB.
    """
    saved = db_module.MIGRATIONS
    db_module.MIGRATIONS = migrations
    try:
        run_migrations(conn)
    finally:
        db_module.MIGRATIONS = saved


def test_migration_0002_idempotent(tmp_db_path):
    """Re-running run_migrations on a v2 DB applies nothing and leaves user_version=2."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 2
    run_migrations(conn)  # second call: no-op
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 2
    conn.close()


def _seed_item(conn) -> int:
    """Insert one items row (so staged_files' item_id FK resolves) and return its id."""
    upsert_gap(conn, _gap())
    return get_gap(conn, "lidarr", "42")["id"]


def test_record_staged_file_returns_rowid(tmp_db_path):
    """record_staged_file inserts a row keyed to the item and returns a positive rowid."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    item_id = _seed_item(conn)
    sid = record_staged_file(conn, item_id, "/data/downloads/soulseek/curator-lidarr-42")
    assert isinstance(sid, int) and sid > 0
    row = conn.execute("SELECT * FROM staged_files WHERE id = ?", (sid,)).fetchone()
    assert row["item_id"] == item_id
    assert row["staging_path"] == "/data/downloads/soulseek/curator-lidarr-42"
    assert row["created_at"]            # stamped
    assert row["quarantine_path"] is None
    conn.close()


def test_record_quarantine_roundtrip(tmp_db_path):
    """record_quarantine stamps quarantine_path/failure_reason/quarantined_at on the row (D-06)."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    item_id = _seed_item(conn)
    sid = record_staged_file(conn, item_id, "/data/downloads/soulseek/curator-lidarr-42")
    record_quarantine(conn, sid, "/data/downloads/soulseek/.quarantine/lidarr-42-x",
                      "manual import rejected all files")
    row = conn.execute("SELECT * FROM staged_files WHERE id = ?", (sid,)).fetchone()
    assert row["quarantine_path"] == "/data/downloads/soulseek/.quarantine/lidarr-42-x"
    assert row["failure_reason"] == "manual import rejected all files"
    assert row["quarantined_at"]        # timestamp set
    conn.close()


# ---------------------------------------------------------------------------
# Phase 4 — config tunables (SP-4): defaults, override, fail-fast.
# ---------------------------------------------------------------------------

def test_settings_phase4_defaults():
    """Settings.from_env() with no env returns the documented Phase-4 defaults (and no Plex)."""
    import config
    s = config.Settings.from_env()
    assert s.slskd_url == "http://localhost:5030"
    assert s.slskd_api_key is None
    assert s.acq_search_window_seconds == 12.0
    assert s.acq_stall_seconds == 600.0
    assert s.acq_poll_seconds == 5.0
    assert s.staging_root == "/data/downloads/soulseek"
    assert s.quarantine_root == "/data/downloads/soulseek/.quarantine"
    assert s.quarantine_ttl_seconds == 604800.0
    # Revised D-04: Curator does NOT call Plex — no Plex fields exist on Settings.
    assert not hasattr(s, "plex_url")
    assert not hasattr(s, "plex_token")


def test_settings_env_override_and_failfast(monkeypatch):
    """An env override is honored; a non-numeric tunable fails fast at from_env() time."""
    import config
    monkeypatch.setenv("ACQ_STALL_SECONDS", "120")
    assert config.Settings.from_env().acq_stall_seconds == 120.0
    monkeypatch.setenv("ACQ_STALL_SECONDS", "not-a-number")
    with pytest.raises(ValueError):
        config.Settings.from_env()


# ---------------------------------------------------------------------------
# Phase 4 — offline fixtures parse into the contracts later waves depend on.
# ---------------------------------------------------------------------------

def test_search_responses_fixture_builds_candidates(load_fixture):
    """slskd/search_responses.json builds >=2 Candidates via from_slskd with no KeyError."""
    from core.candidate import Candidate
    responses = load_fixture("slskd/search_responses")
    assert isinstance(responses, list) and len(responses) >= 2
    candidates = [Candidate.from_slskd(r) for r in responses]
    assert len(candidates) >= 2
    # The clean FLAC album has a full track set and a parsed album.
    clean = candidates[0]
    assert clean.audio_file_count == 12
    assert clean.username == "good_seeder"
    assert clean.parsed_album  # release_parse pulled an album from the folder name
    # The weaker candidate still builds (its quality is gated downstream, not here).
    assert candidates[1].audio_file_count >= 1


def test_transfer_fixtures_parse(load_fixture):
    """The three transfer snapshots load and carry the stall-watch signals (state + bytes)."""
    completed = load_fixture("slskd/transfer_completed")
    stalled = load_fixture("slskd/transfer_stalled")
    failed = load_fixture("slskd/transfer_failed")
    # Terminal-success heuristic (RESEARCH §stall watch): state holds Completed AND Succeeded.
    assert "Completed" in completed["state"] and "Succeeded" in completed["state"]
    # Failure heuristic: state holds one of Failed/Errored/Cancelled.
    assert any(tok in failed["state"] for tok in ("Failed", "Errored", "Cancelled"))
    # Stalled snapshot is mid-transfer with partial bytes (the no-progress poll input).
    assert 0 < stalled["bytesTransferred"] < stalled["size"]


def test_get_mapping_fixture_filters_to_importable(load_fixture):
    """manualimport/get_mapping.json filters (empty rejections + real tracks) to the importable set."""
    mapping = load_fixture("manualimport/get_mapping")
    assert isinstance(mapping, list) and len(mapping) >= 2
    importable = [m for m in mapping if not m["rejections"] and m["tracks"]]
    rejected = [m for m in mapping if m["rejections"]]
    assert len(importable) == 2          # the two real FLAC tracks
    assert len(rejected) == 1            # the folder.jpg with a permanent rejection
    # The expected POST body lists exactly the importable files.
    expected = load_fixture("manualimport/expected_post")
    assert expected["name"] == "ManualImport"
    assert len(expected["files"]) == len(importable)
