# Plan 01-03 Summary — Curator Stub + CI

**Status:** Complete
**Requirements:** INFRA-05

## What was done
- `app/main.py` — FastAPI `Curator` v0.1.0-phase1 with `/healthz` ({"status":"ok","phase":1}) and
  `/readyz` (data_mount_present, data_readable, slskd_url).
- `app/requirements.txt` — pinned `fastapi==0.115.6`, `uvicorn[standard]==0.34.0`.
- `app/tests/test_health.py` — 3 tests (healthz, readyz shape, readyz reflects SLSKD_URL env).
- `pyproject.toml` — pytest config (`pythonpath=app`, `testpaths=app/tests`).
- `Dockerfile` — `python:3.12-slim`, uvicorn on `:8674`.
- `.dockerignore` — excludes `.env`, `.git`, `.planning`, scripts, compose, `*.md` (no baked secrets/bulk).
- `.github/workflows/docker-publish.yml` — `linux/amd64` only (no QEMU), proven action majors
  (checkout@v4, buildx@v3, login@v3, metadata@v5, build-push@v6), tags branch+semver+sha+latest,
  pushes `sambo7262/curator` via `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` repo secrets (already set).

## Verification
- `pytest app/tests` → **3 passed** (run locally in a clean venv).
- Workflow YAML targets `linux/amd64`; no `setup-qemu`.
- CI build green + Docker Hub push confirmed on first push to main; `docker history` secret scan
  in smoke-test (3e).

## Self-Check: PASSED
