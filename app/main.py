# Curator — Phase 1 health/status stub.
# Proves the image builds, pulls from Docker Hub, runs on synobridge, and can read /data.
# All application logic (gap detection, matching, slskd, import) arrives in Phases 2-6.
import os
from pathlib import Path

from fastapi import FastAPI

app = FastAPI(title="Curator", version="0.1.0-phase1")
DATA = Path("/data")


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
