# Curator shares ensure/self-heal — the SHARE-01/02 (D-10) Wave-1 core service.
#
# This is the core side of the firewall (PITFALL #6): ZERO *arr/slskd wire vocabulary. It composes
# the NEUTRAL slskd seam the adapter exposes — `get_shared_file_count() -> int` and
# `rescan_shares() -> bool` — and the `shares.files` wire key / the `PUT /api/v0/shares` rescan stay
# in app/adapters/slskd.py. shares.py only ever sees the neutral int + bool.
#
# WHY this exists (D-10, anti-leech): a Soulseek account that shares zero files gets leech-blocked,
# so Curator must keep slskd's shared-file count > 0 on its own. The owner OWNS the share dirs in
# slskd.yml (D-11: /data/media/music + /data/media/books, read-only) — Curator NEVER rewrites
# slskd.yml. It only (a) READS the current shared-file count and (b) triggers a RESCAN if the count
# has dropped to 0, then surfaces the issue on the status page if it can't recover.
#
# EVENTUALLY-CONSISTENT ACROSS CYCLES (Pitfall 6 — the load-bearing subtlety): slskd's rescan is
# ASYNC. `PUT /api/v0/shares` returns 204 immediately and the scan runs in the background, so the
# shared-file count does NOT update within the same cycle. ensure_shares therefore must NOT re-read
# the count after triggering a rescan in the same call — re-reading would observe a stale 0 and
# falsely conclude the rescan failed. The self-heal is surfaced/cleared ACROSS cycles: this cycle
# triggers the rescan and marks shares_ok=False; a LATER cycle re-reads and (if the scan finished)
# observes count > 0 and clears shares_ok back to True. The scheduler (05-04) calls ensure_shares
# once per cycle, which is exactly the cross-cycle cadence this contract relies on.
#
# NON-BLOCKING (D-10): a zero-share state is a LEECH RISK to SURFACE, not a hard stop. ensure_shares
# never raises on a zero count and never blocks acquisition — it records the state on app_state and
# returns a bool; the caller surfaces it (REL-03 status page) but keeps acquiring.
import logging
from typing import Any

log = logging.getLogger(__name__)


def ensure_shares(slskd: Any, app_state: Any) -> bool:
    """Ensure slskd is still sharing files; self-heal via a single async rescan if it dropped to 0.

    Reads the NEUTRAL shared-file count (the adapter reads the wire key, this sees only the int):

      * count > 0  -> sharing is healthy. Mark `app_state.shares_ok = True` and return True.
      * count == 0 -> trigger EXACTLY ONE rescan (`slskd.rescan_shares()`), mark
                      `app_state.shares_ok = False`, and return False. Do NOT re-read the count in
                      this same cycle — the rescan is async (Pitfall 6), so a same-cycle re-read
                      would observe a stale 0. The count is re-checked on a LATER cycle; the share
                      issue is only durably surfaced if it is STILL 0 a cycle after a rescan
                      (the eventually-consistent ACROSS-cycles rule, SHARE-02 "surface if it can't
                      recover").

    Never blocks acquisition and never raises on a zero count (D-10): a zero-share state is a leech
    risk to surface, not a fatal error. (A transport fault inside `get_shared_file_count` /
    `rescan_shares` DOES propagate — the adapter raises on a hard fault so the caller can classify it
    as infra, REL-02 — but a clean zero count is handled here as a self-heal, not an error.)

    Returns True iff sharing is currently healthy (count > 0); False iff a rescan was just triggered
    for a zero count this cycle.
    """
    count = slskd.get_shared_file_count()
    if count > 0:
        app_state.shares_ok = True
        return True

    # Zero shared files — trigger ONE rescan to self-heal. The rescan is async; do NOT re-read the
    # count this cycle (Pitfall 6). rescan_shares() returns True (scan started) or False (a scan is
    # already in progress — "already healing", not an error); either way we surface shares_ok=False
    # this cycle and let a LATER cycle observe the recovered count.
    started = slskd.rescan_shares()
    log.warning(
        "slskd shared-file count is 0 -> triggered a rescan (started=%s); "
        "surfacing a share issue until a later cycle observes a non-zero count",
        started,
    )
    app_state.shares_ok = False
    return False
