# Curator startup reconciliation — the REL-02 (D-14) Wave-1 core service.
#
# On every boot, items left in a non-terminal acquisition state are ORPHANS: the previous process
# died (container restart, crash, OOM) mid-flow and nothing is driving them anymore. reconcile_on_
# startup resets those orphans so they re-enter the normal loop, with a verify-by-requery double-
# import guard so an item whose import ACTUALLY completed while we were down is NOT re-imported.
#
# This is the core side of the firewall (PITFALL #6): ZERO *arr/slskd wire vocabulary. It composes
# the NEUTRAL adapter surface (`verify_imported(item) -> bool`, built in Phase 4) + the repo DAOs
# (`list_by_status` / `set_status`) + the SHARED INFRA_EXC classifier IMPORTED from core.acquire
# (the 05-02 deliverable — reused, NEVER redefined, so the infra-vs-genuine boundary lives in one
# place). It never reads an *arr/slskd JSON key.
#
# THE THREE ORPHANABLE STATES (D-14, the load-bearing detail): a kill can strand an item in any of
#   - "searching"   : killed mid-search; NOTHING was ever downloaded.
#   - "downloading" : killed mid-transfer; a partial may be on disk.
#   - "importing"   : killed mid-import; the *arr MAY or may not have finished importing.
# `searching` MUST be reset alongside downloading/importing, because select_eligible only re-picks
# pending/stuck/quarantined/permanently-unavailable — it NEVER selects `searching`. A `searching`
# orphan that is not reset is therefore STRANDED FOREVER (never re-eligible). Resetting it to
# `pending` is the fix (T-05-24 / D-14 "no orphaned in-flight items").
#
# THE DOUBLE-IMPORT GUARD (D-14, Pitfall 3): before resetting an orphan we ask the adapter
# `verify_imported(item)` — "did this item actually LEAVE the *arr wanted list?" (D-03: downloaded
# != imported). If it imported while we were down -> set `imported` and do NOT re-attempt (no
# duplicate grab, no orphaned staging). If it is still wanted -> reset to `pending` so the loop
# retries it cleanly.
#
# THE NO-BURN RULES (D-14 / REL-02 — the two cases that must burn NO attempt):
#   1. A clean reset to `pending` (still-wanted orphan) NEVER increments attempt_count: the
#      interruption was INFRA (the process died), not a genuine acquisition failure. A `searching`
#      orphan never downloaded anything, so its reset is the textbook no-burn case. We call
#      repo.set_status (status only) — NEVER repo.record_attempt — so attempt_count is untouched.
#   2. An INFRA_EXC during the verify (the *arr is unreachable this boot) -> `continue`: leave the
#      row exactly as-is and retry next boot. No status change, no burn — a VPN/*arr flap on boot
#      must never push an available album toward permanently-unavailable.
#
# SINGLE-WRITER (D-16): every ledger write goes through the shared writer lock. CALLER-OWNS-CLOSE
# (CR-02): adapters/clients are built via the injected build_adapters and EVERY client is closed in
# `finally`, mirroring gap_detector.build_adapters' contract.
import logging
import sqlite3
from typing import Any, Callable

from adapters.base import GapItem
from core.acquire import INFRA_EXC   # the SINGLE infra-vs-genuine classifier (05-02) — reused, not redefined
from state import repo

log = logging.getLogger(__name__)

# The non-terminal acquisition states a crash can strand an item in. `searching` is included
# DELIBERATELY (D-14 / T-05-24): select_eligible never re-picks it, so a mid-search-kill orphan that
# is not reset here is stranded forever.
ORPHAN_STATES = ("searching", "downloading", "importing")


def _identity(item: GapItem) -> str:
    """Neutral log identity — app + id ONLY (no keys/tokens; mirrors acquire._identity, T-04-16)."""
    return f"{item.arr_app}:{item.arr_id}"


def _gapitem_from_row(row: sqlite3.Row) -> GapItem:
    """Map a ledger sqlite3.Row to the neutral GapItem the adapter's verify_imported(item) consumes.

    Reads ONLY the neutral ledger columns (the same fields upsert_gap wrote) — never an *arr JSON
    key. `quality_profile_id` is cast to int when present (it is stored as INTEGER); `raw` is left
    empty because verify_imported keys off arr_app/arr_id (the stable identity), not the raw record.
    """
    qpid = row["quality_profile_id"]
    return GapItem(
        arr_app=row["arr_app"],
        arr_id=row["arr_id"],
        kind=row["kind"],
        gap_type=row["gap_type"],
        title=row["title"],
        artist_or_author=row["artist_or_author"],
        foreign_id=row["foreign_id"],
        quality_profile_id=int(qpid) if qpid is not None else None,
        raw={},
    )


def _purge_item_staging(conn: sqlite3.Connection, item_id: int, settings: Any) -> None:
    """Purge every staging dir recorded for an item whose in-flight download we're abandoning on boot.

    A crash/redeploy mid-download leaves the peer's partial (or fully-completed-but-never-imported)
    files on disk under staging_root/<leaf>. Since reconcile is resetting the item to 'pending' for a
    clean re-attempt, those leftovers are orphaned junk — purge them (guarded by assert_under_root
    inside purge_staging) so a restart never accumulates duplicate downloads. Best-effort; never raises
    (a purge hiccup must not block the boot reconcile). No-op when the item has no staged_files rows."""
    from core import staging

    for path in repo.staging_paths_for(conn, item_id):
        try:
            staging.purge_staging(path, settings.staging_root)
        except Exception as e:  # a purge hiccup must never block reconcile
            log.warning("reconcile: purge of orphaned staging '%s' hiccuped (ignored): %s", path, e)


def clear_orphaned_downloads_on_start(settings: Any) -> int:
    """Cancel+remove every download slskd is still tracking on boot — the cross-restart orphan sweep.

    A redeploy/crash mid-download leaves the slskd transfer running (slskd persists downloads across a
    Curator restart); reconcile resets the ledger item to 'pending' to re-attempt it, but without this
    the abandoned transfer keeps going, completes as un-imported junk, and the re-attempt DUPLICATES the
    download. Curator owns slskd's download queue (uploads are separate), and this runs at startup
    before any watch is active, so clearing it is safe. Gated by settings.acq_clear_downloads_on_start
    (default True) so it can be disabled WITHOUT a rebuild. Builds + closes its own slskd client
    (CR-02); an infra fault (slskd/VPN down this boot) is swallowed — the sweep is best-effort and must
    never block startup. Returns the count cancelled."""
    if not getattr(settings, "acq_clear_downloads_on_start", True):
        return 0

    clients = []
    try:
        from core.acquire import build_acquire_clients

        slskd, clients = build_acquire_clients(settings)   # may raise (e.g. missing key offline)
        n = slskd.clear_all_downloads()
        if n:
            log.info("reconcile: cleared %d orphaned slskd download(s) on boot (cross-restart sweep)", n)
        return n
    except Exception as e:  # slskd/VPN unreachable or unconfigured this boot — skip; never block startup
        log.warning("reconcile: orphaned-download sweep skipped (%s)", e)
        return 0
    finally:
        for c in clients:
            try:
                c.close()
            except Exception:
                pass


def rearm_stuck_on_start(conn: sqlite3.Connection, lock: Any, settings: Any) -> int:
    """Clear the retry backoff on every stuck/quarantined/permanently-unavailable row at boot.

    The owner's intent (live-rollout): a container rebuild should re-attempt the WHOLE backlog
    immediately, not honor the per-item 8h/24h backoff or 30-day dormant TTL left over from the prior
    run's failures. This nulls those gates (status->pending, attempt_count=0, next_attempt_at=NULL) so
    select_eligible re-picks them this cycle (still subject to the grace window). Gated by
    settings.acq_reset_stuck_on_start (default True) so it can be turned off later WITHOUT a rebuild.

    Pure DB op — NO adapters, NO network — so it always runs on boot even if the *arr clients can't be
    built. Writes through the shared writer lock (single-writer, D-16). Returns the rows re-armed.
    """
    if not getattr(settings, "acq_reset_stuck_on_start", True):
        return 0
    with lock:
        n = repo.rearm_retryable(conn)
    if n:
        log.info(
            "reconcile: re-armed %d stuck/quarantined/permanently-unavailable item(s) -> pending "
            "(attempt_count + backoff cleared) on boot",
            n,
        )
    return n


def reconcile_on_startup(
    conn: sqlite3.Connection,
    lock: Any,
    build_adapters: Callable[[], Any],
    settings: Any,
) -> None:
    """Reset orphaned in-flight rows on boot with a verify-by-requery double-import guard (D-14).

    For each row in ('searching', 'downloading', 'importing'):
      * verify_imported(item) is True  -> the item landed while we were down: set `imported`, do NOT
        re-import (Pitfall 3 — no double-import, no orphaned staging).
      * verify_imported(item) is False -> still wanted: reset to `pending` so the loop retries it.
        attempt_count is left UNTOUCHED (the interruption was infra, not a genuine fail — D-14).
      * verify_imported raises an INFRA_EXC -> the *arr is unreachable this boot: `continue`, leave
        the row as-is, retry next boot. No status change, NO burned attempt (REL-02).

    Adapters/clients are built lazily via the injected `build_adapters` (gap_detector.build_adapters)
    and EVERY client is closed in `finally` (CR-02). Every ledger write is serialized through the
    shared writer `lock` (single-writer, D-16). Never raises out of a per-row failure — a bad row is
    logged and skipped so one orphan can never block the boot reconcile.
    """
    adapters, clients = build_adapters()
    try:
        by_app = {a.app: a for a in adapters}
        for status in ORPHAN_STATES:
            for row in repo.list_by_status(conn, status):
                adapter = by_app.get(row["arr_app"])
                if adapter is None:
                    # No adapter for this row's app (e.g. Readarr disabled this boot) — leave it as
                    # is; a later boot with the adapter present reconciles it. No burn.
                    log.info(
                        "reconcile: no adapter for %s (status=%s) -> leaving as-is",
                        row["arr_app"],
                        status,
                    )
                    continue

                item = _gapitem_from_row(row)
                try:
                    imported = adapter.verify_imported(item)   # did it actually land while we were down?
                except INFRA_EXC:
                    # The world is unreachable this boot — leave the orphan as-is, retry next boot.
                    # NO status change and NO burned attempt (REL-02): an *arr/VPN flap must never
                    # push an available item toward permanently-unavailable.
                    log.info(
                        "reconcile: %s verify hit an infra fault -> leaving as-is (no burn)",
                        _identity(item),
                    )
                    continue

                with lock:
                    if imported:
                        # It imported while we were down — confirm it, do NOT re-import (Pitfall 3).
                        repo.set_status(conn, item.arr_app, item.arr_id, "imported")
                        log.info(
                            "reconcile: %s already imported during downtime -> imported (no re-import)",
                            _identity(item),
                        )
                    else:
                        # Still wanted — reset to pending for a clean retry. set_status writes the
                        # STATUS ONLY: attempt_count is deliberately NOT touched here (no record_
                        # attempt) because the interruption was infra, not a genuine fail (D-14).
                        repo.set_status(conn, item.arr_app, item.arr_id, "pending")
                        # Purge the abandoned download's leftover staging files so the re-attempt
                        # starts clean (no orphaned junk, no half-album on disk). row["id"] is the
                        # items PK = the staged_files FK.
                        if getattr(settings, "acq_clear_downloads_on_start", True):
                            _purge_item_staging(conn, row["id"], settings)
                        log.info(
                            "reconcile: %s orphaned in '%s' -> reset to pending (no attempt burned)",
                            _identity(item),
                            status,
                        )
    finally:
        for c in clients:
            try:
                c.close()
            except Exception as e:  # a client close must never crash the boot reconcile
                log.warning("reconcile: client close hiccuped (ignored): %s", e)
