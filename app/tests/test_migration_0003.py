"""Phase-5 migration 0003 proofs (STATE-03): the v0002 -> v0003 table rebuild must add the
backoff/attempt/dormant columns + the `permanently-unavailable` status WITHOUT losing a single
existing row — the live NAS ledger carries ~1,493 gaps that must survive untouched.

Imports ONLY from state.* (firewall: the state layer never couples to adapters). The seed comes
from the conftest `seed_v0002_ledger` fixture (the v1-only-then-full-migrate harness analog).

Sandbox note: stdlib sqlite3 only (no new deps) — runs on the Python 3.9 offline box; the
authoritative green gate is Python 3.12 at CI/NAS.
"""
import sqlite3

import pytest

from state.db import connect, run_migrations, MIGRATIONS
from state.repo import get_gap, set_status

_LATEST = len(MIGRATIONS)   # the user_version a full run_migrations reaches (tracks new migrations)


def test_v0002_rows_survive_migration_to_v0003(seed_v0002_ledger):
    """Every v0002 row survives the rebuild (count unchanged; identity + status + discovered_at intact)."""
    # The seed DB is at user_version=2 with three rows. Reconnect with the FULL migration list.
    conn = connect(seed_v0002_ledger)
    count_before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count_before == 3
    discovered_before = {
        r["arr_id"]: r["discovered_at"]
        for r in conn.execute("SELECT arr_id, discovered_at FROM items").fetchall()
    }
    conn.close()

    conn2 = connect(seed_v0002_ledger)
    run_migrations(conn2)
    assert conn2.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    assert conn2.execute("SELECT COUNT(*) FROM items").fetchone()[0] == count_before

    # Identity + status + discovered_at preserved across the rebuild.
    row1 = get_gap(conn2, "lidarr", "1")
    assert row1["title"] == "Old Pending"
    assert row1["status"] == "pending"
    assert row1["discovered_at"] == discovered_before["1"]
    assert get_gap(conn2, "lidarr", "2")["status"] == "stuck"
    assert get_gap(conn2, "lidarr", "3")["status"] == "imported"
    conn2.close()


def test_new_columns_default_correctly(seed_v0002_ledger):
    """attempt_count defaults 0 (NOT NULL); next_attempt_at + last_checked_at default NULL."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    for row in conn.execute(
        "SELECT attempt_count, next_attempt_at, last_checked_at FROM items"
    ).fetchall():
        assert row["attempt_count"] == 0
        assert row["next_attempt_at"] is None
        assert row["last_checked_at"] is None
    conn.close()


def test_permanently_unavailable_status_accepted(seed_v0002_ledger):
    """The widened CHECK accepts 'permanently-unavailable'; set_status no longer raises (STATE-03/D-07)."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    set_status(conn, "lidarr", "1", "permanently-unavailable")  # must NOT raise
    assert get_gap(conn, "lidarr", "1")["status"] == "permanently-unavailable"
    conn.close()


def test_pre_existing_acquisition_statuses_still_accepted(seed_v0002_ledger):
    """No regression: the Phase-4 acquisition statuses still round-trip after 0003 widens the enum."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    for status in ("downloading", "importing", "quarantined", "stuck"):
        set_status(conn, "lidarr", "1", status)  # must NOT raise
        assert get_gap(conn, "lidarr", "1")["status"] == status
    with pytest.raises(sqlite3.IntegrityError):
        set_status(conn, "lidarr", "1", "not-a-real-status")
    conn.close()


def test_migration_0003_idempotent(seed_v0002_ledger):
    """Re-running run_migrations on a v0003 DB applies nothing and leaves user_version=3."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    run_migrations(conn)  # second call: no-op
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    conn.close()


def test_fresh_db_migrates_all_the_way_to_v0003(tmp_db_path):
    """A brand-new DB (no prior version) migrates 0001 -> 0002 -> 0003 in order to user_version=3."""
    conn = connect(tmp_db_path)
    run_migrations(conn)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    # The new columns exist on a fresh DB too.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    assert {"attempt_count", "next_attempt_at", "last_checked_at"} <= cols
    conn.close()
