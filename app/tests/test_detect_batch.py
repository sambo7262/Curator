# Curator detect-batch tests (D-15) — prove a detection pass commits as ONE transaction (one fsync),
# rolls back WHOLLY on a mid-pass fault, and that the single-transaction wrap does NOT regress the
# Phase-2 dedup / status-never-clobbered / discovered_at-preservation guarantees.
# Offline: FakeAdapter yields canned GapItems; a real temp SQLite ledger proves the txn semantics.
import sqlite3

import pytest

from adapters.base import GapItem
from core.gap_detector import detect_gaps
from state import repo
from state.db import connect, run_migrations


class FakeAdapter:
    """A neutral ArrAdapter double: yields canned GapItems and reports an app name."""

    def __init__(self, app, items, raise_on_get=False):
        self.app = app
        self._items = items
        self.raise_on_get = raise_on_get

    def get_wanted(self):
        if self.raise_on_get:
            raise RuntimeError(f"{self.app} adapter hard fault")
        return list(self._items)


def _item(app, arr_id, title="T"):
    return GapItem(
        arr_app=app, arr_id=str(arr_id), kind="album", gap_type="missing",
        artist_or_author="Artist", title=title,
        foreign_id=f"fid-{arr_id}", quality_profile_id=1,
    )


def _seed_conn():
    conn = connect(":memory:")
    run_migrations(conn)
    return conn


def test_pass_commits_as_one_transaction():
    """After a successful pass every row is present AND the connection is back in autocommit (no
    dangling open transaction) — i.e. the pass opened ONE BEGIN and COMMITted it."""
    conn = _seed_conn()
    adapters = [FakeAdapter("lidarr", [_item("lidarr", 1), _item("lidarr", 2)])]
    counts = detect_gaps(adapters, conn)
    assert counts == {"lidarr": 2}
    rows = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
    assert rows["n"] == 2
    # sqlite3.Connection.in_transaction is False once the pass COMMITs (no leaked open txn).
    assert conn.in_transaction is False


def test_midpass_fault_rolls_back_whole_pass():
    """A FakeAdapter raising mid-pass ROLLBACKs the WHOLE pass: zero new rows survive, even the
    ones from the adapter iterated before the faulting one."""
    conn = _seed_conn()
    adapters = [
        FakeAdapter("lidarr", [_item("lidarr", 1), _item("lidarr", 2)]),
        FakeAdapter("readarr", [], raise_on_get=True),  # raises AFTER lidarr rows were upserted
    ]
    with pytest.raises(RuntimeError):
        detect_gaps(adapters, conn)
    # The whole pass rolled back — the lidarr rows committed in the same txn are gone too.
    rows = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
    assert rows["n"] == 0
    # And the connection is not left in a dangling open transaction.
    assert conn.in_transaction is False


def test_batch_preserves_dedup():
    """Re-running detection inside the single-txn wrap still dedups (ON CONFLICT) — no dup rows."""
    conn = _seed_conn()
    adapters = [FakeAdapter("lidarr", [_item("lidarr", 1)])]
    detect_gaps(adapters, conn)
    detect_gaps(adapters, conn)
    rows = conn.execute("SELECT COUNT(*) AS n FROM items").fetchone()
    assert rows["n"] == 1


def test_batch_preserves_acted_on_status_and_discovered_at():
    """A re-detect of an acted-on row must NOT reset its status back to pending and must NOT move
    discovered_at — the grace anchor + status-never-clobbered guarantee holds inside the txn."""
    conn = _seed_conn()
    adapters = [FakeAdapter("lidarr", [_item("lidarr", 1)])]
    detect_gaps(adapters, conn)
    before = conn.execute(
        "SELECT status, discovered_at FROM items WHERE arr_id = '1'"
    ).fetchone()
    repo.set_status(conn, "lidarr", "1", "imported")
    detect_gaps(adapters, conn)  # re-detect must NOT clobber imported nor move discovered_at
    after = conn.execute(
        "SELECT status, discovered_at FROM items WHERE arr_id = '1'"
    ).fetchone()
    assert after["status"] == "imported"
    assert after["discovered_at"] == before["discovered_at"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
