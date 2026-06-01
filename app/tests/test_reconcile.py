"""05-03 Task 2: core/reconcile.py startup orphan reset + verify-by-requery guard (D-14, REL-02).

Offline-only: builds a real temp SQLite ledger via state.db.connect + run_migrations (the same
pair gap_detector.__main__ uses), seeds orphaned in-flight rows directly through the repo DAOs,
and drives reconcile_on_startup with a scriptable FakeAdapter (per-item verify_imported result)
behind an injected build_adapters. NO live *arr / slskd / network — INFRA_EXC is exercised by
having the fake RAISE one of the real httpx infra exception types.

The four load-bearing cases (D-14 / REL-02):
  (a) an `importing` orphan that ACTUALLY imported -> `imported`, execute_import NEVER called
      (no double-import, Pitfall 3);
  (b) a `downloading` orphan still wanted -> `pending`, attempt_count UNCHANGED (no burn, D-14);
  (c) a `searching` orphan still wanted -> `pending`, attempt_count UNCHANGED — proving the
      mid-search-kill orphan is NOT silently stranded (select_eligible never re-picks `searching`;
      D-14 / REL-02 / T-05-24);
  (d) verify raising an INFRA_EXC -> the row's status is LEFT AS-IS (no burn, retried next boot).
"""
import sqlite3
import threading

import pytest

from adapters.base import GapItem
from core import reconcile
from core.acquire import INFRA_EXC
from state import repo
from state.db import connect, run_migrations


# --- a scriptable adapter + a build_adapters that hands it back -----------------------------------

class FakeAdapter:
    """A minimal ArrAdapter stand-in for reconcile: a per-(arr_app, arr_id) verify_imported result,
    a flag whose value can be an Exception INSTANCE (raised to model an infra fault), and a counter
    proving execute_import is NEVER called by reconcile (it only verifies + resets — no re-import)."""

    def __init__(self, app="lidarr", results=None):
        self.app = app
        # results: {arr_id: True|False|<Exception instance to raise>}
        self._results = dict(results or {})
        self.execute_import_calls = 0
        self.verify_calls = []

    def verify_imported(self, item: GapItem) -> bool:
        self.verify_calls.append(item.arr_id)
        outcome = self._results.get(item.arr_id, False)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def execute_import(self, decisions) -> None:   # pragma: no cover - must never be called here
        self.execute_import_calls += 1


def _build_adapters_factory(adapter):
    """Return a build_adapters() -> (adapters, clients) that hands back the one fake + a close-spy
    client (so the CR-02 finally-close path is exercised)."""

    class _Client:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    client = _Client()

    def build_adapters():
        return [adapter], [client]

    build_adapters.client = client   # expose for the close assertion
    return build_adapters


class _Settings:
    """reconcile reads nothing off settings today, but the signature takes it — pass a stub."""


def _seed(conn, arr_id, status, *, attempt_count=0):
    """Insert a gap then move it to `status` (and optionally stamp attempt_count) so reconcile finds
    an orphan. Uses ONLY the repo DAOs — the same writes the live loop makes."""
    item = GapItem(
        arr_app="lidarr",
        arr_id=arr_id,
        kind="album",
        gap_type="missing",
        title=f"Album {arr_id}",
        artist_or_author=f"Artist {arr_id}",
        foreign_id=f"mbid-{arr_id}",
        quality_profile_id=1,
        raw={},
    )
    repo.upsert_gap(conn, item)
    if attempt_count:
        # Stamp the attempt counter WITHOUT moving status yet (record_attempt sets status too), then
        # land on the orphan status via set_status so the seeded attempt_count is what we assert on.
        repo.record_attempt(conn, "lidarr", arr_id, attempt_count, None, "stuck")
    repo.set_status(conn, "lidarr", arr_id, status)
    conn.commit()


def _status_of(conn, arr_id):
    return repo.get_gap(conn, "lidarr", arr_id)["status"]


def _attempt_of(conn, arr_id):
    return repo.get_gap(conn, "lidarr", arr_id)["attempt_count"]


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "curator.sqlite"))
    run_migrations(c)
    yield c
    c.close()


# --- (a) importing orphan that imported -> imported, NO re-import ---------------------------------

def test_importing_orphan_that_imported_becomes_imported_no_double_import(conn):
    """An `importing` row whose verify_imported -> True (it landed while we were down) becomes
    `imported`, and execute_import is NEVER called (no double-import, Pitfall 3 / T-05-10)."""
    _seed(conn, "10", "importing")
    adapter = FakeAdapter(results={"10": True})
    lock = threading.Lock()

    reconcile.reconcile_on_startup(conn, lock, _build_adapters_factory(adapter), _Settings())

    assert _status_of(conn, "10") == "imported"
    assert adapter.execute_import_calls == 0      # verify-by-requery guard: never re-imported


# --- (b) downloading orphan still wanted -> pending, attempt_count unchanged ----------------------

def test_downloading_orphan_still_wanted_resets_to_pending_without_burning_attempt(conn):
    """A `downloading` row whose verify -> False (still wanted) resets to `pending` and its
    attempt_count is UNCHANGED — the interruption was infra, not a genuine fail (D-14, REL-02)."""
    _seed(conn, "20", "downloading", attempt_count=2)
    adapter = FakeAdapter(results={"20": False})

    reconcile.reconcile_on_startup(conn, threading.Lock(), _build_adapters_factory(adapter), _Settings())

    assert _status_of(conn, "20") == "pending"
    assert _attempt_of(conn, "20") == 2           # NOT incremented (no burn)


# --- (c) searching orphan still wanted -> pending, attempt_count unchanged (the D-14 headline) ----

def test_searching_orphan_resets_to_pending_not_stranded(conn):
    """THE D-14 / T-05-24 case: a `searching` row (killed mid-search, nothing ever downloaded) whose
    verify -> False resets to `pending` with attempt_count UNCHANGED. select_eligible never re-picks
    `searching`, so without this reset the orphan would be stranded FOREVER — this proves it is
    re-eligible and burned no attempt."""
    _seed(conn, "30", "searching", attempt_count=1)
    adapter = FakeAdapter(results={"30": False})

    reconcile.reconcile_on_startup(conn, threading.Lock(), _build_adapters_factory(adapter), _Settings())

    assert _status_of(conn, "30") == "pending"
    assert _attempt_of(conn, "30") == 1           # no burn — a mid-search kill is infra, not a fail


# --- (d) infra fault during verify -> row left as-is, no burn -------------------------------------

def test_infra_fault_during_verify_leaves_row_as_is_no_burn(conn):
    """An INFRA_EXC during verify (the *arr is unreachable this boot) leaves the orphan's status
    UNCHANGED and burns NO attempt — it is retried next boot (REL-02). Exercised by the fake raising
    a real httpx infra exception type (the first member of INFRA_EXC)."""
    if not INFRA_EXC:
        pytest.skip("httpx absent in this sandbox -> nothing classifiable as infra")
    infra_error = INFRA_EXC[0]("simulated *arr outage")   # e.g. httpx.ConnectError
    _seed(conn, "40", "downloading", attempt_count=1)
    adapter = FakeAdapter(results={"40": infra_error})

    reconcile.reconcile_on_startup(conn, threading.Lock(), _build_adapters_factory(adapter), _Settings())

    assert _status_of(conn, "40") == "downloading"   # left exactly as-is (no reset)
    assert _attempt_of(conn, "40") == 1              # no burn


# --- bonus: all three orphan states are swept in one pass + clients are closed (CR-02) ------------

def test_all_orphan_states_swept_and_clients_closed(conn):
    """reconcile sweeps searching/downloading/importing together in one pass and closes every client
    in `finally` (CR-02). Mixed outcomes: imported -> imported, still-wanted -> pending."""
    _seed(conn, "50", "searching")
    _seed(conn, "51", "downloading")
    _seed(conn, "52", "importing")
    adapter = FakeAdapter(results={"50": False, "51": False, "52": True})
    factory = _build_adapters_factory(adapter)

    reconcile.reconcile_on_startup(conn, threading.Lock(), factory, _Settings())

    assert _status_of(conn, "50") == "pending"
    assert _status_of(conn, "51") == "pending"
    assert _status_of(conn, "52") == "imported"
    assert factory.client.closed is True            # CR-02: client closed in finally
    assert adapter.execute_import_calls == 0


# --- boot re-arm: clear stuck/quarantined/permanently-unavailable backoff on rebuild --------------

class _ResetOffSettings:
    """Settings stub with the boot re-arm explicitly disabled."""
    acq_reset_stuck_on_start = False


def test_rearm_clears_stuck_quarantined_and_permanently_unavailable(conn):
    """rearm_stuck_on_start resets every stuck/quarantined/permanently-unavailable row to a clean
    pending slate (attempt_count=0, next_attempt_at=NULL) so a rebuild re-attempts the whole backlog
    immediately. A `pending`/`imported`/in-flight row is left untouched."""
    _seed(conn, "60", "stuck", attempt_count=2)
    _seed(conn, "61", "quarantined", attempt_count=1)
    _seed(conn, "62", "permanently-unavailable", attempt_count=3)
    _seed(conn, "63", "pending")                 # already pending — untouched
    _seed(conn, "64", "imported")                # terminal — must NOT be re-armed
    repo.record_attempt(conn, "lidarr", "60", 2, "2999-01-01T00:00:00Z", "stuck")  # future backoff
    conn.commit()

    n = reconcile.rearm_stuck_on_start(conn, threading.Lock(), _Settings())

    assert n == 3
    for aid in ("60", "61", "62"):
        assert _status_of(conn, aid) == "pending"
        assert _attempt_of(conn, aid) == 0
        assert repo.get_gap(conn, "lidarr", aid)["next_attempt_at"] is None
    assert _status_of(conn, "63") == "pending"
    assert _status_of(conn, "64") == "imported"  # terminal state untouched


def test_rearm_is_a_noop_when_disabled(conn):
    """With acq_reset_stuck_on_start False, no row is touched and the stuck backoff survives."""
    _seed(conn, "70", "stuck", attempt_count=2)

    n = reconcile.rearm_stuck_on_start(conn, threading.Lock(), _ResetOffSettings())

    assert n == 0
    assert _status_of(conn, "70") == "stuck"
    assert _attempt_of(conn, "70") == 2


# --- cross-restart orphan cleanup: purge abandoned staging + sweep slskd's download queue ----------

def test_reset_orphan_purges_its_abandoned_staging(conn, tmp_path):
    """A reset in-flight orphan has its recorded staging dir (the abandoned partial/complete download)
    PURGED so a restart leaves no junk and never re-imports a half-album."""
    from pathlib import Path

    class _S:
        staging_root = str(tmp_path / "data" / "downloads" / "soulseek")

    _seed(conn, "80", "downloading")
    item_id = repo.get_gap(conn, "lidarr", "80")["id"]
    leftover = Path(_S.staging_root) / "Queen - The Miracle"
    leftover.mkdir(parents=True, exist_ok=True)
    (leftover / "01 - orphaned.flac").write_text("junk")
    repo.record_staged_file(conn, item_id, str(leftover))
    conn.commit()

    adapter = FakeAdapter(results={"80": False})   # still wanted -> reset to pending
    reconcile.reconcile_on_startup(conn, threading.Lock(), _build_adapters_factory(adapter), _S())

    assert _status_of(conn, "80") == "pending"
    assert not leftover.exists(), "the abandoned download's staging dir must be purged on reset"


def test_clear_orphaned_downloads_noop_when_disabled():
    """With acq_clear_downloads_on_start False, the sweep returns 0 and never builds a client."""
    class _Off:
        acq_clear_downloads_on_start = False

    assert reconcile.clear_orphaned_downloads_on_start(_Off()) == 0


def test_clear_orphaned_downloads_sweeps_and_closes_client(monkeypatch):
    """The sweep builds a slskd client, calls clear_all_downloads, and closes the client (CR-02)."""
    class _FakeSlskd:
        def clear_all_downloads(self):
            return 5

    class _Client:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    client = _Client()
    monkeypatch.setattr("core.acquire.build_acquire_clients", lambda settings: (_FakeSlskd(), [client]))

    class _On:
        acq_clear_downloads_on_start = True

    n = reconcile.clear_orphaned_downloads_on_start(_On())
    assert n == 5
    assert client.closed is True   # CR-02: caller-owns-close


def test_clear_orphaned_downloads_swallows_infra_fault(monkeypatch):
    """slskd unreachable this boot -> the sweep is skipped (returns 0), never blocks startup."""
    class _DownSlskd:
        def clear_all_downloads(self):
            raise RuntimeError("slskd unreachable")

    class _Client:
        def close(self):
            pass

    monkeypatch.setattr("core.acquire.build_acquire_clients", lambda settings: (_DownSlskd(), [_Client()]))

    class _On:
        acq_clear_downloads_on_start = True

    assert reconcile.clear_orphaned_downloads_on_start(_On()) == 0
