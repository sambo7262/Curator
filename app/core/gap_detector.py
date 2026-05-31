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
import logging
import sqlite3
from typing import Any, Dict, List, Tuple

from adapters.base import ArrAdapter  # the Protocol the core depends on (the firewall's interface)
from state import repo

log = logging.getLogger(__name__)


def detect_gaps(adapters: List[ArrAdapter], conn: sqlite3.Connection) -> Dict[str, int]:
    """Run one detection pass: for each adapter independently, pull its wanted GapItems and
    upsert every one into the ledger; return a per-app count of items seen.

    Independence is the ARR-02 guarantee: each adapter is processed in its own iteration, and the
    breaker-wrapped Readarr returns [] (never raises) on any fault, so a book-side outage yields
    `readarr: 0` while the Lidarr items are fully upserted. Dedup is the STATE-02 guarantee, owned
    by repo.upsert_gap's ON CONFLICT(arr_app, arr_id) clause — a re-run refreshes rows in place
    and preserves any acted-on status rather than inserting duplicates.

    D-15 batch transaction: every per-adapter upsert in the pass is wrapped in ONE explicit
    BEGIN/COMMIT so the whole pass is a single fsync (synchronous=FULL is unchanged — durability
    preserved, ~100x faster than per-row commits over the ~1493-gap ledger). On ANY exception the
    pass ROLLBACKs wholly and re-raises, so a mid-pass fault never leaves a half-written pass behind.
    The connection is autocommit (isolation_level=None) so these BEGIN/COMMIT statements are the
    explicit transaction boundary (the same idiom run_migrations uses). The ON CONFLICT dedup +
    status/discovered_at preservation in repo.upsert_gap is unchanged — the txn only batches the fsync.
    """
    counts: Dict[str, int] = {}
    conn.execute("BEGIN")
    try:
        for adapter in adapters:
            items = adapter.get_wanted()  # breaker-wrapped Readarr returns [] on fault — music unaffected
            for it in items:
                repo.upsert_gap(conn, it)
            counts[adapter.app] = len(items)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return counts


def build_adapters() -> Tuple[List[ArrAdapter], List[Any]]:
    """Construct the live adapter list and the httpx clients backing it.

    Lidarr first (primary, used directly — hard faults surface); Readarr second (best-effort,
    CircuitBreaker-wrapped so a fault degrades to [] instead of gating music). A missing
    READARR_API_KEY disables Readarr gracefully (skip it) rather than crashing — music is never
    gated by a book-side misconfiguration (ARR-02 / CR-01).

    Returns (adapters, clients): the caller OWNS the clients and MUST close every one (each
    httpx.Client holds a connection pool / sockets) — see the __main__ try/finally below (CR-02).
    Imported lazily so this module parses/imports even where httpx is absent (offline 3.9 sandbox).
    """
    import httpx

    from adapters.breaker import CircuitBreaker
    from adapters.lidarr import LidarrAdapter
    from adapters.readarr import ReadarrAdapter
    from config import settings

    adapters: List[ArrAdapter] = []
    clients: List[Any] = []

    lidarr_client = httpx.Client()
    clients.append(lidarr_client)
    adapters.append(LidarrAdapter(settings.lidarr_url, settings.lidarr_api_key, lidarr_client))

    # Readarr is best-effort: a missing key (or any construction error) disables it, it never
    # crashes the primary music path. The client is created first and closed if construction fails.
    readarr_client = httpx.Client()
    try:
        readarr = ReadarrAdapter(settings.readarr_url, settings.readarr_api_key, readarr_client)
        clients.append(readarr_client)
        adapters.append(CircuitBreaker(readarr))
    except ValueError as e:   # e.g. READARR_API_KEY unset -> skip Readarr (ARR-02 graceful degrade)
        readarr_client.close()
        log.warning("Readarr disabled (%s); continuing music-only", e)

    return adapters, clients


if __name__ == "__main__":
    # One-shot MANUAL trigger for on-NAS UAT — run `python -m core.gap_detector` to detect once
    # and print the per-app counts. NOT a scheduled loop / daemon (that is Phase 5).
    from state.db import connect, run_migrations
    from config import settings

    _conn = connect(settings.db_path)
    run_migrations(_conn)
    _adapters, _clients = build_adapters()
    try:
        _counts = detect_gaps(_adapters, _conn)
        print("gap detection complete:", _counts)
    finally:
        # Own the client lifecycle — close every httpx.Client so the one-shot leaks no sockets (CR-02).
        for _c in _clients:
            _c.close()
        _conn.close()
