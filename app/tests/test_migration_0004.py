"""Phase-5 migration 0004 proofs (STATE-03): the v0003 -> v0004 table rebuild must add the `partial`
status (partial album completion) WITHOUT losing a single existing row — the live NAS ledger carries
~1,493 gaps that must survive untouched. Mirrors the 0003 proofs.

Imports ONLY from state.* (firewall). Seeds from the conftest `seed_v0002_ledger` fixture, then runs
the FULL migration chain (0001 -> 0004).

Sandbox note: stdlib sqlite3 only — runs on the Python 3.9 offline box; authoritative green at CI/NAS.
"""
import sqlite3

import pytest

from state.db import connect, run_migrations, MIGRATIONS
from state.repo import get_gap, set_status

_LATEST = len(MIGRATIONS)


def test_seeded_rows_survive_migration_to_v0004(seed_v0002_ledger):
    """Every pre-existing row survives the 0004 rebuild (count + identity + status + discovered_at)."""
    conn = connect(seed_v0002_ledger)
    count_before = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    discovered_before = {
        r["arr_id"]: r["discovered_at"]
        for r in conn.execute("SELECT arr_id, discovered_at FROM items").fetchall()
    }
    conn.close()

    conn2 = connect(seed_v0002_ledger)
    run_migrations(conn2)
    assert conn2.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    assert conn2.execute("SELECT COUNT(*) FROM items").fetchone()[0] == count_before
    assert get_gap(conn2, "lidarr", "1")["status"] == "pending"
    assert get_gap(conn2, "lidarr", "1")["discovered_at"] == discovered_before["1"]
    assert get_gap(conn2, "lidarr", "3")["status"] == "imported"
    conn2.close()


def test_partial_status_accepted(seed_v0002_ledger):
    """The widened CHECK accepts 'partial'; set_status round-trips it (partial album completion)."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    set_status(conn, "lidarr", "1", "partial")  # must NOT raise
    assert get_gap(conn, "lidarr", "1")["status"] == "partial"
    conn.close()


def test_all_prior_statuses_still_accepted_after_0004(seed_v0002_ledger):
    """No regression: every status the enum carried before 0004 still round-trips; junk still rejected."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    for status in ("pending", "downloading", "importing", "quarantined", "stuck",
                   "permanently-unavailable", "imported", "partial"):
        set_status(conn, "lidarr", "1", status)  # must NOT raise
        assert get_gap(conn, "lidarr", "1")["status"] == status
    with pytest.raises(sqlite3.IntegrityError):
        set_status(conn, "lidarr", "1", "not-a-real-status")
    conn.close()


def test_migration_0004_idempotent(seed_v0002_ledger):
    """Re-running run_migrations on a current DB applies nothing and leaves user_version at the latest."""
    conn = connect(seed_v0002_ledger)
    run_migrations(conn)
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    run_migrations(conn)  # second call: no-op
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == _LATEST
    conn.close()
