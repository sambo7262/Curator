"""Phase-2 END-TO-END gap-detector proofs — the integration of the *arr seam + the ledger spine.

These close the Phase-2 success criteria by driving the real detect_gaps over a real migrated
SQLite ledger, using small in-test FAKE adapters (no network, offline by construction):

- test_end_to_end_counts ................. GAP-01/GAP-02 — missing+cutoff GapItems detected via the
                                            adapter and persisted; per-app counts returned.
- test_dedup_on_rerun .................... STATE-02 — a second detection pass over the same
                                            identities adds ZERO duplicate ledger rows.
- test_dedup_preserves_status_end_to_end . STATE-02 / Pitfall 1 — a re-detect through the detector
                                            path does NOT clobber an acted-on row's status.
- test_readarr_fault_does_not_gate_music . ARR-02 — a faulting Readarr (breaker-wrapped -> [])
                                            yields readarr:0 while ALL Lidarr rows are upserted and
                                            NOTHING is raised — books never gate music.

Flat imports (pythonpath=["app"]) mirror app/tests/test_health.py. Sandbox is Python 3.9 + offline
but this suite uses only stdlib sqlite3 + the in-repo modules (no new deps), so it runs locally;
the authoritative green gate is Python 3.12 at CI/NAS.
"""
from adapters.base import GapItem
from adapters.breaker import CircuitBreaker
from core.gap_detector import build_adapters, detect_gaps
from state.db import connect, run_migrations
from state.repo import get_gap, set_status


def _album(arr_id, gap_type="missing"):
    """A real GapItem in the music shape (Lidarr)."""
    return GapItem(
        arr_app="lidarr",
        arr_id=str(arr_id),
        kind="album",
        gap_type=gap_type,
        title=f"Album {arr_id}",
        artist_or_author="Some Artist",
        foreign_id=f"mbid-{arr_id}",
        quality_profile_id=1,
        raw={"id": arr_id},
    )


def _book(arr_id, gap_type="missing"):
    """A real GapItem in the book shape (Readarr)."""
    return GapItem(
        arr_app="readarr",
        arr_id=str(arr_id),
        kind="book",
        gap_type=gap_type,
        title=f"Book {arr_id}",
        artist_or_author="Some Author",
        foreign_id=f"fbid-{arr_id}",
        quality_profile_id=1,
        raw={"id": arr_id},
    )


class FakeAdapter:
    """A minimal ArrAdapter: exposes `app` + get_wanted() returning a fixed GapItem list."""

    def __init__(self, app, items):
        self.app = app
        self._items = items

    def get_wanted(self):
        return list(self._items)


class FaultyAdapter:
    """A Readarr stand-in whose get_wanted() RAISES — used behind the breaker to prove ARR-02."""

    app = "readarr"

    def get_wanted(self):
        raise RuntimeError("Readarr metadata server is down")


def _row_count(conn):
    return conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]


def _migrated(tmp_db_path):
    conn = connect(tmp_db_path)
    run_migrations(conn)
    return conn


def test_end_to_end_counts(tmp_db_path):
    """GAP-01/02: a missing+cutoff Lidarr set + a Readarr set are detected and persisted; the
    returned counts and the ledger row total both equal K+M."""
    lidarr = FakeAdapter("lidarr", [_album(1, "missing"), _album(2, "cutoff"), _album(3, "missing")])
    readarr = FakeAdapter("readarr", [_book(10, "missing"), _book(11, "cutoff")])
    conn = _migrated(tmp_db_path)

    counts = detect_gaps([lidarr, readarr], conn)

    assert counts == {"lidarr": 3, "readarr": 2}
    assert _row_count(conn) == 5
    # both gap_types persisted (GAP-01 missing + GAP-02 cutoff)
    assert get_gap(conn, "lidarr", "1")["gap_type"] == "missing"
    assert get_gap(conn, "lidarr", "2")["gap_type"] == "cutoff"
    assert get_gap(conn, "readarr", "10") is not None
    conn.close()


def test_dedup_on_rerun(tmp_db_path):
    """STATE-02 end-to-end: running detection TWICE over the same identities adds no duplicate rows."""
    lidarr = FakeAdapter("lidarr", [_album(1), _album(2)])
    readarr = FakeAdapter("readarr", [_book(10)])
    conn = _migrated(tmp_db_path)

    detect_gaps([lidarr, readarr], conn)
    after_first = _row_count(conn)
    counts2 = detect_gaps([lidarr, readarr], conn)
    after_second = _row_count(conn)

    assert after_first == 3
    assert after_second == after_first  # zero duplicates on re-run
    assert counts2 == {"lidarr": 2, "readarr": 1}  # counts reflect items SEEN, not rows added
    conn.close()


def test_dedup_preserves_status_end_to_end(tmp_db_path):
    """STATE-02 / Pitfall 1: a re-detect through the detector path must NOT reset an acted-on
    row's status — the upsert preserves lifecycle progress end-to-end."""
    lidarr = FakeAdapter("lidarr", [_album(1), _album(2)])
    conn = _migrated(tmp_db_path)

    detect_gaps([lidarr], conn)
    set_status(conn, "lidarr", "1", "imported")  # something acted on this row
    detect_gaps([lidarr], conn)                   # a periodic re-detect runs again

    assert get_gap(conn, "lidarr", "1")["status"] == "imported"  # NOT clobbered to 'pending'
    assert get_gap(conn, "lidarr", "2")["status"] == "pending"
    conn.close()


def test_build_adapters_returns_closable_clients(monkeypatch):
    """CR-02: build_adapters hands back the httpx clients it created so the caller can close them
    (no leaked sockets/FDs). With both keys set, two adapters + two clients come back."""
    import config
    from config import Settings

    monkeypatch.setattr(config, "settings", Settings.from_env(), raising=True)
    monkeypatch.setenv("LIDARR_API_KEY", "lk")
    monkeypatch.setenv("READARR_API_KEY", "rk")
    monkeypatch.setattr(config, "settings", Settings.from_env(), raising=True)

    adapters, clients = build_adapters()
    try:
        assert [a.app for a in adapters] == ["lidarr", "readarr"]
        assert len(clients) == 2
    finally:
        for c in clients:
            c.close()
        # closing must be idempotent-safe and not error on already-built clients
        assert all(c.is_closed for c in clients)


def test_build_adapters_skips_readarr_without_key(monkeypatch):
    """CR-01/ARR-02: a missing READARR_API_KEY disables Readarr (music-only) rather than crashing,
    and the discarded Readarr client is closed so nothing leaks."""
    import config
    from config import Settings

    monkeypatch.setenv("LIDARR_API_KEY", "lk")
    monkeypatch.delenv("READARR_API_KEY", raising=False)
    monkeypatch.setattr(config, "settings", Settings.from_env(), raising=True)

    adapters, clients = build_adapters()
    try:
        assert [a.app for a in adapters] == ["lidarr"]   # Readarr skipped, music path intact
        # the Readarr client was created-then-closed; only the live Lidarr client remains tracked
        assert len(clients) == 1
    finally:
        for c in clients:
            c.close()


def test_readarr_fault_does_not_gate_music(tmp_db_path):
    """ARR-02 end-to-end: a faulting Readarr (breaker-wrapped -> []) yields readarr:0 and raises
    NOTHING, while ALL Lidarr rows are still upserted — books never gate music."""
    lidarr = FakeAdapter("lidarr", [_album(1), _album(2), _album(3), _album(4)])
    readarr = CircuitBreaker(FaultyAdapter())  # breaker swallows the RuntimeError -> []
    conn = _migrated(tmp_db_path)

    counts = detect_gaps([lidarr, readarr], conn)  # must NOT raise

    assert counts == {"lidarr": 4, "readarr": 0}   # Lidarr fully detected; Readarr degraded to 0
    assert _row_count(conn) == 4                    # all four music rows persisted
    for i in (1, 2, 3, 4):
        assert get_gap(conn, "lidarr", str(i)) is not None
    conn.close()
