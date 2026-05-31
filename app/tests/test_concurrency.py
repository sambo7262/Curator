# Curator scheduler concurrency tests (T-05-14 / T-05-15) — prove the per-cycle dispatch never runs
# more than MAX_CONCURRENT acquire_item calls at once (the backlog-flood hard cap), and that every
# ledger write under that bounded parallelism succeeds with NO "database is locked" (the single
# connection serialized through the shared writer lock via LockedConn). Offline + deterministic:
# a counting fake acquire records the max-observed simultaneous depth using a real Barrier-free
# latch; no network, no sleep beyond a tiny rendezvous.
import sqlite3
import threading

import pytest

from adapters.base import GapItem
from core import scheduler
from state import repo
from state.db import connect, run_migrations


def _settings(max_concurrent):
    return type("S", (), dict(
        acq_enabled=True,
        acq_dry_run=False,
        max_concurrent=max_concurrent,
        acq_poll_interval_seconds=3600.0,
        acq_grace_seconds=259200.0,
        acq_max_attempts=3,
        acq_dormant_seconds=2592000.0,
    ))()


class FakeAdapter:
    def __init__(self, app="lidarr"):
        self.app = app

    def get_queue_status(self, item):
        return False


def _conn():
    conn = connect(":memory:")
    run_migrations(conn)
    return conn


def _seed(conn, n):
    for i in range(n):
        conn.execute(
            """INSERT INTO items (arr_app, arr_id, kind, gap_type, artist_or_author, title, foreign_id,
                                  quality_profile_id, status, discovered_at, last_seen_at, attempt_count)
               VALUES ('lidarr', ?, 'album', 'missing', 'A', 'T', ?, 1, 'pending',
                       '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z', 0)""",
            (str(i), f"fid-{i}"),
        )


def test_dispatch_respects_max_concurrent(monkeypatch):
    """With MAX_CONCURRENT=2 and 8 eligible items, at most 2 acquire_item calls run at once."""
    max_concurrent = 2
    n_items = 8

    current = {"n": 0, "max": 0}
    depth_lock = threading.Lock()
    enter = threading.Event()

    def fake_acquire(*a, **k):
        with depth_lock:
            current["n"] += 1
            current["max"] = max(current["max"], current["n"])
        # Spin briefly so concurrent workers overlap (deterministic-enough rendezvous, no network).
        for _ in range(2000):
            pass
        with depth_lock:
            current["n"] -= 1
        return "imported"

    monkeypatch.setattr(scheduler, "acquire_item", fake_acquire)

    conn = _conn()
    _seed(conn, n_items)
    lock = threading.Lock()
    settings = _settings(max_concurrent)

    adapter = FakeAdapter()
    items = [
        GapItem(arr_app="lidarr", arr_id=str(i), kind="album", gap_type="missing",
                artist_or_author="A", title="T", foreign_id="fid", quality_profile_id=1)
        for i in range(n_items)
    ]
    outcomes = scheduler.dispatch(items, {"lidarr": adapter}, object(), conn, lock, settings)

    assert current["max"] <= max_concurrent  # the hard cap held
    assert len(outcomes) == n_items


def test_bounded_writes_no_database_locked(monkeypatch):
    """Under bounded concurrency every apply_result write succeeds on the single connection (the
    LockedConn / shared lock serializes writers) — no sqlite3 'database is locked' OperationalError."""
    max_concurrent = 3
    n_items = 12

    monkeypatch.setattr(scheduler, "acquire_item", lambda *a, **k: "stuck")

    conn = _conn()
    _seed(conn, n_items)
    lock = threading.Lock()
    settings = _settings(max_concurrent)
    adapter = FakeAdapter()
    items = [
        GapItem(arr_app="lidarr", arr_id=str(i), kind="album", gap_type="missing",
                artist_or_author="A", title="T", foreign_id="fid", quality_profile_id=1)
        for i in range(n_items)
    ]

    outcomes = scheduler.dispatch(items, {"lidarr": adapter}, object(), conn, lock, settings)
    # Apply every outcome under the shared lock — must not raise OperationalError.
    for item, outcome in zip(items, outcomes):
        scheduler.apply_result(conn, lock, item, outcome, settings)

    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM items WHERE attempt_count = 1 AND status = 'stuck'"
    ).fetchone()
    assert rows["n"] == n_items  # every write landed (no lost write, no lock error)


def test_locked_conn_serializes_execute():
    """LockedConn.execute proxies the wrapped conn.execute under the shared lock (Shape B)."""
    conn = _conn()
    _seed(conn, 1)
    lock = threading.Lock()
    lc = scheduler.LockedConn(conn, lock)
    cur = lc.execute("SELECT COUNT(*) AS n FROM items")
    assert cur.fetchone()["n"] == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
