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
    """Reconcile the SQLite schema on boot so a recreated container is self-healing (STATE-01, criterion 1)."""
    run_migrations(connect(settings.db_path))


@app.get("/healthz")
def healthz():
    """Liveness — the process is up."""
    return {"status": "ok", "phase": 1}


@app.get("/readyz")
def readyz():
    """Readiness — proves the shared /data mount is present + readable and surfaces the slskd URL."""
    return {
        "data_mount_present": DATA.is_dir(),
        "data_readable": os.access(DATA, os.R_OK),
        "slskd_url": os.getenv("SLSKD_URL"),
    }
