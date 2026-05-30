---
phase: 02-state-ledger-arr-adapter-gap-detection
plan: 02
subsystem: database
tags: [sqlite, wal, migrations, dedup, upsert, fastapi, ledger]

# Dependency graph
requires:
  - phase: 02-01
    provides: "config.Settings (db_path), conftest tmp_db_path fixture, app/state package marker"
provides:
  - "SQLite-WAL connection factory (state.db.connect) tuned for the single-writer detection path"
  - "Idempotent PRAGMA user_version migration runner (state.db.run_migrations) + schema.sql (the items table)"
  - "items table: UNIQUE(arr_app, arr_id) dedup key, 7-value status CHECK enum, foreign_id Phase-3 anchor, two indexes"
  - "Status-preserving dedup repository (state.repo.upsert_gap/get_gap/set_status/list_by_status)"
  - "FastAPI startup migration hook so a recreated container self-heals its schema"
affects: [02-03 adapter seam, 02-04 gap_detector wiring, phase-3 matching (foreign_id anchor), phase-4 acquisition (set_status lifecycle), phase-5 reliability]

# Tech tracking
tech-stack:
  added: []  # stdlib sqlite3 only — no new runtime dependency this plan
  patterns:
    - "WAL connection: isolation_level=None + check_same_thread=False + 4 PRAGMAs + Row factory"
    - "Idempotent versioned migrations gated on PRAGMA user_version (1-based index = target version)"
    - "DB-layer dedup via UNIQUE constraint + ON CONFLICT DO UPDATE (structural, not algorithmic)"
    - "Status-preserving upsert: SET clause omits status AND discovered_at (RESEARCH Pitfall 1)"
    - "Duck-typed GapItem-shaped input keeps the state layer free of any adapter import (firewall both directions)"

key-files:
  created:
    - app/state/schema.sql
    - app/state/db.py
    - app/state/repo.py
    - app/tests/test_state_repo.py
  modified:
    - app/main.py

key-decisions:
  - "ON CONFLICT SET clause omits BOTH status and discovered_at — an acted-on/first-seen row is never clobbered (the load-bearing STATE-02 rule)"
  - "schema.sql read relative to db.py at import time so the .sql file is the single source of truth for the DDL"
  - "Kept @app.on_event('startup') per PLAN/PATTERNS despite FastAPI deprecation warning — plan-specified pattern, non-fatal"

patterns-established:
  - "WAL connection + idempotent PRAGMA user_version migration runner as the schema-versioning approach"
  - "Status-preserving ON CONFLICT upsert as the canonical dedup primitive"
  - "State-layer tests import only state.* and use a SimpleNamespace GapItem stand-in (no adapter coupling)"

requirements-completed: [STATE-01, STATE-02]

# Metrics
duration: 4min
completed: 2026-05-30
---

# Phase 2 Plan 02: SQLite (WAL) Ledger Summary

**A WAL-mode SQLite ledger with an idempotent PRAGMA-user_version migration runner and a status-preserving `ON CONFLICT(arr_app, arr_id)` upsert that dedups gaps without ever clobbering lifecycle status — the persistent spine for every later phase.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-05-30T22:38:38Z
- **Completed:** 2026-05-30T22:42:00Z
- **Tasks:** 3
- **Files modified:** 5 (4 created, 1 modified)

## Accomplishments
- `items` table with the STATE-02 dedup primitive `UNIQUE(arr_app, arr_id)`, the 7-value STATE-01 status CHECK enum, the `foreign_id` Phase-3 MBID anchor (stored, not acted on), and `idx_items_status` + `idx_items_app_kind`.
- WAL connection factory (`connect`) + idempotent versioned migration runner (`run_migrations`) gated on `PRAGMA user_version`; the only f-string-into-SQL is the loop-controlled version bump.
- Status-preserving dedup repository: `upsert_gap` refreshes metadata + `last_seen_at` ONLY — the `ON CONFLICT` SET clause omits `status` and `discovered_at`, structurally avoiding the #1 STATE-02 pitfall.
- FastAPI `@app.on_event("startup")` hook runs migrations on boot so a recreated container self-heals its schema (STATE-01 criterion 1).
- 5 offline pytest proofs (restart-durability, enum CHECK, dedup, status-preservation, idempotent migrations) — all green locally on Python 3.9 (stdlib sqlite3 only).

## Task Commits

Each task was committed atomically:

1. **Task 1: WAL connection, schema DDL, idempotent migration runner** - `2169575` (feat)
2. **Task 2: dedup-preserving repository (upsert_gap/get_gap/set_status/list_by_status)** - `e7a81b0` (feat)
3. **Task 3: startup migration hook + state-layer test suite** - `c7cc311` (feat)

_Note: TDD tasks were authored implementation-first then proven behaviorally because the state layer touches only stdlib sqlite3 (no new deps); the five named pytest proofs all pass locally and lock the behavior._

## Files Created/Modified
- `app/state/schema.sql` - Migration 0001: the single `items` table + the dedup UNIQUE, status CHECK enum, and two indexes (no out-of-scope Phase 4-6 tables).
- `app/state/db.py` - `connect()` (WAL + synchronous=NORMAL + foreign_keys=ON + busy_timeout=5000 + Row factory) and `run_migrations()` (idempotent, PRAGMA user_version-gated, reads schema.sql relative to the module).
- `app/state/repo.py` - `upsert_gap` (status-preserving ON CONFLICT), `get_gap`, `set_status`, `list_by_status`; all values bound via `?`, duck-typed GapItem input (no adapter import).
- `app/tests/test_state_repo.py` - 5 proofs: `test_persists_across_reconnect`, `test_status_enum`, `test_dedup_no_duplicate`, `test_upsert_preserves_status`, `test_migrations_idempotent`.
- `app/main.py` - Added `from config import settings` + `from state.db import connect, run_migrations` and an `@app.on_event("startup")` `_startup()` hook; existing `/healthz` + `/readyz` untouched; version stays `0.2.0-phase2`.

## Decisions Made
- **ON CONFLICT omits `discovered_at` as well as `status`** — the plan called out `status`; `discovered_at` is also omitted so the original first-sighting timestamp survives re-detection (semantically `discovered_at` = first seen, `last_seen_at` = refreshed). Consistent with the schema comments.
- **schema.sql is the DDL source of truth**, read relative to `db.py` at import — keeps the SQL editable as SQL and avoids duplicating DDL in a Python string.
- **Retained `@app.on_event("startup")`** exactly as PLAN.md and PATTERNS.md specify, despite FastAPI's deprecation notice favoring lifespan handlers. The plan is the contract; the warning is non-fatal and migrating to lifespan is out of scope for this plan.

## Deviations from Plan

None - plan executed exactly as written. (The `discovered_at` omission from the SET clause is an explicit reinforcement of the plan's instruction, which already directed omitting both `status` and `discovered_at` — see PLAN Task 2 action.)

## Issues Encountered
- The plan's Task 1 `<automated>` grep `grep -Eq "CHECK.*pending.*blacklisted"` is line-based and returns non-zero because the CHECK clause intentionally spans two lines in schema.sql. Verified the constraint via a whole-file check (`tr '\n' ' ' | grep`) and, authoritatively, via a behavioral sqlite3 run proving the CHECK rejects a bogus status. Not a defect — a line-vs-multiline grep artifact; the constraint is correct and proven.
- Module-level `TestClient(app)` (the existing test_health.py style) does not fire `on_event("startup")`, so the health tests do not exercise the migration hook and do not require a local `/db` mount. The hook is correctly registered (grep-verified) and fires under real uvicorn startup; on the NAS `/db` is a bind-mount. No local `/db` needed.

## Verification

- **Local gate (Python 3.9 + offline):** AST parse + grep proofs pass for all three tasks; `ON CONFLICT` SET clause has 0 assignments to `status`; schema has exactly 1 CREATE TABLE; no attempts/staged_files/events/peers tables.
- **Behavioral (stdlib sqlite3, ran locally):** WAL active, migrations idempotent (user_version 1→1), dedup yields 1 row, `set_status('imported')` survives a re-upsert (status stays `imported`, metadata still refreshes), bad enum raises `IntegrityError`.
- **pytest:** `app/tests/test_state_repo.py` = 5 passed; full `app/tests` = 8 passed, 2 deprecation warnings (on_event), 0 failures — on Python 3.9. The authoritative green/red gate remains Python 3.12 at CI/NAS.

## Next Phase Readiness
- The ledger spine is ready: 02-03 (adapter seam) produces GapItems that 02-04 (gap_detector) will feed straight into `repo.upsert_gap`. The `foreign_id` column is in place as the Phase-3 matching anchor (column-only, no logic). `set_status` is ready for Phase-4/5 lifecycle transitions.
- No blockers. Note for the phase verifier: run `python -m pytest app/tests -q` on Python 3.12 (CI/NAS) as the authoritative gate.

## Self-Check: PASSED

All created files exist on disk (db.py, schema.sql, repo.py, test_state_repo.py, 02-02-SUMMARY.md) and all three task commits (2169575, e7a81b0, c7cc311) exist in git history.

---
*Phase: 02-state-ledger-arr-adapter-gap-detection*
*Completed: 2026-05-30*
