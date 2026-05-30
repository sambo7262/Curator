# Curator gap detector — the integration point where the *arr seam meets the ledger spine.
# detect_gaps() is the ONLY caller of the adapters and the ONLY orchestrator of the upsert; it is
# the core side of the firewall (PITFALL #6) so it must contain ZERO *arr field names or wire
# vocabulary — it speaks only GapItem + the repo.
#
# Two guarantees are proven *together* here (not just in isolation): the adapters are iterated
# INDEPENDENTLY so a faulting Readarr (breaker-wrapped -> []) can never stop the Lidarr upserts
# (ARR-02 — books never gate music), and re-running detection re-upserts the same identities so
# the ledger never grows duplicate rows (STATE-02 dedup, structural in repo.upsert_gap).
#
# The __main__ block is a one-shot MANUAL trigger for on-NAS UAT only. It is deliberately NOT a
# scheduled loop or daemon — periodic scheduling is Phase 5.
import sqlite3
from typing import Dict, List

from adapters.base import ArrAdapter, GapItem  # noqa: F401  (Protocol + model — the firewall's whole vocabulary)
from state import repo


def detect_gaps(adapters: List[ArrAdapter], conn: sqlite3.Connection) -> Dict[str, int]:
    """Run one detection pass: for each adapter independently, pull its wanted GapItems and
    upsert every one into the ledger; return a per-app count of items seen.

    Independence is the ARR-02 guarantee: each adapter is processed in its own iteration, and the
    breaker-wrapped Readarr returns [] (never raises) on any fault, so a book-side outage yields
    `readarr: 0` while the Lidarr items are fully upserted. Dedup is the STATE-02 guarantee, owned
    by repo.upsert_gap's ON CONFLICT(arr_app, arr_id) clause — a re-run refreshes rows in place
    and preserves any acted-on status rather than inserting duplicates.
    """
    counts: Dict[str, int] = {}
    for adapter in adapters:
        items = adapter.get_wanted()      # breaker-wrapped Readarr returns [] on fault — music unaffected
        for it in items:
            repo.upsert_gap(conn, it)
        counts[adapter.app] = len(items)
    return counts


def build_adapters() -> List[ArrAdapter]:
    """Construct the live adapter list — Lidarr first (primary), Readarr second (best-effort).

    Lidarr is used directly (hard faults surface — it is the primary path); Readarr is wrapped in
    the CircuitBreaker so a fault degrades to [] instead of gating music. Imported lazily so this
    module parses/imports even where httpx is absent (the offline Python-3.9 dev sandbox).
    """
    import httpx

    from adapters.breaker import CircuitBreaker
    from adapters.lidarr import LidarrAdapter
    from adapters.readarr import ReadarrAdapter
    from config import settings

    lidarr = LidarrAdapter(settings.lidarr_url, settings.lidarr_api_key, httpx.Client())
    readarr = CircuitBreaker(
        ReadarrAdapter(settings.readarr_url, settings.readarr_api_key, httpx.Client())
    )
    return [lidarr, readarr]


if __name__ == "__main__":
    # One-shot MANUAL trigger for on-NAS UAT — run `python -m core.gap_detector` to detect once
    # and print the per-app counts. NOT a scheduled loop / daemon (that is Phase 5).
    from state.db import connect, run_migrations
    from config import settings

    _conn = connect(settings.db_path)
    run_migrations(_conn)
    _counts = detect_gaps(build_adapters(), _conn)
    print("gap detection complete:", _counts)
