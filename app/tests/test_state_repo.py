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
from state.repo import upsert_gap, get_gap, set_status, list_by_status

VALID_STATUSES = (
    "pending", "searching", "grabbed", "downloaded",
    "imported", "unavailable", "blacklisted",
)


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
