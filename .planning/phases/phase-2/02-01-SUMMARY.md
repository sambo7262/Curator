---
phase: 02-state-ledger-arr-adapter-gap-detection
plan: 01
subsystem: testing
tags: [pytest, respx, httpx, fixtures, sqlite, docker-compose, config, dataclass]

# Dependency graph
requires:
  - phase: 01-vpn-routed-networking-foundation
    provides: "Curator FastAPI stub (app/main.py), docker-compose curator service with *arr env vars, single /data mount, pyproject pythonpath=['app']"
provides:
  - "Offline recorded *arr JSON fixtures (6 files) matching the verified Servarr v1 paged envelope"
  - "Shared conftest.py with tmp_db_path, load_fixture, and an offline httpx-mock client factory"
  - "app/config.py — frozen Settings dataclass + module-level singleton reading LIDARR/READARR URL+key and DB_PATH from env"
  - "Package markers app/state, app/adapters, app/core (app/ stays the import root)"
  - "Dedicated /db bind-mount + DB_PATH wired into docker-compose and .env.example (DB NOT under /data)"
  - "Approved runtime dep httpx==0.28.1 and dev dep respx==0.22.0"
affects: [02-02 ledger, 02-03 adapters, 02-04 gap_detector, phase-3 matching]

# Tech tracking
tech-stack:
  added: [httpx==0.28.1, respx==0.22.0, pytest]
  patterns:
    - "Frozen @dataclass(frozen=True) Settings singleton reading env via os.getenv only — single declarative config seam, keys never logged"
    - "Recorded JSON fixtures + respx/httpx.MockTransport conftest fixtures so adapter/ledger tests run fully offline with no live Lidarr/Readarr"
    - "DB on its OWN local /db mount, never under the shared /data tree (WAL safety + trust-boundary isolation)"

key-files:
  created:
    - app/config.py
    - app/state/__init__.py
    - app/adapters/__init__.py
    - app/core/__init__.py
    - app/requirements-dev.txt
    - app/tests/conftest.py
    - app/tests/fixtures/lidarr_missing.json
    - app/tests/fixtures/lidarr_cutoff.json
    - app/tests/fixtures/lidarr_cutoff_page2.json
    - app/tests/fixtures/readarr_missing.json
    - app/tests/fixtures/readarr_empty.json
    - app/tests/fixtures/readarr_garbage.json
  modified:
    - app/requirements.txt
    - docker-compose.yml
    - .env.example
    - app/main.py

key-decisions:
  - "httpx==0.28.1 (encode/httpx) and respx==0.22.0 (lundberg/respx) human-approved as pinned via the Phase 2 blocking package-legitimacy checkpoint — no version changes"
  - "respx is dev/test-only (requirements-dev.txt), never in the runtime image — no Phase 3-6 packages added (PITFALL #5)"
  - "SQLite DB lives on a dedicated /db mount, not under /data, because WAL is unsafe on network shares and the DB is a distinct trust boundary"

patterns-established:
  - "Config seam: one frozen Settings dataclass + module-level `settings` singleton; env var names match compose verbatim; DB_PATH is the only new name"
  - "Offline test substrate: recorded Servarr paged-envelope fixtures + conftest mock-transport client; cutoff fixture is genuinely multi-page; garbage fixture forces the defensive _map() skip path"

requirements-completed: [STATE-01]

# Metrics
duration: 9min
completed: 2026-05-30
---

# Phase 2 Plan 01: Wave 0 Offline Test Substrate + Config + /db Mount Summary

**Offline *arr JSON fixtures + respx-backed conftest, a frozen-dataclass config singleton (incl. DB_PATH), and a dedicated /db bind-mount — the deterministic, network-free foundation every later Phase 2 plan tests against.**

## Performance

- **Duration:** ~9 min (across the original autonomous run + this checkpoint finalization)
- **Started:** 2026-05-30T15:26:50-07:00 (Task 1 commit)
- **Completed:** 2026-05-30T15:35:50-07:00 (checkpoint-resolution commit)
- **Tasks:** 3 autonomous + 1 blocking checkpoint (approved)
- **Files modified:** 16 (12 created, 4 modified)

## Accomplishments
- Six recorded Servarr v1 paged-envelope fixtures so Plans 02-04 need no live Lidarr/Readarr — including a genuinely multi-page cutoff fixture (forces the paging loop) and a garbage fixture (missing-`id`, non-dict, null-`title` records to drive graceful Readarr degradation).
- `app/tests/conftest.py` exposing `tmp_db_path`, `load_fixture`, and an offline httpx-mock client factory serving `/api/v1/wanted/missing` and `/api/v1/wanted/cutoff`.
- `app/config.py` — frozen `Settings` dataclass + `settings` singleton reading `LIDARR_URL/KEY`, `READARR_URL/KEY` (names matching compose verbatim) and the new `DB_PATH` (default `/db/curator.sqlite`); keys never logged.
- Package markers for `app/state`, `app/adapters`, `app/core` (app/ stays the import root).
- Dedicated `/db` bind-mount (`/volume1/docker/curator/db:/db`, writable, NOT under `/data`) + `DB_PATH` wired into `docker-compose.yml` and documented in `.env.example`; `app/main.py` version bumped to `0.2.0-phase2`.
- Package-legitimacy checkpoint cleared: `httpx==0.28.1` and `respx==0.22.0` human-approved as pinned.

## Task Commits

Each task was committed atomically:

1. **Task 1: config.py, package markers, dependency pins** - `347d413` (feat)
2. **Task 2: recorded *arr JSON fixtures + shared conftest** - `852af5c` (test)
3. **Task 3: /db mount + DB_PATH into compose and .env.example** - `98e9e2b` (feat)
4. **Checkpoint resolution: record package-legitimacy approval** - `48cadd1` (docs)

## Files Created/Modified
- `app/config.py` - Frozen `Settings` dataclass + `settings` singleton; *arr URLs/keys + `DB_PATH` from env only.
- `app/state/__init__.py`, `app/adapters/__init__.py`, `app/core/__init__.py` - Package markers (import-root convention).
- `app/requirements.txt` - Added `httpx==0.28.1` (runtime, approved).
- `app/requirements-dev.txt` - `pytest` + `respx==0.22.0` (dev/test only, approved).
- `app/tests/conftest.py` - Shared fixtures: `tmp_db_path`, `load_fixture`, offline httpx-mock client.
- `app/tests/fixtures/*.json` - 6 recorded Servarr paged-envelope fixtures (lidarr missing/cutoff/cutoff_page2; readarr missing/empty/garbage).
- `docker-compose.yml` - `DB_PATH` env + `/volume1/docker/curator/db:/db` volume on curator service.
- `.env.example` - Documented `DB_PATH` + PUID/PGID + local-FS (WAL) requirement.
- `app/main.py` - Version string bumped to `0.2.0-phase2`.

## Decisions Made
- Packages approved AS PINNED via the blocking checkpoint — `httpx==0.28.1` (encode/httpx, runtime) and `respx==0.22.0` (lundberg/respx, dev-only). No version changes; stale "gated by blocking checkpoint" / "to confirm on first CI build" comments cleared to record the approval.
- DB on a dedicated `/db` mount, never under `/data` — WAL safety + trust-boundary isolation (T-02-02).

## Deviations from Plan

None - plan executed exactly as written. The plan's `<task type="checkpoint:human-verify" gate="blocking-human">` was an expected, planned gate (not a deviation); it was resolved by human approval of the pinned versions.

## Issues Encountered
- **Local test execution not possible:** the dev sandbox is Python 3.9 + offline, so `httpx`/`respx` cannot be pip-installed and `pytest` cannot exercise the new fixtures/conftest here. This is expected per RESEARCH "Environment Availability". Local verification was AST-parse + grep per each task's `<automated>` block (all passed). **The real green/red pytest gate is `pytest app/tests -q` on Python 3.12 at CI/NAS, where the network exists and the approved deps install** — run it before marking Phase 2 verified.

## User Setup Required
None - no external service configuration required for this plan. NAS operators must ensure `/volume1/docker/curator/db` exists and is owned by PUID/PGID 1031:65536 before first run (documented in `.env.example`); this is wired but not yet exercised until the ledger startup hook lands in Plan 02-02.

## Next Phase Readiness
- Plans 02-02 (SQLite-WAL ledger), 02-03 (ArrAdapter seam), and 02-04 (gap_detector) now have a deterministic offline test substrate, a config seam (incl. `DB_PATH`), import-root package markers, and approved deps — all blocking scaffolding removed.
- No blockers. Reminder for the phase verifier: run the behavioral pytest suite on Python 3.12 (CI/NAS), since it could not run in the local Python-3.9 offline sandbox.

## Self-Check: PASSED

All 13 claimed files present on disk; all 4 commits (347d413, 852af5c, 98e9e2b, 48cadd1) present in git history.

---
*Phase: 02-state-ledger-arr-adapter-gap-detection*
*Completed: 2026-05-30*
