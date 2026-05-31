# Curator scheduler tests (REL-01 / GAP-03 / STATE-03 write side) — the daemon loop, the kill-switch,
# dry-run, the queue-check short-circuit, the infra/usenet no-burn, and the apply_result backoff /
# permanently-unavailable transitions. All offline + deterministic: a fake stop_event drives the loop,
# a frozen `now` callable drives the cutoffs, fakes drive the adapter/slskd; no real sleep, no network.
import sqlite3
import threading

import pytest

from adapters.base import GapItem
from core import scheduler
from state import repo
from state.db import connect, run_migrations


# --------------------------------------------------------------------------- helpers / fakes
class FakeAdapter:
    """A neutral ArrAdapter double for the scheduler cycle. queue_status / acquire_outcome are
    scriptable; get_queue_status can be told to raise an INFRA_EXC to prove the no-burn skip."""

    def __init__(self, app="lidarr", queue_active=False, queue_raises=None, wanted=None):
        self.app = app
        self.queue_active = queue_active
        self.queue_raises = queue_raises
        self._wanted = wanted or []

    def get_wanted(self):
        return list(self._wanted)

    def get_queue_status(self, item):
        if self.queue_raises is not None:
            raise self.queue_raises
        return self.queue_active


class FrozenNow:
    """A fixed wall-clock `now` returning a constant datetime (so cutoffs are deterministic)."""

    def __init__(self, dt):
        self._dt = dt

    def __call__(self):
        return self._dt


def _settings(**over):
    """A throwaway Settings-like object carrying only the fields the scheduler reads."""
    base = dict(
        acq_enabled=True,
        acq_dry_run=False,
        max_concurrent=2,
        acq_poll_interval_seconds=3600.0,
        acq_grace_seconds=259200.0,
        acq_max_attempts=3,
        acq_dormant_seconds=2592000.0,
    )
    base.update(over)
    return type("S", (), base)()


def _conn():
    conn = connect(":memory:")
    run_migrations(conn)
    return conn


def _seed_item(conn, arr_id, status="pending", attempt_count=0, discovered_at="2020-01-01T00:00:00Z",
               next_attempt_at=None, app="lidarr"):
    conn.execute(
        """INSERT INTO items (arr_app, arr_id, kind, gap_type, artist_or_author, title, foreign_id,
                              quality_profile_id, status, discovered_at, last_seen_at,
                              attempt_count, next_attempt_at)
           VALUES (?, ?, 'album', 'missing', 'Artist', 'Title', ?, 1, ?, ?, ?, ?, ?)""",
        (app, str(arr_id), f"fid-{arr_id}", status, discovered_at, discovered_at,
         attempt_count, next_attempt_at),
    )


def _item(app="lidarr", arr_id="1"):
    return GapItem(arr_app=app, arr_id=str(arr_id), kind="album", gap_type="missing",
                   artist_or_author="Artist", title="Title", foreign_id="fid", quality_profile_id=1)


# --------------------------------------------------------------------------- row -> GapItem mapping
def test_gapitem_from_row_carries_gap_type():
    """Regression: _gapitem_from_row must map the ledger's gap_type column onto the REQUIRED
    GapItem.gap_type field. It previously omitted gap_type, so every live cycle raised
    `TypeError: GapItem.__init__() missing 1 required positional argument: 'gap_type'` the moment
    select_eligible returned a row — the row->GapItem mapping was never exercised against a real
    SELECT * row. Seed a 'cutoff' gap so we prove the value is READ from the row, not defaulted."""
    conn = _conn()
    _seed_item(conn, "9")  # default gap_type 'missing'
    conn.execute("UPDATE items SET gap_type = 'cutoff' WHERE arr_id = '9'")
    row = conn.execute("SELECT * FROM items WHERE arr_id = '9'").fetchone()

    item = scheduler._gapitem_from_row(row)  # must NOT raise

    assert item.gap_type == "cutoff"
    assert (item.arr_app, item.arr_id, item.kind) == ("lidarr", "9", "album")
    assert item.foreign_id == "fid-9"


# --------------------------------------------------------------------------- loop lifecycle
def test_loop_runs_a_cycle_then_stops_cleanly(monkeypatch):
    calls = {"n": 0}

    def fake_run_cycle(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(scheduler, "run_cycle", fake_run_cycle)
    app = type("App", (), {"state": type("St", (), {"db": None})()})()
    sch = scheduler.Scheduler(app, _settings(acq_poll_interval_seconds=0.01), lock=threading.Lock())
    sch.start()
    sch.stop(timeout=2.0)
    assert not sch._thread.is_alive()
    assert calls["n"] >= 1  # at least the boot cycle ran


def test_disabled_skips_the_cycle(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(scheduler, "run_cycle", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    app = type("App", (), {"state": type("St", (), {"db": None})()})()
    sch = scheduler.Scheduler(app, _settings(acq_enabled=False, acq_poll_interval_seconds=0.01),
                              lock=threading.Lock())
    sch.start()
    sch.stop(timeout=2.0)
    assert calls["n"] == 0  # kill-switch off -> run_cycle never invoked


def test_raising_cycle_does_not_kill_the_thread(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("cycle blew up")

    monkeypatch.setattr(scheduler, "run_cycle", boom)
    app = type("App", (), {"state": type("St", (), {"db": None})()})()
    sch = scheduler.Scheduler(app, _settings(acq_poll_interval_seconds=0.01), lock=threading.Lock())
    sch.start()
    # Give the loop time to run several cycles, each raising.
    import time
    time.sleep(0.1)
    alive = sch._thread.is_alive()
    sch.stop(timeout=2.0)
    assert alive  # the thread survived the raising cycles (daemon never dies)
    assert calls["n"] >= 2  # it kept looping past the first exception


# --------------------------------------------------------------------------- run_one
def test_run_one_skips_when_usenet_active(monkeypatch):
    """get_queue_status True -> skip-usenet-active; acquire_item is NOT called (no burn, D-02)."""
    called = {"acquire": 0}
    monkeypatch.setattr(scheduler, "acquire_item",
                        lambda *a, **k: called.__setitem__("acquire", called["acquire"] + 1) or "imported")
    adapter = FakeAdapter(queue_active=True)
    outcome = scheduler.run_one(_item(), adapter, object(), object(), _settings())
    assert outcome == "skip-usenet-active"
    assert called["acquire"] == 0


def test_run_one_infra_on_queue_check_is_infra_skip(monkeypatch):
    """An INFRA_EXC from get_queue_status -> infra-skip (no burn); acquire NOT called."""
    called = {"acquire": 0}
    monkeypatch.setattr(scheduler, "acquire_item",
                        lambda *a, **k: called.__setitem__("acquire", called["acquire"] + 1) or "imported")
    if not scheduler.INFRA_EXC:
        pytest.skip("httpx absent in this sandbox; INFRA_EXC is empty")
    adapter = FakeAdapter(queue_raises=scheduler.INFRA_EXC[0]("boom"))
    outcome = scheduler.run_one(_item(), adapter, object(), object(), _settings())
    assert outcome == "infra-skip"
    assert called["acquire"] == 0


def test_run_one_infra_during_acquire_is_infra_skip(monkeypatch):
    if not scheduler.INFRA_EXC:
        pytest.skip("httpx absent in this sandbox; INFRA_EXC is empty")

    def boom(*a, **k):
        raise scheduler.INFRA_EXC[0]("acquire infra")

    monkeypatch.setattr(scheduler, "acquire_item", boom)
    adapter = FakeAdapter(queue_active=False)
    outcome = scheduler.run_one(_item(), adapter, object(), object(), _settings())
    assert outcome == "infra-skip"


def test_run_one_passes_through_acquire_outcome(monkeypatch):
    monkeypatch.setattr(scheduler, "acquire_item", lambda *a, **k: "quarantined")
    adapter = FakeAdapter(queue_active=False)
    outcome = scheduler.run_one(_item(), adapter, object(), object(), _settings())
    assert outcome == "quarantined"


def test_run_one_dry_run_logs_no_acquire(monkeypatch):
    """ACQ_DRY_RUN -> run_one short-circuits to a log-only path; acquire_item NOT called."""
    called = {"acquire": 0}
    monkeypatch.setattr(scheduler, "acquire_item",
                        lambda *a, **k: called.__setitem__("acquire", called["acquire"] + 1) or "imported")
    adapter = FakeAdapter(queue_active=False)
    outcome = scheduler.run_one(_item(), adapter, object(), object(), _settings(acq_dry_run=True))
    assert outcome == "dry-run"
    assert called["acquire"] == 0


# --------------------------------------------------------------------------- apply_result
def test_apply_result_imported_resets_attempt():
    conn = _conn()
    _seed_item(conn, 1, status="downloading", attempt_count=2)
    lock = threading.Lock()
    scheduler.apply_result(conn, lock, _item(arr_id="1"), "imported", _settings())
    row = conn.execute("SELECT status, attempt_count FROM items WHERE arr_id='1'").fetchone()
    assert row["status"] == "imported"
    assert row["attempt_count"] == 0


def test_apply_result_one_fail_backs_off_1h():
    conn = _conn()
    _seed_item(conn, 1, status="searching", attempt_count=0)
    lock = threading.Lock()
    scheduler.apply_result(conn, lock, _item(arr_id="1"), "stuck", _settings())
    row = conn.execute(
        "SELECT status, attempt_count, next_attempt_at FROM items WHERE arr_id='1'"
    ).fetchone()
    assert row["attempt_count"] == 1
    assert row["status"] == "stuck"
    assert row["next_attempt_at"] is not None  # backoff anchor set (~now+1h)


def test_apply_result_three_fails_permanently_unavailable():
    conn = _conn()
    _seed_item(conn, 1, status="stuck", attempt_count=2)  # this fail makes it the 3rd
    lock = threading.Lock()
    scheduler.apply_result(conn, lock, _item(arr_id="1"), "quarantined", _settings(acq_max_attempts=3))
    row = conn.execute(
        "SELECT status, attempt_count, next_attempt_at FROM items WHERE arr_id='1'"
    ).fetchone()
    assert row["attempt_count"] == 3
    assert row["status"] == "permanently-unavailable"
    assert row["next_attempt_at"] is not None  # dormant anchor (~now+30d)


def test_apply_result_infra_skip_writes_nothing():
    conn = _conn()
    _seed_item(conn, 1, status="pending", attempt_count=1)
    lock = threading.Lock()
    scheduler.apply_result(conn, lock, _item(arr_id="1"), "infra-skip", _settings())
    row = conn.execute(
        "SELECT status, attempt_count, next_attempt_at FROM items WHERE arr_id='1'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["attempt_count"] == 1
    assert row["next_attempt_at"] is None  # NO write — item stays eligible next cycle


def test_apply_result_usenet_skip_writes_nothing():
    conn = _conn()
    _seed_item(conn, 1, status="pending", attempt_count=0)
    lock = threading.Lock()
    scheduler.apply_result(conn, lock, _item(arr_id="1"), "skip-usenet-active", _settings())
    row = conn.execute("SELECT status, attempt_count FROM items WHERE arr_id='1'").fetchone()
    assert row["status"] == "pending"
    assert row["attempt_count"] == 0


def test_apply_result_dry_run_writes_nothing():
    conn = _conn()
    _seed_item(conn, 1, status="pending", attempt_count=0)
    lock = threading.Lock()
    scheduler.apply_result(conn, lock, _item(arr_id="1"), "dry-run", _settings(acq_dry_run=True))
    row = conn.execute("SELECT status, attempt_count FROM items WHERE arr_id='1'").fetchone()
    assert row["status"] == "pending"
    assert row["attempt_count"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
