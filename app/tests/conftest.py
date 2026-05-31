"""Shared Phase-2 pytest fixtures: temp SQLite path, JSON fixture loader, and an
offline httpx mock client so adapter/ledger tests run with NO live Lidarr/Readarr.

The dev sandbox is Python 3.9 + offline (RESEARCH "Environment Availability"); the real
pytest run is Python 3.12 at CI/NAS. These fixtures make every Phase-2 behavior provable
against the recorded JSON in app/tests/fixtures/ — no network is referenced anywhere.
"""
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def seed_v0002_ledger(tmp_db_path):
    """A tmp ledger migrated to ONLY v0002, seeded with representative items rows.

    This is the Phase-5 analog of 02-02's "v1-only-then-full-migrate" preservation harness:
    it stands up a DB at user_version=2 (the live NAS shape before migration_0003), seeds a few
    rows with varied discovered_at + status (one old pending, one stuck, one imported), and
    returns the path. test_migration_0003 reconnects with the FULL migration list and asserts
    every row survives the v0002 -> v0003 rebuild with the new columns defaulted.

    Returns the db path (str). The DB is left CLOSED so the test owns the reconnect.
    """
    import state.db as db
    from state.repo import upsert_gap, set_status
    from types import SimpleNamespace

    # Apply ONLY 0001 + 0002 (the v0002 live shape) by slicing MIGRATIONS, then restore.
    conn = db.connect(tmp_db_path)
    saved = db.MIGRATIONS
    db.MIGRATIONS = [m for m in saved if m[0] in ("0001", "0002")]
    try:
        db.run_migrations(conn)
    finally:
        db.MIGRATIONS = saved
    assert conn.execute("PRAGMA user_version;").fetchone()[0] == 2

    def _gap(**ov):
        base = dict(
            arr_app="lidarr", arr_id="1", kind="album", gap_type="missing",
            title="Seed Album", artist_or_author="Seed Artist",
            foreign_id="mbid-seed-1", quality_profile_id=3,
            raw={"id": 1},
        )
        base.update(ov)
        return SimpleNamespace(**base)

    # Three representative rows: an old pending, a stuck one, an imported one. upsert_gap stamps
    # discovered_at = now; we then back-date them via a direct UPDATE so the grace/eligibility
    # tests have deterministic timestamps to compare against.
    upsert_gap(conn, _gap(arr_id="1", title="Old Pending"))
    upsert_gap(conn, _gap(arr_id="2", title="Stuck One"))
    upsert_gap(conn, _gap(arr_id="3", title="Imported One"))
    set_status(conn, "lidarr", "2", "stuck")
    set_status(conn, "lidarr", "3", "imported")
    conn.execute(
        "UPDATE items SET discovered_at = ? WHERE arr_id = ?",
        ("2020-01-01T00:00:00Z", "1"),
    )
    conn.execute(
        "UPDATE items SET discovered_at = ? WHERE arr_id = ?",
        ("2020-06-01T00:00:00Z", "2"),
    )
    conn.commit() if hasattr(conn, "commit") else None
    conn.close()
    return tmp_db_path


@pytest.fixture
def frozen_clock():
    """A deterministic, monotonic-style clock for the Phase-5 scheduler/backoff tests.

    Returns a small callable that yields a strictly-increasing float each call (starting at 0.0,
    +1.0 per tick) — the network-free stand-in the scheduler/transfer-watch tests inject in place
    of time.monotonic(). `.set(value)` jumps the clock; `.value` reads it without advancing.
    """

    class _Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            v = self.value
            self.value += 1.0
            return v

        def set(self, value):
            self.value = float(value)

    return _Clock()


@pytest.fixture
def tmp_db_path(tmp_path):
    """A throwaway SQLite file path under pytest's tmp_path.

    Returned as a string (sqlite3.connect signature). The file does not exist yet —
    state.db.connect()/run_migrations() create it, which lets tests reconnect to the
    same path to prove restart-durability (STATE-01, criterion 1).
    """
    return str(tmp_path / "curator-test.sqlite")


@pytest.fixture
def load_fixture():
    """Loader: name (without .json) -> parsed JSON from app/tests/fixtures/.

    `name` may include a subdir, e.g. load_fixture("slskd/transfer_completed") reads
    app/tests/fixtures/slskd/transfer_completed.json — so the Phase-4 slskd/ and
    manualimport/ fixtures load through the same helper with no new wiring.

    Returns whatever the file holds (a dict for an envelope, a list for a result array).
    """

    def _load(name: str):
        path = FIXTURES_DIR / f"{name}.json"
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    return _load


@pytest.fixture
def httpx_client(load_fixture):
    """Factory -> offline httpx.Client serving recorded *arr envelopes.

    Uses httpx.MockTransport (ships with httpx — no respx import needed, so adapter tests
    stay green even if respx is absent). Call the factory mapping a route suffix to a
    fixture name, e.g.::

        client = httpx_client({
            "wanted/missing": "lidarr_missing",
            "wanted/cutoff": ["lidarr_cutoff", "lidarr_cutoff_page2"],  # paged: page N -> Nth fixture
        })

    Routes are matched by the request path ending in "/api/v1/<suffix>". A list value
    serves a different fixture per `page` query param (1-indexed) so the adapter's paging
    loop can be exercised end-to-end. Unmapped paths return 404.
    """
    import httpx  # imported lazily so conftest still imports where httpx is absent (offline sandbox)

    def _factory(routes: dict) -> "httpx.Client":
        def _handler(request: "httpx.Request") -> "httpx.Response":
            for suffix, fixture in routes.items():
                if request.url.path.endswith(f"/api/v1/{suffix}"):
                    if isinstance(fixture, (list, tuple)):
                        page = int(request.url.params.get("page", "1"))
                        idx = min(page, len(fixture)) - 1
                        body = load_fixture(fixture[idx])
                    else:
                        body = load_fixture(fixture)
                    return httpx.Response(200, json=body)
            return httpx.Response(404, json={"error": "no route", "path": request.url.path})

        return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")

    return _factory
