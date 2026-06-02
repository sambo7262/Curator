# Curator acquire — the Phase-4 INTEGRATION point: the single composition function that wires
# search -> gate -> download -> stall-watch -> import -> verify -> purge/quarantine for ONE gap,
# exactly as gap_detector.detect_gaps composes the adapters + ledger in Phase 2. It is the core side
# of the firewall (PITFALL #6): ZERO *arr/slskd wire vocabulary — it speaks only the neutral GapItem /
# GateResult / Candidate / Manifest / Profile contract types, the neutral slskd-client progress seam
# (search_is_complete / transfer_progress -> TransferProgress), the adapter's already-filtered
# importable subset (opaque dicts it NEVER inspects by key), the pure staging lifecycle, and the repo.
#
# acquire_item() answers the whole Phase-4 question for a single gap in one call:
#   "Given this gap, can we search Soulseek, gate-select a copy, download it into isolated staging,
#    hand ONLY the wanted files to the *arr Manual Import, confirm it really imported, and clean up?"
#
# The locked decisions it honors (D-01..D-10, see 04-CONTEXT.md):
#   D-06  housekeeping: purge expired quarantine dirs FIRST (TTL-on-next-run), never raising on it.
#   D-07  fixed collection window: one search, poll until complete or the window deadline, gate ONCE.
#   D-08  one relaxed-query retry on a full decline, then stuck.
#   D-01  no-progress stall detection (no byte advance for acq_stall_seconds) cancels the transfer.
#   D-02  fall to the next gate-accepted candidate on stall/failure; exhausted -> stuck (never loops).
#   D-03  DONE is *arr-confirmed import (verify_imported re-query) — "downloaded" never counts.
#   D-05  verified import -> purge the staging dir.
#   D-06  terminal/ambiguous failure -> quarantine the staging dir + record the reason (not purge).
#   D-10  the import flows through the *arr-agnostic adapter; a Readarr fault quarantines that book
#         only and never gates music (ARR-02).
# IMPORT-04 (Plex reflects new imports) is satisfied EXTERNALLY by the owner's Plex "scan on new media"
# auto-scan (revised D-04) — there is NO Plex call anywhere in this loop.
#
# Clock seam: a `now` callable (default time.monotonic) drives both the collection window and the stall
# timer, so the whole loop is offline-provable with a fake clock and fakes (no sleep, no network).
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from adapters.base import ArrAdapter, GapItem
from core import gate as gate_module
from core.candidate import Candidate
from core.selector import select as select_copy
from state import repo

log = logging.getLogger(__name__)

# REL-02 infra-vs-genuine classifier (RESEARCH Pattern 5 / open question A1) — defined ONCE here and
# imported by core/reconcile.py + core/scheduler.py (Waves 1/2) so the boundary lives in one place.
# An INFRA-class fault (the connection/timeout family below) means "the world is unreachable" — the
# caller must NOT burn a per-item attempt on it (a VPN flap must never push an available album toward
# permanently-unavailable). A genuine "not found" (an adapter returning None, or a non-infra error)
# is distinct and DOES map to stuck/burns an attempt.
#
# acquire.py deliberately does NOT import httpx at module top (it must parse in the offline Python 3.9
# sandbox where httpx may be absent — see build_acquire_clients' lazy import). So INFRA_EXC is built
# behind a guarded import: when httpx is present it is the real tuple of exception types; when absent
# it is the empty tuple (nothing is classified as infra, so _safe_call keeps its pre-Phase-5 behavior
# of mapping every fault to None — safe for the sandbox, and the real classification runs on 3.12).
# httpx exception TYPES are neutral library types (not *arr/slskd wire vocabulary), so the firewall
# over core holds. Downstream modules import THIS single definition — they must not redefine it.
try:
    import httpx as _httpx

    INFRA_EXC = (
        _httpx.ConnectError,
        _httpx.ConnectTimeout,
        _httpx.ReadTimeout,
        _httpx.PoolTimeout,
        _httpx.RemoteProtocolError,
    )
except ImportError:  # offline 3.9 sandbox without httpx — nothing is classifiable as infra here
    INFRA_EXC = ()


@dataclass(frozen=True)
class TransferProgress:
    """The NEUTRAL progress shape the slskd client hands the stall watch (the firewall boundary for
    transfer state). `terminal` is "success" | "failure" | None (still running); `bytes_done` is the
    monotonically-non-decreasing byte counter the watch diffs to detect a no-progress stall. The
    slskd transfer-state + byte-counter wire keys are interpreted in the client, never read here."""

    terminal: Optional[str]
    bytes_done: int


def _relax_query(query: str) -> str:
    """Drop year/edition noise from a search query for the D-08 single relaxed retry.

    Removes 4-digit years, bracketed/parenthesised tags ([FLAC], (1997), (Deluxe Edition)), and the
    common edition words, then collapses whitespace. Pure, defensive, never raises on odd input."""
    import re

    relaxed = re.sub(r"[\[(].*?[\])]", " ", query)          # drop [..]/(..) tags
    relaxed = re.sub(r"\b\d{4}\b", " ", relaxed)            # drop bare 4-digit years
    relaxed = re.sub(
        r"\b(deluxe|remaster(?:ed)?|edition|expanded|anniversary|bonus|special)\b",
        " ",
        relaxed,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", relaxed).strip()


def _search_query(item: GapItem) -> str:
    """Build the slskd free-text search query for a gap from its neutral identity fields only."""
    parts = [p for p in (item.artist_or_author, item.title) if p]
    return " ".join(parts).strip() or (item.title or "")


def _collect_candidates(slskd, query: str, settings, now, build_candidate, poll_hook) -> List[Candidate]:
    """D-07 fixed collection window: submit one search, poll until the client reports it complete or
    the monotonic window deadline elapses, then build neutral Candidates from the accumulated
    responses. Returns [] when the search could not be submitted.

    `now()` is the injected monotonic clock. `poll_hook()` THROTTLES the completeness poll (production
    sleeps acq_poll_seconds between polls; tests inject a clock-advancer / no-op): WITHOUT it the loop
    hammered slskd with thousands of GET /searches/{id} per second over the whole window (the daemon
    log-flood / search-poll busy-loop bug). The hook runs only when the search is NOT yet complete, so
    a search that completes on the first poll (the common offline-test case) never calls it.

    The submitted search is NOT deleted here. slskd retains every tracked search and 409s a duplicate
    query, so the search set must still be cleaned up — but deleting it the instant responses were read
    RACED slskd's own finalize (it logged `Failed to finalize search ... expected 1 row affected 0` on
    nearly every search and could clip late responses). Cleanup is now deferred to slskd.gc_searches(),
    which the scheduler calls BETWEEN batches once every search in the batch has fully finalized."""
    search_id = slskd.search(query)
    if not search_id:
        return []
    deadline = now() + settings.acq_search_window_seconds
    while now() < deadline:
        if slskd.search_is_complete(search_id):
            break
        poll_hook()  # throttle: sleep acq_poll_seconds (prod) / advance the fake clock (tests)
    responses = slskd.search_responses(search_id)
    candidates = []
    for r in responses:
        cand = r if isinstance(r, Candidate) else build_candidate(r)
        if cand is not None:
            candidates.append(cand)
    return candidates


def _accepted_order(
    candidates: List[Candidate], manifest, profile, gate_evaluate
):
    """Run gate.evaluate ONCE over the full candidate set; return (accepted, reasons) where `accepted`
    is the accepted candidates in selection order (best first), or [] on a decline, and `reasons` is
    the gate's explainability trail from the first (authoritative) evaluation.

    Re-uses gate + selector semantics rather than re-judging: the winner is the gate's chosen copy;
    the next-best fallback (D-02) is the selector's ordering of the remaining accepted candidates with
    the winner removed and re-run through the SAME gate, so match precision is never re-derived here.
    The first gate.evaluate call is the authoritative ACCEPT/DECLINE for the set. `reasons` is returned
    so the caller can LOG why a set was declined (the Soularr-opacity fix extended to the live loop:
    'nothing passed the gate' is otherwise blind to whether it was quality/fakeflac/match)."""
    result = gate_evaluate(candidates, manifest, profile)
    reasons = list(getattr(result, "reasons", []) or [])
    if result.decision != "accept" or result.chosen is None:
        return [], reasons

    ordered: List[Candidate] = [result.chosen]
    # Build the runner-up order by re-running the gate over the remaining candidates, repeatedly
    # peeling off each new winner — this re-uses gate/selector ranking without re-judging the match.
    remaining = [c for c in candidates if c is not result.chosen]
    while remaining:
        nxt = gate_evaluate(remaining, manifest, profile)
        if nxt.decision != "accept" or nxt.chosen is None:
            break
        ordered.append(nxt.chosen)
        remaining = [c for c in remaining if c is not nxt.chosen]
    return ordered, reasons


def _summarize_reasons(reasons: List[str], limit: int = 6) -> str:
    """Compact one-line summary of a gate reason trail for a log line (avoid a 250-line spew over a
    full candidate set). Shows the first `limit` reasons and a '(+N more)' tail. Neutral prose only."""
    if not reasons:
        return "(no candidates returned by the search)"
    head = "; ".join(reasons[:limit])
    extra = len(reasons) - limit
    return head + (f" (+{extra} more)" if extra > 0 else "")


def _watch_to_completion(slskd, handle, settings, now, poll_hook) -> str:
    """D-01 no-progress stall watch over the neutral progress seam. Returns one of:
      "success"  — terminal success (proceed to import)
      "failure"  — terminal failure (fall to next candidate)
      "stalled"  — no byte advance for acq_stall_seconds; the transfer is cancelled here (remove=True)

    `handle` is the OPAQUE transfer token the enqueue seam returned (acquire never names a username).
    `now()` is the injected monotonic clock; `poll_hook()` is an injected per-poll side effect the
    tests use to advance the clock deterministically (production passes a real sleep). The stall timer
    resets on ANY byte increase (tolerant of slow-but-legitimate peers, Pitfall 4)."""
    last_bytes = -1
    last_progress_at = now()
    while True:
        progress = slskd.transfer_progress(handle)
        if progress.terminal == "success":
            return "success"
        if progress.terminal == "failure":
            return "failure"
        if progress.bytes_done > last_bytes:
            last_bytes = progress.bytes_done
            last_progress_at = now()
        elif now() - last_progress_at > settings.acq_stall_seconds:
            slskd.cancel_transfer(handle, remove=True)
            return "stalled"
        poll_hook()


def _import_and_verify(item, adapter, staging_path_str, staged_id, conn, settings) -> str:
    """On a completed transfer: ask the adapter for the importable subset (already filtered), then
    import + verify + purge / quarantine / park. Returns "imported" | "partial" | "already-present"
    | "quarantined".

    Core consumes the adapter's list AS-IS (it never reads an *arr key). The outcomes:
      * import lands and the album LEFT wanted          -> "imported" (purge staging)
      * import lands SOME new track files               -> "partial"  (purge staging, revisit cooldown)
      * nothing importable / NO new track files landed  -> "already-present" (owner 2026-06): the files
            we grabbed are already on disk (the dominant churn — a wanted single only exists INSIDE an
            album whose tracks Lidarr already has, so manualimport drops them all / DestinationAlready-
            Exists blocks the move). Re-downloading the SAME source can never change this, so we PARK it
            (purge staging, no quarantine) on the long partial cooldown rather than quarantine->re-arm->
            re-download every cycle. It is never exiled — the cooldown re-checks later in case a fuller
            source appears (faithful to "never permanently ignore a gap").
      * execute_import RAISED (a genuine *arr fault)     -> "quarantined" (move staging aside + record)

    A best-effort (Readarr) adapter degrades its own faults to safe defaults, so a book whose import
    raises quarantines that book only (ARR-02); a book that lands nothing parks like any other item."""
    from core import staging

    def _parked(reason: str) -> str:
        # "Already present": the completed download landed no NEW tracks (already on disk / nothing
        # matched). Not a failure and not retryable against this source — purge staging, mark 'partial'
        # so the scheduler parks it on the revisit cooldown (reconcile won't re-arm it; select_eligible
        # re-checks only after the cooldown). NO quarantine row, NO attempt burned toward exile.
        _purge()
        repo.set_status(conn, item.arr_app, item.arr_id, "partial")
        log.info("%s: already present -> %s (parked, not re-downloading)", _identity(item), reason)
        return "already-present"

    def _quarantine(reason: str) -> str:
        try:
            dest = staging.quarantine_staging(
                staging_path_str, settings.quarantine_root, _batch_label(item)
            )
            repo.record_quarantine(conn, staged_id, str(dest), reason)
        except Exception as e:  # quarantine must never crash the loop; record what we can
            log.warning("quarantine of %s failed (%s); recording reason only", _identity(item), e)
            repo.record_quarantine(conn, staged_id, "", f"{reason} (+quarantine move failed: {e})")
        repo.set_status(conn, item.arr_app, item.arr_id, "quarantined")
        log.info("%s: quarantined -> %s", _identity(item), reason)
        return "quarantined"

    def _purge() -> None:
        # D-05: a landed import (full OR partial) purges staging — the matched files were already moved
        # out by importMode=move, so this only sweeps leftovers (no junk). A failed purge must NOT
        # unmark a real import. Guarded by assert_under_root inside purge_staging.
        try:
            staging.purge_staging(staging_path_str, settings.staging_root)
        except Exception as e:
            log.warning("staging purge of %s hiccuped (ignored): %s", _identity(item), e)

    decisions = adapter.manual_import_candidates(staging_path_str)
    if not decisions:
        # Nothing importable: with filterExistingFiles the *arr drops files whose track it already has,
        # so an all-dropped result is overwhelmingly "we already own these" (a single inside an album we
        # have) — park it, don't quarantine + re-download forever.
        return _parked("nothing importable (tracks already on disk or no track match)")

    # Partial-completion baseline (Phase 5): how many of this album's tracks the *arr already has on
    # disk BEFORE this import. A post-import increase = real tracks landed even if the album stays
    # wanted (only a single/EP/partial was available). A read fault degrades to None -> we fall back to
    # the binary verify below (the pre-partial behavior), never error-skipping a completed import.
    baseline = _imported_track_count(adapter, item)

    try:
        adapter.execute_import(decisions)
    except Exception as e:
        return _quarantine(f"manual import failed: {e}")

    # Four-way verify (D-03 + partial album completion + already-present park, owner policy 2026-06):
    #   * album LEFT the wanted list            -> "imported"        (fully satisfied)
    #   * still wanted but track count INCREASED -> "partial"         (real tracks landed; revisit for the rest)
    #   * still wanted, no new track files       -> "already-present" (we already own them / nothing matched;
    #                                               re-downloading can't help -> PARK, do NOT quarantine)
    if adapter.verify_imported(item):
        _purge()
        repo.set_status(conn, item.arr_app, item.arr_id, "imported")
        log.info("%s: imported %d file(s) -> library", _identity(item), len(decisions))
        return "imported"

    after = _imported_track_count(adapter, item)
    if baseline is not None and after is not None and after > baseline:
        # The good tracks landed but the album is incomplete — take them now (no quarantine), and the
        # scheduler parks the item on a long cooldown so Curator revisits later for the missing tracks
        # (a fuller source may appear) without re-downloading this same partial every cycle.
        _purge()
        repo.set_status(conn, item.arr_app, item.arr_id, "partial")
        log.info("%s: partial import -> %d new track(s) landed; album still incomplete (revisit later for the rest)",
                 _identity(item), after - baseline)
        return "partial"

    # Import executed but NO new track files landed — the files are already on disk (the wanted single
    # lives inside an album we already have; DestinationAlreadyExists / all-dropped). Re-downloading the
    # same source can never change this, so park it instead of quarantining + re-grabbing every cycle.
    return _parked("import landed no new tracks (files already on disk)")


def _cleanup_abandoned(item, slskd, handle, staging_path_str, settings) -> None:
    """Best-effort cleanup of an abandoned (failed/stalled) candidate before falling to the next one —
    the 'no leftover junk' guarantee on the abandon path (D-05 extended to the non-import exits).

    A terminal FAILURE (e.g. 8/10 tracks completed, 2 errored) leaves the peer's already-downloaded
    files on disk and the transfer in slskd's list; a STALL was cancelled inside the watch but its
    on-disk partials can linger. Either way: (1) cancel+remove the transfer from slskd (idempotent —
    cancel_transfer no-ops when the files are already gone from the per-user list), and (2) purge the
    staging leaf dir (guarded by assert_under_root inside purge_staging) so nothing unwanted is left
    behind. NEVER raises — cleanup must never crash the acquisition loop."""
    from core import staging

    try:
        slskd.cancel_transfer(handle, remove=True)
    except Exception as e:  # a cancel hiccup must not block the next-candidate fallback
        log.warning("%s: cancel of abandoned transfer hiccuped (ignored): %s", _identity(item), e)
    try:
        staging.purge_staging(staging_path_str, settings.staging_root)
        log.info("%s: abandoned download cleaned up (cancel+purge) before next candidate", _identity(item))
    except Exception as e:  # a purge hiccup must not block the next-candidate fallback
        log.warning("%s: purge of abandoned download hiccuped (ignored): %s", _identity(item), e)


def _batch_label(item: GapItem) -> str:
    """Deterministic per-item staging label — the FALLBACK staging subdir name used only when the
    client could not derive the real slskd landing dir (the leaf of the peer's remote directory, A2).
    slskd does NOT honor a batchId (A2), so the normal path is the handle's landing_dir_name; this
    label is just a stable, collision-free default so a leaf-less edge case still has an isolated dir."""
    return f"curator-{item.arr_app}-{item.arr_id}"


def _identity(item: GapItem) -> str:
    """Neutral log identity — app + id ONLY (no keys/tokens; T-04-16)."""
    return f"{item.arr_app}:{item.arr_id}"


def _item_row_id(conn: sqlite3.Connection, item: GapItem) -> Optional[int]:
    """The ledger rowid for this gap (the staged_files FK), or None if untracked."""
    row = repo.get_gap(conn, item.arr_app, item.arr_id)
    return row["id"] if row is not None else None


def acquire_item(
    item: GapItem,
    adapter: ArrAdapter,
    slskd: Any,
    conn: sqlite3.Connection,
    settings: Any,
    now: Callable[[], float] = time.monotonic,
    gate_evaluate: Optional[Callable] = None,
    build_candidate: Optional[Callable] = None,
    poll_hook: Optional[Callable[[], None]] = None,
) -> str:
    """Compose the single-item acquisition loop (the Phase-4 verdict). Returns a neutral outcome
    string: "imported" | "partial" | "already-present" | "quarantined" | "stuck". Speaks ONLY neutral
    shapes (firewall holds).

    Mirrors detect_gaps' single-composition-point shape (gap_detector 23-39). The clock + gate +
    candidate-builder + poll seams are injectable so the whole loop is offline-provable with fakes.
    """
    if gate_evaluate is None:
        gate_evaluate = gate_module.evaluate
    if build_candidate is None:
        build_candidate = Candidate.from_slskd
    if poll_hook is None:
        poll_hook = lambda: time.sleep(settings.acq_poll_seconds)  # noqa: E731

    from core import staging

    # 0. Housekeeping (D-06): sweep expired quarantine dirs FIRST. Never raise on it.
    try:
        staging.purge_expired_quarantine(settings.quarantine_root, settings.quarantine_ttl_seconds)
    except Exception as e:
        log.warning("quarantine TTL sweep hiccuped (ignored): %s", e)

    # 1. Fetch the neutral decision inputs (Phase-3 contract). If either is unavailable, the gap is
    #    unresolvable -> stuck WITHOUT searching (don't burn a search on a gap we can't even gate).
    manifest = _safe_call(adapter.get_manifest, item.foreign_id)
    profile = _safe_call(adapter.get_quality_profile, item.quality_profile_id)
    if manifest is None or profile is None:
        log.info("%s: manifest/profile unavailable -> stuck (no search)", _identity(item))
        repo.set_status(conn, item.arr_app, item.arr_id, "stuck")
        return "stuck"

    # 2. Collection-window search (D-07) -> build candidates -> gate ONCE (D-07). On a full decline,
    #    retry ONCE with a relaxed query (D-08); still declined -> stuck.
    repo.set_status(conn, item.arr_app, item.arr_id, "searching")
    query = _search_query(item)
    candidates = _collect_candidates(slskd, query, settings, now, build_candidate, poll_hook)
    accepted, reasons = _accepted_order(candidates, manifest, profile, gate_evaluate)

    if not accepted:
        log.info("%s: query '%s' (%d candidates) declined by gate: %s",
                 _identity(item), query, len(candidates), _summarize_reasons(reasons))
        relaxed = _relax_query(query)
        candidates = _collect_candidates(slskd, relaxed, settings, now, build_candidate, poll_hook)
        accepted, reasons = _accepted_order(candidates, manifest, profile, gate_evaluate)
        if not accepted:
            log.info("%s: nothing passed the gate after relaxed retry '%s' (%d candidates) -> stuck: %s",
                     _identity(item), relaxed, len(candidates), _summarize_reasons(reasons))
            repo.set_status(conn, item.arr_app, item.arr_id, "stuck")
            return "stuck"

    # 3. Download the winner (then the next-best on stall/failure, D-02). Each candidate's slskd
    #    writes land in an isolated per-item staging dir; a stalled/failed peer is cancelled and we
    #    fall to the next accepted candidate. Exhausted -> stuck (never loop forever).
    #
    #    A2 (pinned live 2026-05-31): slskd lands each download under the LEAF of the peer's remote
    #    folder (no batchId / username subdir). The real landing dir is therefore per-candidate and
    #    only known AFTER enqueue, so we resolve the staging path from the handle's neutral landing
    #    dir name once the chosen candidate is enqueued — and point the import + purge at THAT folder.
    item_id = _item_row_id(conn, item)

    for cand in accepted:
        repo.set_status(conn, item.arr_app, item.arr_id, "downloading")
        # Hand the chosen Candidate across; get back an OPAQUE handle (acquire never reads the
        # uploader identity — that SELECTOR-ONLY field stays in the client; the firewall holds).
        #
        # A flaky/offline peer makes slskd 500 the enqueue (or otherwise reject it). The enqueue is
        # non-idempotent (retry=False), so a single submit failed cleanly — fall to the NEXT accepted
        # candidate (D-02) rather than letting the raise escape and error-skip the WHOLE item when 200+
        # other sources are sitting right there. An INFRA_EXC (world unreachable) still propagates so the
        # scheduler classifies it as infra-skip (no burn) — no point churning candidates against a down VPN.
        try:
            handle = slskd.enqueue_candidate(cand)
        except INFRA_EXC:
            raise
        except Exception as e:
            log.info("%s: enqueue of candidate failed (%s) -> next candidate (D-02)", _identity(item), e)
            continue

        # Resolve the REAL landing dir slskd uses (A2): staging_root / <leaf-of-remote-folder>. Fall
        # back to the deterministic per-item label only if the client could not derive a leaf.
        landing_name = getattr(handle, "landing_dir_name", "") or _batch_label(item)
        staging_dir = staging.staging_path(settings.staging_root, landing_name)
        staging_path_str = str(staging_dir)
        staged_id = repo.record_staged_file(conn, item_id, staging_path_str) if item_id else None

        outcome = _watch_to_completion(slskd, handle, settings, now, poll_hook)
        if outcome != "success":
            # stalled or hard failure (e.g. a partial 8/10-track grab) -> clean up the abandoned
            # download (cancel+remove from slskd + purge the staging leaf) so no junk is left behind,
            # THEN fall to the next accepted candidate (D-02).
            _cleanup_abandoned(item, slskd, handle, staging_path_str, settings)
            continue

        # 4. Completed -> import + verify + purge / quarantine (D-03/D-05/D-06). The import source +
        #    purge/quarantine target is the real landing folder resolved above (A2).
        repo.set_status(conn, item.arr_app, item.arr_id, "importing")
        return _import_and_verify(item, adapter, staging_path_str, staged_id, conn, settings)

    log.info("%s: all accepted candidates exhausted -> stuck", _identity(item))
    repo.set_status(conn, item.arr_app, item.arr_id, "stuck")
    return "stuck"


def _imported_track_count(adapter: ArrAdapter, item: GapItem) -> Optional[int]:
    """The partial-completion baseline/after read, made fault-proof. Returns the adapter's neutral
    on-disk track count, or None on ANY fault (incl. infra) — a count read failure must never turn a
    completed import into an error-skip; the caller simply falls back to the binary verify. Readarr
    returns 0 here by design, so its partial branch never fires (an unincreased baseline)."""
    try:
        return int(adapter.imported_track_count(item))
    except Exception as e:
        log.info("%s: track-count read failed (%s) -> no partial baseline (binary verify)",
                 _identity(item), e)
        return None


def _safe_call(fn: Callable, arg) -> Any:
    """Call an adapter decision-input fetch, distinguishing an INFRA outage from a genuine not-found
    (REL-02, A1). The infra-vs-genuine boundary:

      * An INFRA-class fault (INFRA_EXC — connection/timeout family) RE-RAISES so the caller can
        classify it as infra and burn NO per-item attempt (the world is unreachable, not "this gap
        genuinely failed" — a VPN flap must never push an available album toward stuck/permanently-
        unavailable).
      * A genuine not-found — the adapter returning None, OR any NON-infra exception (e.g. a parse /
        value error standing in for a 404-style absence) — maps to None so the caller marks the gap
        stuck. This preserves the pre-Phase-5 behavior for the genuine path (test_acquire stays green).

    The primary (Lidarr) adapter raises on a hard fault; only the connection/timeout subset of those
    is infra. INFRA_EXC is () in the offline sandbox without httpx, so there it degrades to the old
    everything-to-None behavior (safe; the real classification runs on 3.12)."""
    try:
        return fn(arg)
    except INFRA_EXC:
        # The world is unreachable — re-raise so the caller treats it as infra (no burned attempt).
        log.info("decision-input fetch hit an infra fault -> re-raising for infra classification")
        raise
    except Exception as e:
        log.info("decision-input fetch failed (%s) -> treat as genuinely unavailable", e)
        return None


def build_acquire_clients(settings: Any):
    """Construct the SlskdClient + the httpx client backing it. Returns (slskd, clients): the caller
    OWNS the clients and MUST close every one (CR-02, mirrors gap_detector.build_adapters). httpx is
    imported lazily so this module parses where httpx is absent (offline 3.9 sandbox)."""
    import httpx

    from adapters.slskd import SlskdClient

    clients: List[Any] = []
    slskd_client = httpx.Client()
    clients.append(slskd_client)
    slskd = SlskdClient(
        settings.slskd_url,
        settings.slskd_api_key,
        slskd_client,
        search_min_interval=getattr(settings, "slskd_search_min_interval_seconds", 0.0),
    )
    return slskd, clients
