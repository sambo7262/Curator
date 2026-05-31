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


def _collect_candidates(slskd, query: str, settings, now, build_candidate) -> List[Candidate]:
    """D-07 fixed collection window: submit one search, poll until the client reports it complete or
    the monotonic window deadline elapses, then build neutral Candidates from the accumulated
    responses. Returns [] when the search could not be submitted.

    `now()` is the injected monotonic clock; a `poll_hook` is NOT used here (the window simply re-polls
    completeness) — the deadline is advanced by the clock the caller controls in tests."""
    search_id = slskd.search(query)
    if not search_id:
        return []
    deadline = now() + settings.acq_search_window_seconds
    while now() < deadline:
        if slskd.search_is_complete(search_id):
            break
    responses = slskd.search_responses(search_id)
    candidates = []
    for r in responses:
        cand = r if isinstance(r, Candidate) else build_candidate(r)
        if cand is not None:
            candidates.append(cand)
    return candidates


def _accepted_order(
    candidates: List[Candidate], manifest, profile, gate_evaluate
) -> List[Candidate]:
    """Run gate.evaluate ONCE over the full candidate set; return the accepted candidates in
    selection order (best first), or [] on a decline.

    Re-uses gate + selector semantics rather than re-judging: the winner is the gate's chosen copy;
    the next-best fallback (D-02) is the selector's ordering of the remaining accepted candidates with
    the winner removed and re-run through the SAME gate, so match precision is never re-derived here.
    The first gate.evaluate call is the authoritative ACCEPT/DECLINE for the set."""
    result = gate_evaluate(candidates, manifest, profile)
    if result.decision != "accept" or result.chosen is None:
        return []

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
    return ordered


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
    import + verify + purge / quarantine. Returns "imported" | "quarantined".

    Core consumes the adapter's list AS-IS (it never reads an *arr key): an empty list means nothing
    importable -> quarantine; a non-empty list is passed straight to execute_import. A primary import
    fault (raise) OR a False verify (downloaded != imported, D-03) quarantines the staging dir and
    records the reason (D-06) — staging is NEVER blind-purged on failure. A best-effort (Readarr)
    adapter already degrades its own faults to safe defaults, so a book fault lands here as an empty
    subset / False verify and quarantines that book only (ARR-02)."""
    from core import staging

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
        return "quarantined"

    decisions = adapter.manual_import_candidates(staging_path_str)
    if not decisions:
        return _quarantine("no importable files in the completed download")

    try:
        adapter.execute_import(decisions)
    except Exception as e:
        return _quarantine(f"manual import failed: {e}")

    if not adapter.verify_imported(item):
        return _quarantine("import not confirmed by re-query (downloaded != imported)")

    # D-05: verified import -> purge the staging dir (guarded by assert_under_root inside purge).
    try:
        staging.purge_staging(staging_path_str, settings.staging_root)
    except Exception as e:  # a failed purge must not unmark a real import
        log.warning("staging purge of %s hiccuped (ignored): %s", _identity(item), e)
    repo.set_status(conn, item.arr_app, item.arr_id, "imported")
    return "imported"


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
    string: "imported" | "quarantined" | "stuck". Speaks ONLY neutral shapes (the firewall holds).

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
    candidates = _collect_candidates(slskd, query, settings, now, build_candidate)
    accepted = _accepted_order(candidates, manifest, profile, gate_evaluate)

    if not accepted:
        relaxed = _relax_query(query)
        candidates = _collect_candidates(slskd, relaxed, settings, now, build_candidate)
        accepted = _accepted_order(candidates, manifest, profile, gate_evaluate)
        if not accepted:
            log.info("%s: nothing passed the gate after relaxed retry -> stuck", _identity(item))
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
        handle = slskd.enqueue_candidate(cand)

        # Resolve the REAL landing dir slskd uses (A2): staging_root / <leaf-of-remote-folder>. Fall
        # back to the deterministic per-item label only if the client could not derive a leaf.
        landing_name = getattr(handle, "landing_dir_name", "") or _batch_label(item)
        staging_dir = staging.staging_path(settings.staging_root, landing_name)
        staging_path_str = str(staging_dir)
        staged_id = repo.record_staged_file(conn, item_id, staging_path_str) if item_id else None

        outcome = _watch_to_completion(slskd, handle, settings, now, poll_hook)
        if outcome != "success":
            # stalled (already cancelled inside the watch) or hard failure -> next candidate.
            continue

        # 4. Completed -> import + verify + purge / quarantine (D-03/D-05/D-06). The import source +
        #    purge/quarantine target is the real landing folder resolved above (A2).
        repo.set_status(conn, item.arr_app, item.arr_id, "importing")
        return _import_and_verify(item, adapter, staging_path_str, staged_id, conn, settings)

    log.info("%s: all accepted candidates exhausted -> stuck", _identity(item))
    repo.set_status(conn, item.arr_app, item.arr_id, "stuck")
    return "stuck"


def _safe_call(fn: Callable, arg) -> Any:
    """Call an adapter decision-input fetch, mapping a not-found (None return OR raise) to None so the
    caller can mark the gap stuck. The primary (Lidarr) adapter raises on a hard fault; a raise here
    means we can't gate the gap, which is correctly surfaced as stuck (not a crash)."""
    try:
        return fn(arg)
    except Exception as e:
        log.info("decision-input fetch failed (%s) -> treat as unavailable", e)
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
    slskd = SlskdClient(settings.slskd_url, settings.slskd_api_key, slskd_client)
    return slskd, clients
