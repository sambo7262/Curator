# Curator — Phase 1 health/status stub.
# Proves the image builds, pulls from Docker Hub, runs on synobridge, and can read /data.
# All application logic (gap detection, matching, slskd, import) arrives in Phases 2-6.
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from config import settings
from state.db import connect, run_migrations

# Make the app's own loggers (core.scheduler etc.) actually emit. uvicorn configures only its own
# loggers, leaving the root at WARNING — so the scheduler's INFO cycle lines (detect/eligible/DRY-RUN)
# were silently dropped while only ERROR-level crashes showed. Honor LOG_LEVEL (default INFO).
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)

app = FastAPI(title="Curator", version="0.2.0-phase2")
DATA = Path("/data")

# Serialize detection passes: the manual /detect trigger (and, later, the Phase 5 scheduler)
# must never run two passes at once, because they share the SINGLE app.state.db writer
# connection — sqlite3 forbids concurrent use of one connection across threads. Non-blocking
# acquire → a second concurrent request gets 409 rather than corrupting the connection.
_detect_lock = threading.Lock()


@app.on_event("startup")
def _startup() -> None:
    """Boot the ledger, reconcile crash orphans, then start the Phase-5 acquisition daemon (REL-01/02).

    Open ONE connection, migrate on it (run_migrations now applies migration_0003 — the backoff/
    attempt columns + the 'permanently-unavailable' status), and RETAIN it on app.state as the single
    long-lived WAL writer connection (BL-02). Then:

      1. Seed app.state.shares_ok = True (the /status surface reads it; ensure_shares maintains it).
      2. reconcile_on_startup (D-14/REL-02): reset orphaned searching/downloading/importing rows from
         a prior crash, with a verify-by-requery guard so an item that imported during downtime is NOT
         re-imported. Uses the SHARED _detect_lock so its writes never collide with /detect.
      3. Start the scheduler daemon (REL-01): a stdlib daemon thread runs a boot cycle then loops on
         the poll interval. It is handed the SAME _detect_lock so a manual /detect and a cycle can
         never write the single connection concurrently (D-16).

    Imports are lazy (inside the handler, the established main.py pattern) so the module parses in the
    offline 3.9 sandbox and tests can monkeypatch build_adapters / Scheduler / reconcile_on_startup.
    """
    from core.gap_detector import build_adapters
    from core.reconcile import (
        reconcile_on_startup,
        rearm_stuck_on_start,
        clear_orphaned_downloads_on_start,
    )
    from core.scheduler import Scheduler

    conn = connect(settings.db_path)
    run_migrations(conn)
    app.state.db = conn
    app.state.shares_ok = True

    # Boot re-arm (owner live-rollout): a container rebuild clears the leftover stuck/quarantined/
    # permanently-unavailable backoff so the WHOLE backlog re-attempts immediately (gated by
    # acq_reset_stuck_on_start, default on). Pure DB op under the shared lock — runs BEFORE the
    # adapter-dependent reconcile so an *arr/VPN flap at boot can never skip it.
    rearm_stuck_on_start(conn, _detect_lock, settings)

    # Cross-restart orphan sweep: cancel downloads slskd is still running from a prior container
    # (a redeploy/crash abandoned them) BEFORE reconcile re-queues their items — so the abandoned
    # transfer can't complete as un-imported junk and the re-attempt won't duplicate it. Self-guarded
    # (a slskd/VPN fault this boot is swallowed) so it never blocks startup.
    clear_orphaned_downloads_on_start(settings)

    # D-14/REL-02: reset crash orphans cleanly (verify-by-requery guard, no double-import, no burn).
    # Defensive: a boot-time infra/config fault while BUILDING the adapters (e.g. *arr unreachable or
    # a transiently-missing key) must NOT crash app startup — the app comes up and the scheduler will
    # reconcile/retry on a later cycle. reconcile_on_startup already swallows per-row infra faults; we
    # additionally guard the build_adapters() call itself so the app is always self-healing on boot.
    try:
        reconcile_on_startup(conn, _detect_lock, build_adapters, settings)
    except Exception:  # noqa: BLE001 — boot reconcile must never block the app coming up (REL-01/02)
        import logging
        logging.getLogger(__name__).exception(
            "startup reconcile failed (continuing; the scheduler will retry)"
        )

    # REL-01: start the daemon, sharing the single writer lock with /detect (D-16). The daemon's own
    # cycle is fully guarded (a cycle exception is logged and the loop continues — Pitfall 5).
    app.state.scheduler = Scheduler(app, settings, _detect_lock)
    app.state.scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    """Stop the scheduler daemon, THEN close the retained writer connection (clean WAL checkpoint).

    Order matters: the daemon must stop (its in-flight cycle drains / the loop joins) BEFORE the
    connection closes, so a worker never touches a closed connection on the way down (BL-02 + REL-01).
    """
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.stop()
        app.state.scheduler = None
    conn = getattr(app.state, "db", None)
    if conn is not None:
        conn.close()
        app.state.db = None


@app.get("/healthz")
def healthz():
    """Liveness — the process is up. `phase` reflects the running build (IN-01: was stale '1')."""
    return {"status": "ok", "phase": 2, "version": app.version}


@app.get("/readyz")
def readyz():
    """Readiness — proves the shared /data mount is present + readable and surfaces the slskd URL."""
    return {
        "data_mount_present": DATA.is_dir(),
        "data_readable": os.access(DATA, os.R_OK),
        "slskd_url": os.getenv("SLSKD_URL"),
    }


@app.post("/detect")
def detect():
    """Manual one-shot gap-detection trigger (on-NAS UAT / ops).

    Runs ONE detection pass on the app's retained single writer connection (app.state.db) —
    NOT a second connection — so there is exactly one writer and no WAL lock contention (this
    is the in-app trigger the Phase 2 plan allowed instead of a separate-process CLI; Phase 5's
    scheduler will call the same detect_gaps() on the same connection). Builds the live adapters
    per call and closes their httpx clients afterward (CR-02). Returns per-app gap counts.
    """
    # Imported here (not at module load) so main.py imports even where httpx is absent
    # (the offline 3.9 sandbox) and so tests can monkeypatch core.gap_detector.build_adapters.
    from core.gap_detector import build_adapters, detect_gaps

    conn = getattr(app.state, "db", None)
    if conn is None:
        raise HTTPException(status_code=503, detail="ledger connection not ready")

    if not _detect_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="detection already in progress")
    try:
        adapters, clients = build_adapters()
        try:
            counts = detect_gaps(adapters, conn)
        finally:
            for client in clients:
                client.close()
        return {"status": "ok", "detected": counts}
    finally:
        _detect_lock.release()


def _row_view(row) -> dict:
    """Map a neutral ledger sqlite3.Row to the status-page view dict.

    Exposes ONLY neutral fields (app, id, title, reason) — never a raw *arr JSON record. `reason`
    falls back to the lifecycle status when no explicit reason column is carried, so the page always
    has something human-readable to show.
    """
    return {
        "app": row["arr_app"],
        "id": row["arr_id"],
        "title": row["title"],
        "reason": row["status"],
    }


def _status_snapshot(conn) -> dict:
    """Build the neutral /status.json snapshot from the ledger (the Phase-6 widget contract, REL-03).

    Pure read over the single app connection: the per-status counts, the three issue buckets
    (stuck / quarantined / permanently-unavailable) as neutral row views, the last shares-ensure
    result (app.state.shares_ok), and the 24h healthy-throughput number.
    """
    # Imported here (not at module load) so main.py parses where the repo's deps are absent and so
    # tests can seed app.state.db freely. repo is firewall-clean (neutral DAOs only).
    from state import repo

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "counts": repo.status_counts(conn),
        "stuck": [_row_view(r) for r in repo.list_by_status(conn, "stuck")],
        "quarantined": [_row_view(r) for r in repo.list_by_status(conn, "quarantined")],
        "permanently_unavailable": [
            _row_view(r) for r in repo.list_by_status(conn, "permanently-unavailable")
        ],
        "shares_ok": bool(getattr(app.state, "shares_ok", True)),
        "throughput": repo.imported_recent(conn, since),
    }


@app.get("/status.json")
def status_json():
    """REL-03 JSON surface — the same data /status renders, for the Phase-6 Homepage widget.

    SECURITY (T-05-21): this exposes the owner's library gaps/titles; it stays on the existing
    LAN/Tailscale-only firewalled port (:8674), no new exposure and no auth (matches /healthz/detect).
    """
    conn = getattr(app.state, "db", None)
    if conn is None:
        raise HTTPException(status_code=503, detail="ledger connection not ready")
    return _status_snapshot(conn)


@app.get("/status", response_class=HTMLResponse)
def status_html():
    """REL-03 status surface — a bare server-rendered HTML page listing stuck / quarantined /
    permanently-unavailable items with counts, reasons, and the healthy-throughput number.

    SECURITY: every interpolated ledger string is html.escape'd by render_status_html (T-05-20, XSS);
    the page exposes library gaps so it stays on the existing LAN/Tailscale-only firewalled port
    (:8674) with no new exposure and no auth (T-05-21, matches the existing posture).
    """
    # Lazy import keeps main.py parsing offline and lets tests seed the ledger freely.
    from core.status_page import render_status_html

    conn = getattr(app.state, "db", None)
    if conn is None:
        raise HTTPException(status_code=503, detail="ledger connection not ready")
    return render_status_html(_status_snapshot(conn))
