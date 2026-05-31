"""Phase-2 coverage for the POST /detect manual trigger.

This endpoint is the in-app detection trigger (the Phase 2 plan allowed "a FastAPI route or
CLI entry"). It runs one detection pass on the app's SINGLE retained writer connection
(app.state.db) rather than opening a second connection — the fix for the on-NAS
`database is locked` contention seen when a separate `python -m core.gap_detector` process
wrote the ledger while uvicorn held it.

build_adapters is monkeypatched to a fake offline adapter, so no httpx/network is touched —
these run on the Python 3.9 + offline sandbox as well as CI/NAS 3.12.
"""
import main
from fastapi.testclient import TestClient

from adapters.base import GapItem
from config import Settings


class _FakeAdapter:
    """Minimal structural ArrAdapter: returns a fixed GapItem list, no network."""

    app = "lidarr"

    def __init__(self, items):
        self._items = items

    def get_wanted(self):
        return self._items


def _gap(arr_id, gap_type="missing"):
    return GapItem(
        arr_app="lidarr",
        arr_id=str(arr_id),
        kind="album",
        gap_type=gap_type,
        title=f"Album {arr_id}",
        artist_or_author="Artist",
        foreign_id=f"mbid-{arr_id}",
        quality_profile_id=1,
        raw={"id": arr_id},
    )


def test_detect_writes_to_ledger_and_dedups(tmp_path, monkeypatch):
    """POST /detect upserts gaps into the live app connection; a re-run adds no duplicates
    (STATE-02 proven end-to-end through the running app, single writer)."""
    db_file = str(tmp_path / "detect.sqlite")
    # Rebind the module-level settings to a temp DB before startup fires (frozen dataclass).
    monkeypatch.setattr(main, "settings", Settings(db_path=db_file), raising=True)

    items = [_gap(1), _gap(2, "cutoff"), _gap(3)]
    monkeypatch.setattr(
        "core.gap_detector.build_adapters",
        lambda: ([_FakeAdapter(items)], []),   # (adapters, clients) — no httpx clients to close
        raising=True,
    )

    with TestClient(main.app) as c:           # context manager fires startup -> app.state.db on temp DB
        r1 = c.post("/detect")
        assert r1.status_code == 200
        assert r1.json() == {"status": "ok", "detected": {"lidarr": 3}}

        conn = main.app.state.db
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM items WHERE status='pending'").fetchone()[0] == 3

        # Re-run over identical *arr identities: dedup, no new rows.
        r2 = c.post("/detect")
        assert r2.status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3


def test_detect_409_when_a_pass_is_already_running(tmp_path, monkeypatch):
    """The non-blocking lock means a concurrent /detect returns 409 instead of using the
    shared single connection from two threads at once."""
    monkeypatch.setattr(main, "settings", Settings(db_path=str(tmp_path / "lock.sqlite")), raising=True)
    monkeypatch.setattr(
        "core.gap_detector.build_adapters",
        lambda: ([_FakeAdapter([])], []),
        raising=True,
    )

    with TestClient(main.app) as c:
        main._detect_lock.acquire()           # simulate an in-flight detection pass
        try:
            resp = c.post("/detect")
            assert resp.status_code == 409
            assert "in progress" in resp.json()["detail"]
        finally:
            main._detect_lock.release()
