"""Phase-4 acquire-loop proofs — the single-item composition point end-to-end (offline, fakes + a
fake monotonic clock; NO network, NO sleep).

test_acquire.py is to acquire.acquire_item() what test_gap_detector.py is to detect_gaps(): it drives
the WHOLE loop — purge-expired-quarantine (D-06) -> fetch neutral Manifest+Profile -> collection-window
search (D-07) -> gate.evaluate ONCE -> download the winner into per-item staging -> no-progress stall
watch (D-01/ACQ-03) -> next-candidate fallback / exhausted-stuck (D-02) -> consume the adapter's
pre-filtered importable subset -> ManualImport -> re-query verify (D-03) -> purge-on-success (D-05) /
quarantine-on-failure (D-06) -> returns a neutral "imported"|"quarantined"|"stuck" string.

Every collaborator is a FAKE that speaks the SAME neutral seam the real code uses:
  * FakeAdapter      — get_manifest/get_quality_profile (neutral Manifest/Profile) + the three import
                       methods returning the already-filtered importable subset (opaque dicts).
  * FakeSlskd        — search/search_state/search_responses + the NEUTRAL progress seam the client
                       exposes (search_is_complete / transfer_progress) + enqueue/cancel; a scripted
                       per-poll progress sequence drives the stall watch.
  * a fake monotonic clock — a list-backed callable injected as acquire_item(..., now=clock) so the
                       stall + collection-window deadlines are exercised without real time.

The gate is the REAL core.gate.evaluate where rapidfuzz is present; where it is absent (the 3.9
sandbox) a tiny stub gate is injected via the `gate` seam so the loop's branches still run offline.
"""
import sqlite3
from pathlib import Path

import pytest

from adapters.base import GapItem
from core import acquire
from core.candidate import Candidate, CandidateFile
from core.manifest import Manifest
from core.quality import Profile
from state.db import connect, run_migrations
from state.repo import get_gap, upsert_gap

APP_DIR = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------------------------------
# Fakes — each speaks the neutral seam the real collaborators expose.
# ----------------------------------------------------------------------------------------------------

def _gap(arr_app="lidarr", arr_id="42", kind="album"):
    return GapItem(
        arr_app=arr_app,
        arr_id=str(arr_id),
        kind=kind,
        gap_type="missing",
        title="OK Computer",
        artist_or_author="Radiohead",
        foreign_id=f"mbid-{arr_id}",
        quality_profile_id=1,
        raw={"id": arr_id},
    )


def _candidate(folder="Radiohead - OK Computer (1997) [FLAC]", n_files=2, username="seeder"):
    files = tuple(
        CandidateFile(
            filename=f"{folder}/{i:02d} - Track.flac",
            size_bytes=24_000_000,
            extension="flac",
            bitrate_kbps=900,
            length_seconds=240,
        )
        for i in range(1, n_files + 1)
    )
    return Candidate(folder=folder, files=files, username=username, free_upload_slots=1, upload_speed=500_000)


class FakeClock:
    """A monotonic clock the test advances explicitly: each call returns the current value; the test
    pushes time forward via .advance(). Injected as acquire_item(..., now=clock)."""

    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeSlskd:
    """A neutral-seam slskd stand-in. Scripts the search responses and a per-poll progress sequence.

    progress_script: dict mapping a candidate username -> list of acquire.TransferProgress to return
    on successive transfer_progress() polls (the last element repeats). This is the NEUTRAL shape the
    real SlskdClient exposes; the fake never makes acquire read wire vocabulary.
    """

    def __init__(self, responses, progress_script, complete_after=0):
        self._responses = responses
        self._progress_script = progress_script
        self._complete_after = complete_after   # search_state poll count before isComplete
        self._state_polls = 0
        self._poll_idx = {}
        self.enqueued = []        # (username, files)
        self.cancelled = []       # (username, transfer_id)
        self.searches = []        # every search text submitted (relaxed retry appends a 2nd)
        self.deleted = []         # every search id delete_search() was called with (cleanup)

    def search(self, text):
        self.searches.append(text)
        return f"sid-{len(self.searches)}"

    def search_is_complete(self, search_id):
        self._state_polls += 1
        return self._state_polls > self._complete_after

    def search_responses(self, search_id):
        return list(self._responses)

    def delete_search(self, search_id):
        self.deleted.append(search_id)

    def enqueue_candidate(self, candidate):
        from adapters.slskd import TransferHandle, _remote_folder_leaf

        self.enqueued.append((candidate.username, list(candidate.files)))
        # A2: mirror the real client — the handle carries the leaf of the remote folder, which is the
        # dir slskd actually lands the files in under staging_root (no batchId/username subdir).
        return TransferHandle(
            username=candidate.username,
            filenames=tuple(f.filename for f in candidate.files if f.filename),
            landing_dir_name=_remote_folder_leaf(candidate.folder),
        )

    def transfer_progress(self, handle):
        username = handle.username
        seq = self._progress_script.get(username, [])
        idx = self._poll_idx.get(username, 0)
        item = seq[min(idx, len(seq) - 1)]
        self._poll_idx[username] = idx + 1
        return item

    def cancel_transfer(self, handle, remove=True):
        self.cancelled.append((handle.username, handle.filenames))


class FakeAdapter:
    """A neutral ArrAdapter: returns a neutral Manifest/Profile and the already-filtered importable
    subset. The three import methods are scriptable so each branch (empty subset, verify True/False,
    raise) can be exercised. `app` namespaces it (lidarr/readarr)."""

    def __init__(
        self,
        app="lidarr",
        manifest=None,
        profile=None,
        candidates_subset=None,
        verify_result=True,
        import_raises=False,
        track_counts=None,
    ):
        self.app = app
        self._manifest = manifest if manifest is not None else Manifest(
            artist="Radiohead", album="OK Computer", track_count=2, track_titles=None, kind="album"
        )
        self._profile = profile if profile is not None else Profile(
            allowed=frozenset({5}), cutoff_rank=5
        )
        self._candidates_subset = candidates_subset if candidates_subset is not None else [
            {"path": "/data/.../01.flac"}, {"path": "/data/.../02.flac"}
        ]
        self._verify_result = verify_result
        self._import_raises = import_raises
        # imported_track_count() returns these in order (last repeats). Default None -> a stable 0 on
        # every call, i.e. no increase -> the partial branch never fires (existing tests unaffected).
        self._track_counts = list(track_counts) if track_counts is not None else None
        self._track_count_calls = 0
        self.executed = []          # decisions passed to execute_import
        self.manifest_calls = 0

    def get_manifest(self, foreign_id):
        self.manifest_calls += 1
        return self._manifest

    def get_quality_profile(self, profile_id):
        return self._profile

    def manual_import_candidates(self, path, download_id=None):
        return list(self._candidates_subset)

    def execute_import(self, decisions):
        self.executed.append(list(decisions))
        if self._import_raises:
            raise RuntimeError("Lidarr ManualImport failed")

    def verify_imported(self, item):
        return self._verify_result

    def imported_track_count(self, item):
        if self._track_counts is None:
            return 0
        val = self._track_counts[min(self._track_count_calls, len(self._track_counts) - 1)]
        self._track_count_calls += 1
        return val


def _stub_gate(accept_folders):
    """A stub gate.evaluate replacement (used so the loop runs without rapidfuzz in the 3.9 sandbox).
    Accepts the first candidate whose folder is in accept_folders; declines if none match.

    Returns a real core.gate.GateResult so acquire treats it identically to the live gate."""
    from core.gate import GateResult

    def _evaluate(candidates, manifest, profile, cfg=None, min_kbps=None):
        accepted = [c for c in candidates if c.folder in accept_folders]
        if not accepted:
            return GateResult(decision="decline", chosen=None, distance=1.0, reasons=["stub: none"])
        return GateResult(
            decision="accept", chosen=accepted[0], distance=0.0,
            reasons=[f"stub: accept {accepted[0].folder}"],
        )

    return _evaluate


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "acq.sqlite"))
    run_migrations(c)
    return c


@pytest.fixture
def settings(tmp_path):
    """A Settings whose staging/quarantine roots are deep, isolated tmp dirs (assert_under_root-safe)."""
    from config import Settings

    staging = tmp_path / "data" / "downloads" / "soulseek"
    quarantine = staging / ".quarantine"
    staging.mkdir(parents=True, exist_ok=True)
    quarantine.mkdir(parents=True, exist_ok=True)
    return Settings(
        staging_root=str(staging),
        quarantine_root=str(quarantine),
        acq_search_window_seconds=10.0,
        acq_stall_seconds=100.0,
        acq_poll_seconds=5.0,
        quarantine_ttl_seconds=1000.0,
    )


def _seed(conn, item):
    upsert_gap(conn, item)
    return get_gap(conn, item.arr_app, item.arr_id)


# --- helpers to build the neutral progress sequence -------------------------------------------------

def _P(bytes_done, terminal=None):
    return acquire.TransferProgress(terminal=terminal, bytes_done=bytes_done)


# ====================================================================================================
# D-06: purge-expired-quarantine runs FIRST (before search)
# ====================================================================================================

def test_purge_expired_quarantine_runs_first(conn, settings):
    """An already-EXPIRED quarantine dir is gone after the run; a FRESH one survives (TTL-on-next-run)."""
    import os
    import time

    qroot = Path(settings.quarantine_root)
    expired = qroot / "old-1"
    fresh = qroot / "new-1"
    expired.mkdir()
    fresh.mkdir()
    # Backdate the expired dir well beyond the TTL.
    old = time.time() - settings.quarantine_ttl_seconds - 10_000
    os.utime(expired, (old, old))

    item = _gap()
    _seed(conn, item)
    slskd = FakeSlskd(responses=[], progress_script={}, complete_after=0)
    adapter = FakeAdapter()
    # No candidates -> declines -> stuck, but the housekeeping must already have run.
    acquire.acquire_item(
        item, adapter, slskd, conn, settings,
        now=FakeClock(), gate_evaluate=_stub_gate(set()),
    )
    assert not expired.exists(), "expired quarantine dir must be swept FIRST"
    assert fresh.exists(), "a fresh quarantine dir must survive"


# ====================================================================================================
# Phase-3 contract: fetch Manifest + Profile before gating; stuck (no search) if either missing
# ====================================================================================================

def test_missing_manifest_marks_stuck_without_searching(conn, settings):
    """If the adapter cannot supply a Manifest (returns None), the item is 'stuck' and slskd is never
    searched (we don't burn a search on an unresolvable gap)."""
    item = _gap()
    _seed(conn, item)
    slskd = FakeSlskd(responses=[], progress_script={})
    adapter = FakeAdapter(manifest=None)   # signals not-found via None
    # FakeAdapter returns the stored manifest; force None:
    adapter._manifest = None

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate(set())
    )
    assert out == "stuck"
    assert slskd.searches == [], "must NOT search when the decision inputs are unavailable"
    assert get_gap(conn, "lidarr", "42")["status"] == "stuck"


def test_missing_profile_marks_stuck_without_searching(conn, settings):
    item = _gap()
    _seed(conn, item)
    slskd = FakeSlskd(responses=[], progress_script={})
    adapter = FakeAdapter()
    adapter._profile = None

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate(set())
    )
    assert out == "stuck"
    assert slskd.searches == []


# ====================================================================================================
# ACQ-01/D-07: one search, collection window, gate.evaluate ONCE over the full set
# ====================================================================================================

def test_search_collection_window_then_gate_once(conn, settings):
    """One search; poll search_state until isComplete; build Candidates; call gate ONCE; on accept,
    download the winner. The fake search completes after 1 poll, so the window does not run to the
    deadline."""
    item = _gap()
    _seed(conn, item)
    folder = "Radiohead - OK Computer (1997) [FLAC]"
    cand = _candidate(folder=folder)
    slskd = _FakeSlskdCandidates([cand], progress_script={"seeder": [_P(24_000_000, "success")]},
                                 complete_after=1)
    adapter = FakeAdapter()
    gate_calls = []

    def _counting_gate(candidates, manifest, profile, cfg=None, min_kbps=None):
        gate_calls.append(list(candidates))
        return _stub_gate({folder})(candidates, manifest, profile)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_counting_gate
    )
    assert out == "imported"
    assert len(gate_calls) == 1, "gate.evaluate must be called exactly ONCE per search"
    assert len(slskd.searches) == 1
    assert slskd.enqueued and slskd.enqueued[0][0] == "seeder"


def test_search_window_throttles_poll_via_hook(conn, settings):
    """REGRESSION (daemon search-poll busy-loop): while a search is NOT yet complete, the collection
    window must call poll_hook between completeness polls (the throttle). WITHOUT this the loop fired
    thousands of search_is_complete (GET /searches/{id}) per second over the whole window. With a fake
    clock that only advances inside poll_hook, the window polls exactly as many times as the hook is
    invoked, so we can assert the hook gates the poll cadence."""
    item = _gap()
    _seed(conn, item)
    folder = "Radiohead - OK Computer (1997) [FLAC]"
    cand = _candidate(folder=folder)
    # Search reports incomplete for the first 2 polls, complete on the 3rd.
    slskd = _FakeSlskdCandidates([cand], progress_script={"seeder": [_P(24_000_000, "success")]},
                                 complete_after=2)
    adapter = FakeAdapter()
    clock = FakeClock()
    hook_calls = {"n": 0}

    def _hook():
        # The throttle stand-in for production's time.sleep(acq_poll_seconds): advance the fake clock.
        hook_calls["n"] += 1
        clock.advance(1.0)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=clock,
        gate_evaluate=_stub_gate({folder}), poll_hook=_hook,
    )
    assert out == "imported"
    # The window polled while incomplete and called the throttle each time it was not yet complete.
    assert hook_calls["n"] >= 2, "poll_hook must throttle each not-yet-complete completeness poll"


def test_acquire_does_not_delete_search_inline(conn, settings):
    """REGRESSION (slskd finalize race, 2026-06): acquire no longer deletes the search the instant
    responses are read — that DELETE raced slskd's own finalize (`expected 1 row affected 0` on nearly
    every search + clipped late responses). Cleanup is now deferred to the scheduler's between-batch
    slskd.gc_searches() sweep, so acquire itself must NOT call delete_search."""
    item = _gap()
    _seed(conn, item)
    folder = "Radiohead - OK Computer (1997) [FLAC]"
    cand = _candidate(folder=folder)
    slskd = _FakeSlskdCandidates([cand], progress_script={"seeder": [_P(1, "success")]}, complete_after=0)
    adapter = FakeAdapter()

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({folder})
    )
    assert out == "imported"
    assert slskd.deleted == [], "acquire must NOT delete searches inline (deferred to scheduler GC)"


# ====================================================================================================
# ACQ-02/IMPORT-01: accept -> enqueue + staging under staging_root + state transitions + staged row
# ====================================================================================================

def test_accept_enqueues_and_records_staging(conn, settings):
    item = _gap()
    _seed(conn, item)
    folder = "Radiohead - OK Computer (1997) [FLAC]"
    cand = _candidate(folder=folder)
    slskd = _FakeSlskdCandidates([cand], progress_script={"seeder": [_P(1, "success")]}, complete_after=0)
    adapter = FakeAdapter()

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({folder})
    )
    assert out == "imported"
    # a staged_files row was recorded under the staging root
    row = conn.execute("SELECT staging_path FROM staged_files").fetchone()
    assert row is not None
    assert str(settings.staging_root) in row["staging_path"]
    assert slskd.enqueued, "the chosen candidate must be enqueued"


# ====================================================================================================
# ACQ-03/D-01/D-02: stall -> cancel + next candidate; exhausted -> stuck (never loops forever)
# ====================================================================================================

def test_stall_falls_to_next_then_stuck(conn, settings):
    """First candidate stalls (bytes never advance for acq_stall_seconds) -> cancel + fall to the next
    accepted candidate; the second also stalls -> exhausted -> 'stuck'. The fake clock makes the stall
    fire deterministically with no sleep."""
    item = _gap()
    _seed(conn, item)
    a = _candidate(folder="Alpha", username="stall_a")
    b = _candidate(folder="Beta", username="stall_b")
    # Both peers report the SAME byte count on every poll -> no progress -> stall.
    script = {
        "stall_a": [_P(1000), _P(1000), _P(1000)],
        "stall_b": [_P(2000), _P(2000), _P(2000)],
    }
    slskd = _FakeSlskdCandidates([a, b], progress_script=script, complete_after=0)
    adapter = FakeAdapter()

    clock = FakeClock()

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=clock,
        gate_evaluate=_stub_gate({"Alpha", "Beta"}),
        poll_hook=lambda: clock.advance(settings.acq_stall_seconds + 1),
    )
    assert out == "stuck"
    # both peers were cancelled with remove (D-02), exactly one cancel each
    assert {u for u, _ in slskd.cancelled} == {"stall_a", "stall_b"}
    assert get_gap(conn, "lidarr", "42")["status"] == "stuck"


def test_progress_then_complete_imports(conn, settings):
    """A peer whose bytes ADVANCE across polls resets the stall timer and reaches terminal success."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Gamma", username="good")
    script = {"good": [_P(10), _P(100), _P(24_000_000, "success")]}
    slskd = _FakeSlskdCandidates([cand], progress_script=script, complete_after=0)
    adapter = FakeAdapter()
    clock = FakeClock()
    # advance a little each poll — under the stall threshold, so progress is honored
    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=clock,
        gate_evaluate=_stub_gate({"Gamma"}),
        poll_hook=lambda: clock.advance(1.0),
    )
    assert out == "imported"
    assert not slskd.cancelled, "a progressing transfer must not be cancelled"


def test_terminal_failure_falls_to_next(conn, settings):
    """A hard terminal failure on the first candidate falls to the next accepted candidate (D-02)."""
    item = _gap()
    _seed(conn, item)
    a = _candidate(folder="Alpha", username="fail_a")
    b = _candidate(folder="Beta", username="ok_b")
    script = {
        "fail_a": [_P(0, "failure")],
        "ok_b": [_P(24_000_000, "success")],
    }
    slskd = _FakeSlskdCandidates([a, b], progress_script=script, complete_after=0)
    adapter = FakeAdapter()
    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(),
        gate_evaluate=_stub_gate({"Alpha", "Beta"}),
        poll_hook=lambda: None,
    )
    assert out == "imported"
    assert slskd.enqueued[-1][0] == "ok_b"


def test_enqueue_fault_falls_to_next(conn, settings):
    """A slskd 500 (or any non-infra fault) on ONE candidate's enqueue falls to the NEXT accepted
    candidate (D-02) — a flaky/offline peer never error-skips the whole item when 200+ other sources
    are right there. The first peer's enqueue raises; the second enqueues and imports cleanly."""
    item = _gap()
    _seed(conn, item)
    a = _candidate(folder="Alpha", username="fault_a")
    b = _candidate(folder="Beta", username="ok_b")

    class _EnqueueFaults(_FakeSlskdCandidates):
        def enqueue_candidate(self, candidate):
            if candidate.username == "fault_a":
                raise RuntimeError("500 Internal Server Error")   # slskd hiccup on a flaky peer
            return super().enqueue_candidate(candidate)

    script = {"ok_b": [_P(24_000_000, "success")]}
    slskd = _EnqueueFaults([a, b], progress_script=script, complete_after=0)
    adapter = FakeAdapter()
    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(),
        gate_evaluate=_stub_gate({"Alpha", "Beta"}), poll_hook=lambda: None,
    )
    assert out == "imported"
    assert slskd.enqueued and slskd.enqueued[-1][0] == "ok_b", "must fall through to the healthy peer"


def test_failed_candidate_partial_is_cleaned_up_before_next(conn, settings):
    """A hard-failed candidate (the 8/10-done-2-errored case) has its partial download CANCELLED and
    its staging leaf PURGED before we fall to the next candidate — the 'no leftover junk' guarantee on
    the abandon path. The next candidate then imports cleanly."""
    from pathlib import Path

    item = _gap()
    _seed(conn, item)
    a = _candidate(folder="Alpha", username="fail_a")
    b = _candidate(folder="Beta", username="ok_b")
    # the failed peer left already-downloaded tracks on disk under its landing leaf
    partial = Path(settings.staging_root) / "Alpha"
    partial.mkdir(parents=True, exist_ok=True)
    (partial / "08 - half an album.flac").write_text("junk")

    script = {"fail_a": [_P(0, "failure")], "ok_b": [_P(24_000_000, "success")]}
    slskd = _FakeSlskdCandidates([a, b], progress_script=script, complete_after=0)
    adapter = FakeAdapter()
    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(),
        gate_evaluate=_stub_gate({"Alpha", "Beta"}), poll_hook=lambda: None,
    )
    assert out == "imported"
    assert not partial.exists(), "the failed candidate's partial download must be purged (no junk left)"
    assert "fail_a" in {u for u, _ in slskd.cancelled}, "the failed transfer must be cancelled+removed"


# ====================================================================================================
# ACQ-01/D-08: decline -> one relaxed-query retry -> still decline -> stuck
# ====================================================================================================

def test_decline_then_relaxed_retry_then_stuck(conn, settings, caplog):
    """Gate declines the first set; acquire retries ONCE with a relaxed query; still declines -> stuck.
    Two searches total (original + relaxed), gate called twice, never enqueues. The decline is LOGGED
    with the gate's reason trail (the live-loop explainability fix: 'nothing passed the gate' is no
    longer blind to WHY — quality / fakeflac / match)."""
    import logging as _logging

    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Whatever (2019) [Deluxe Edition]")
    slskd = _FakeSlskdCandidates([cand], progress_script={}, complete_after=0)
    adapter = FakeAdapter()
    gate_calls = []

    def _declining_gate(candidates, manifest, profile, cfg=None, min_kbps=None):
        from core.gate import GateResult
        gate_calls.append(1)
        return GateResult(decision="decline", chosen=None, distance=1.0,
                          reasons=["[Whatever] excluded: below cutoff"])

    with caplog.at_level(_logging.INFO, logger="core.acquire"):
        out = acquire.acquire_item(
            item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_declining_gate
        )
    assert out == "stuck"
    assert len(slskd.searches) == 2, "original + ONE relaxed retry"
    assert len(gate_calls) == 2
    assert not slskd.enqueued
    # the relaxed query dropped the year/edition noise
    assert "2019" not in slskd.searches[1] and "Deluxe" not in slskd.searches[1]
    # the gate reason trail is surfaced in the stuck log (so a live operator can see WHY)
    text = caplog.text
    assert "nothing passed the gate after relaxed retry" in text
    assert "below cutoff" in text, "the gate's decline reason must be logged, not swallowed"


# ====================================================================================================
# IMPORT-02/03/05: complete -> candidates (pre-filtered) -> empty => quarantine; non-empty => import
# ====================================================================================================

def test_empty_importable_subset_parks_as_already_present(conn, settings):
    """If manual_import_candidates returns an EMPTY list (nothing importable — with filterExistingFiles
    that means the tracks are already on disk, the dominant single-inside-an-owned-album case), the item
    PARKS as 'already-present' (status 'partial', staging purged) instead of quarantine->re-download
    churn (owner 2026-06). No quarantine row is recorded."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Delta", username="seed")
    slskd = _FakeSlskdCandidates([cand], progress_script={"seed": [_P(1, "success")]}, complete_after=0)
    adapter = FakeAdapter(candidates_subset=[])   # nothing importable

    staging = Path(settings.staging_root) / "Delta"
    staging.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({"Delta"})
    )
    assert out == "already-present"
    assert get_gap(conn, "lidarr", "42")["status"] == "partial"
    assert not staging.exists(), "an already-present download is purged, not moved to quarantine"
    q = conn.execute("SELECT quarantine_path FROM staged_files").fetchone()
    assert q is None or not q["quarantine_path"], "no quarantine row for an already-present park"


def test_import_verify_purge_imported(conn, settings):
    """Non-empty importable subset -> execute_import with that EXACT list -> verify True -> purge ->
    'imported'. The staging dir is gone afterwards."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Epsilon", username="seed")
    slskd = _FakeSlskdCandidates([cand], progress_script={"seed": [_P(1, "success")]}, complete_after=0)
    subset = [{"path": "/x/01.flac"}, {"path": "/x/02.flac"}]
    adapter = FakeAdapter(candidates_subset=subset, verify_result=True)

    # A2: the staging dir slskd lands in is the remote-folder leaf ("Epsilon"), not a curator-* label.
    staging = Path(settings.staging_root) / "Epsilon"
    staging.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({"Epsilon"})
    )
    assert out == "imported"
    assert adapter.executed == [subset], "core must pass the adapter's list back AS-IS"
    assert get_gap(conn, "lidarr", "42")["status"] == "imported"
    assert not staging.exists(), "verified import must purge the staging dir (D-05)"


def test_verify_false_no_increase_parks_as_already_present(conn, settings):
    """execute_import runs but verify is False AND no new track files landed (downloaded != imported,
    track count unchanged) — the files are already on disk (DestinationAlreadyExists on a wanted single
    that lives inside an album we own). Re-downloading the same source can't help, so it PARKS as
    'already-present' (status 'partial', staging purged), NOT quarantine (owner 2026-06)."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Zeta", username="seed")
    slskd = _FakeSlskdCandidates([cand], progress_script={"seed": [_P(1, "success")]}, complete_after=0)
    adapter = FakeAdapter(verify_result=False)   # track_counts default None -> 0,0 (no increase)

    staging = Path(settings.staging_root) / "Zeta"
    staging.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({"Zeta"})
    )
    assert out == "already-present"
    assert get_gap(conn, "lidarr", "42")["status"] == "partial"
    assert staging.exists() is False, "an already-present download is purged (no junk left behind)"


def test_partial_import_when_tracks_increase(conn, settings):
    """Partial album completion: execute_import runs, the album STAYS wanted (verify False) BUT the
    *arr's on-disk track count INCREASED (baseline 2 -> 5) -> 'partial', NOT quarantine. The good
    tracks landed; staging is purged (no junk) and the item parks as 'partial' for a later revisit."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Theta", username="seed")
    slskd = _FakeSlskdCandidates([cand], progress_script={"seed": [_P(1, "success")]}, complete_after=0)
    # album still wanted (a single/EP only filled some tracks) but trackFileCount went 2 -> 5.
    adapter = FakeAdapter(verify_result=False, track_counts=[2, 5])

    staging = Path(settings.staging_root) / "Theta"
    staging.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({"Theta"})
    )
    assert out == "partial", "real tracks landed on an incomplete album = partial, not quarantine"
    assert get_gap(conn, "lidarr", "42")["status"] == "partial"
    assert not staging.exists(), "a partial import still purges staging (the matched files were moved out)"


def test_partial_no_increase_parks_as_already_present(conn, settings):
    """The partial branch is gated on a REAL increase: verify False AND the track count did NOT move
    (baseline 3 -> 3, e.g. every file already on disk) is NOT 'partial' — and (owner 2026-06) it is no
    longer a quarantine either. It parks as 'already-present' (re-grabbing the same source can't help)."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Iota", username="seed")
    slskd = _FakeSlskdCandidates([cand], progress_script={"seed": [_P(1, "success")]}, complete_after=0)
    adapter = FakeAdapter(verify_result=False, track_counts=[3, 3])

    staging = Path(settings.staging_root) / "Iota"
    staging.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({"Iota"})
    )
    assert out == "already-present"
    assert get_gap(conn, "lidarr", "42")["status"] == "partial"


def test_import_raise_quarantines(conn, settings):
    """A primary (Lidarr) import that RAISES becomes a quarantine outcome for that item (not a crash)."""
    item = _gap()
    _seed(conn, item)
    cand = _candidate(folder="Eta", username="seed")
    slskd = _FakeSlskdCandidates([cand], progress_script={"seed": [_P(1, "success")]}, complete_after=0)
    adapter = FakeAdapter(import_raises=True)

    # A2: the staging dir is the remote-folder leaf ("Eta").
    staging = Path(settings.staging_root) / "Eta"
    staging.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(), gate_evaluate=_stub_gate({"Eta"})
    )
    assert out == "quarantined"
    assert get_gap(conn, "lidarr", "42")["status"] == "quarantined"


# ====================================================================================================
# A2 (pinned live 2026-05-31): the import + purge target is staging_root/<leaf-of-remote-folder>,
# derived from the BACKSLASH-separated peer remote path — NOT a curator-* / batchId subdir.
# ====================================================================================================

def test_landing_dir_is_remote_folder_leaf(conn, settings):
    """A2: a candidate whose remote folder is a deep `music\\ZHU\\BLACK MIDAS (2026)` peer path lands —
    and is imported/purged — under staging_root/'BLACK MIDAS (2026)' (the LEAF only), with no
    curator-* / username / batchId subdir. Proves acquire points the import + purge at the real
    slskd landing folder via the neutral handle leaf."""
    item = _gap()
    _seed(conn, item)
    # slskd reports peer folders with backslash separators; only the last segment is the local dir.
    cand = _candidate(folder="music\\ZHU\\BLACK MIDAS (2026)", username="zhuseed")
    slskd = _FakeSlskdCandidates(
        [cand], progress_script={"zhuseed": [_P(1, "success")]}, complete_after=0
    )
    adapter = FakeAdapter(verify_result=True)

    landing = Path(settings.staging_root) / "BLACK MIDAS (2026)"
    landing.mkdir(parents=True, exist_ok=True)

    out = acquire.acquire_item(
        item, adapter, slskd, conn, settings, now=FakeClock(),
        gate_evaluate=_stub_gate({"music\\ZHU\\BLACK MIDAS (2026)"}),
    )
    assert out == "imported"
    # the staged_files row points at the LEAF landing dir, not a curator-* / batchId subdir
    row = conn.execute("SELECT staging_path FROM staged_files").fetchone()
    assert row["staging_path"].endswith("/BLACK MIDAS (2026)")
    assert "curator-" not in row["staging_path"]
    assert "zhuseed" not in row["staging_path"]
    # verified import purged exactly that leaf dir (D-05)
    assert not landing.exists()


# ====================================================================================================
# ARR-02: a Readarr import fault quarantines only that book; a music item completes "imported"
# ====================================================================================================

def test_readarr_fault_isolates_music(conn, settings):
    """A book whose import RAISES (a genuine *arr fault) quarantines the BOOK; a separately-processed
    music item completes 'imported'. Books never gate music. (A book that merely landed nothing new now
    parks as already-present like any item — see the already-present tests; only a hard fault quarantines.)"""
    book = _gap(arr_app="readarr", arr_id="7", kind="book")
    music = _gap(arr_app="lidarr", arr_id="9", kind="album")
    _seed(conn, book)
    _seed(conn, music)

    book_cand = _candidate(folder="SomeBook", username="bseed")
    music_cand = _candidate(folder="SomeAlbum", username="mseed")

    book_slskd = _FakeSlskdCandidates([book_cand], progress_script={"bseed": [_P(1, "success")]}, complete_after=0)
    music_slskd = _FakeSlskdCandidates([music_cand], progress_script={"mseed": [_P(1, "success")]}, complete_after=0)

    # Readarr best-effort: a hard import fault (raise) -> quarantine for the book only.
    book_adapter = FakeAdapter(app="readarr", import_raises=True)
    music_adapter = FakeAdapter(app="lidarr", verify_result=True)

    # A2: the book lands in the remote-folder leaf ("SomeBook").
    (Path(settings.staging_root) / "SomeBook").mkdir(parents=True, exist_ok=True)

    book_out = acquire.acquire_item(
        book, book_adapter, book_slskd, conn, settings, now=FakeClock(),
        gate_evaluate=_stub_gate({"SomeBook"}),
    )
    music_out = acquire.acquire_item(
        music, music_adapter, music_slskd, conn, settings, now=FakeClock(),
        gate_evaluate=_stub_gate({"SomeAlbum"}),
    )
    assert book_out == "quarantined"
    assert music_out == "imported"
    assert get_gap(conn, "readarr", "7")["status"] == "quarantined"
    assert get_gap(conn, "lidarr", "9")["status"] == "imported"


# ====================================================================================================
# build_acquire_clients factory
# ====================================================================================================

def test_build_acquire_clients_returns_client_and_owned_httpx():
    """The factory builds a SlskdClient + returns the httpx clients for the caller to close (CR-02)."""
    pytest.importorskip("httpx")
    import os

    os.environ["SLSKD_API_KEY"] = "test-key"
    try:
        from config import Settings
        s = Settings.from_env()
        slskd, clients = acquire.build_acquire_clients(s)
        try:
            assert slskd is not None
            assert clients and all(hasattr(c, "close") for c in clients)
        finally:
            for c in clients:
                c.close()
    finally:
        del os.environ["SLSKD_API_KEY"]


# ----------------------------------------------------------------------------------------------------
# A FakeSlskd variant that returns pre-built Candidate objects directly (bypassing from_slskd), so the
# loop's candidate-building seam is overridable for tests that want exact folders.
# ----------------------------------------------------------------------------------------------------

class _FakeSlskdCandidates(FakeSlskd):
    """Like FakeSlskd but search_responses returns objects acquire will turn into the given Candidates.

    acquire builds candidates via a `build_candidate` seam; here we hand back the Candidate objects
    directly and inject an identity builder, so tests control the exact folder/username set."""

    def __init__(self, candidates, progress_script, complete_after=0):
        super().__init__(responses=candidates, progress_script=progress_script, complete_after=complete_after)
        self._candidates = candidates

    def search_responses(self, search_id):
        return list(self._candidates)
