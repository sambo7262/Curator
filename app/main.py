# Curator — Phase 1 health/status stub.
# Proves the image builds, pulls from Docker Hub, runs on synobridge, and can read /data.
# All application logic (gap detection, matching, slskd, import) arrives in Phases 2-6.
import os
from pathlib import Path

from fastapi import FastAPI

from config import settings
from state.db import connect, run_migrations

app = FastAPI(title="Curator", version="0.2.0-phase2")
DATA = Path("/data")


@app.on_event("startup")
def _startup() -> None:
    """Reconcile the SQLite schema on boot so a recreated container is self-healing (STATE-01, criterion 1).

    Open ONE connection, migrate on it, and RETAIN it on app.state — this is the single
    long-lived writer connection the WAL single-writer design calls for (BL-02). Later request
    handlers reuse app.state.db rather than opening their own, and it is closed on shutdown so
    the WAL is checkpointed deterministically rather than left to GC.
    """
    conn = connect(settings.db_path)
    run_migrations(conn)
    app.state.db = conn


@app.on_event("shutdown")
def _shutdown() -> None:
    """Close the retained writer connection so the WAL checkpoints cleanly (BL-02)."""
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
