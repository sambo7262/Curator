---
phase: 04-acquisition-staging-clean-import
plan: 01
subsystem: state-ledger + config + test-fixtures
tags: [migration, sqlite, state-machine, config, fixtures, offline-test]
requires:
  - "migration 0001 items table (Phase 2)"
  - "Settings.from_env() SP-4 pattern (Phase 2/3)"
  - "Candidate.from_slskd factory (Phase 3, candidate.py)"
provides:
  - "items.status widened to the 11-value acquisition state machine (downloading/importing/quarantined/stuck added)"
  - "staged_files ledger table + record_staged_file / record_quarantine DAOs"
  - "8 env-overridable Phase-4 acquisition tunables on Settings (no Plex)"
  - "offline slskd + manualimport fixtures every later 04-wave test asserts against"
affects:
  - "04-02 slskd client tests (consume slskd/* fixtures)"
  - "04-03 adapter import tests (consume manualimport/* fixtures)"
  - "04-04 acquire loop (writes the new statuses + staged_files rows)"
tech-stack:
  added: []   # no new runtime dependency (T-04-SC: respx already pinned, no install this plan)
  patterns:
    - "SQLite CHECK-widen via table rebuild inside the runner's single BEGIN/COMMIT (WR-02)"
    - "?-placeholder-only DAOs (T-04-01) + _now_iso() timestamps"
    - "[ASSUMED]-marked fixtures with named-constant intent pending the 04-05 live probes (A1/A3)"
key-files:
  created:
    - app/state/migration_0002.sql
    - app/tests/fixtures/slskd/search_responses.json
    - app/tests/fixtures/slskd/transfer_completed.json
    - app/tests/fixtures/slskd/transfer_stalled.json
    - app/tests/fixtures/slskd/transfer_failed.json
    - app/tests/fixtures/manualimport/get_mapping.json
    - app/tests/fixtures/manualimport/expected_post.json
  modified:
    - app/state/db.py
    - app/state/repo.py
    - app/config.py
    - app/tests/conftest.py
    - app/tests/test_state_repo.py
decisions:
  - "Plex fields EXCLUDED from Settings (revised D-04 — Curator does not call Plex)"
  - "Enum widen ships in migration_0002 BEFORE staged_files so the status-preserving upsert protects the new states for free (Pitfall 6)"
metrics:
  duration_minutes: 5
  completed: 2026-05-31
  tasks: 3
  files_created: 7
  files_modified: 5
  tests_added: 11
  suite: "139 passed (was 128)"
---

# Phase 4 Plan 01: Acquisition State-Machine Foundation Summary

Widened the SQLite ledger into the Phase-4 acquisition state machine (migration 0002: 4 new lifecycle states + `staged_files` table), added the two `staged_files` DAOs and the 8 env-overridable acquisition tunables (no Plex), and landed the offline slskd/manualimport fixtures every later-wave test will assert against — all proven by 11 new tests with zero runtime dependency added.

## What Was Built

**Task 1 — migration 0002 (`669dbee`):** `migration_0002.sql` widens `items.status` to add `downloading/importing/quarantined/stuck` (11 total) via the standard SQLite table-rebuild (RENAME → recreate with the widened CHECK, copying all columns + the `UNIQUE(arr_app, arr_id)` dedup primitive verbatim → `INSERT … SELECT *` → DROP → recreate both 0001 indexes `IF NOT EXISTS`), then creates the `staged_files` table (`item_id` FK, `staging_path`, `quarantine_path`, `failure_reason`, `quarantined_at`, `created_at`) plus an `item_id` index. `db.py` registers `("0002", _SCHEMA_0002)` on `MIGRATIONS` (0001 untouched). The enum widen comes first so `set_status('downloading')` never hits an IntegrityError (Pitfall 6). The whole rebuild runs inside the runner's single `BEGIN/COMMIT` (WR-02 / T-04-03).

**Task 2 — DAOs + config (`8680149`):** `repo.record_staged_file(conn, item_id, staging_path) -> int` (returns rowid) and `repo.record_quarantine(conn, staged_file_id, quarantine_path, reason)` — both `?`-placeholder-only with `_now_iso()` (T-04-01). `Settings` gains `slskd_url`, `slskd_api_key` (Optional, T-04-02), `acq_search_window_seconds` (12.0), `acq_stall_seconds` (600.0), `acq_poll_seconds` (5.0), `staging_root`, `quarantine_root`, `quarantine_ttl_seconds` (604800.0), each read in `from_env()` with numerics cast via `float()` for fail-fast. **No `plex_*` fields** (revised D-04).

**Task 3 — fixtures + tests (`ba09adc`):** Six offline fixtures: `slskd/search_responses.json` (a clean 12-track FLAC album that builds cleanly through `Candidate.from_slskd` + a weaker MP3-192 runner-up), `slskd/transfer_{completed,stalled,failed}.json` (terminal-success / no-byte-progress / failure snapshots with `state`+`bytesTransferred`+`size`+`percentComplete`), and `manualimport/{get_mapping,expected_post}.json` (two importable resources with empty rejections + real tracks, one rejected resource; the expected ManualImport-Move POST body for the importable set). `conftest.load_fixture` documented for subpath loading. 11 tests in `test_state_repo.py` prove migrate→v2, the 4 new statuses write cleanly, pre-existing rows survive the rebuild (count + identity + status), idempotent re-migrate, the staged_files DAO roundtrip, config defaults/override/fail-fast (+ Plex-absence), and that the fixtures build ≥2 Candidates and filter to exactly the importable set.

## Provenance Markers ([ASSUMED] — pending 04-05 live probes)

- slskd terminal/in-progress/failed `state` strings (A3) — fixtures use plausible compound strings (`"Completed, Succeeded"`, `"InProgress"`, `"Completed, Errored"`) and the fixture docstrings instruct later-wave tests to reference them via named constants so the live strings pin in one place.
- ManualImport `importMode` casing + `files[]` envelope (A1) — `expected_post.json` uses `importMode: "Move"`, marked [ASSUMED].

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Stray `conn2.close()` displaced into a new test**
- **Found during:** Task 3 (first `pytest` run of `test_state_repo.py`).
- **Issue:** The Edit that appended the Phase-4 test block landed after the file's final `conn2.close()` line, leaving a `NameError`-raising `conn2.close()` at the bottom of an unrelated new test.
- **Fix:** Removed the stray line and restored `conn2.close()` to its proper home in `test_migration_and_version_bump_commit_together`.
- **Files modified:** app/tests/test_state_repo.py
- **Commit:** ba09adc

**2. [Rule 3 — Blocking accuracy] `load_fixture` return-type hint**
- **Issue:** The conftest loader was annotated `-> dict`, but the Phase-4 fixtures (search_responses, get_mapping) are JSON lists loaded through the same helper.
- **Fix:** Loosened the annotation and documented subpath loading. No behavior change.
- **Files modified:** app/tests/conftest.py
- **Commit:** ba09adc

## Verification

- `cd app && python3 -m pytest tests/test_state_repo.py -x -q` — 17 passed.
- `cd app && python3 -m pytest` — **139 passed** (was 128), 4 pre-existing FastAPI deprecation warnings (out of scope).
- `cd app && python3 -c "import config; config.Settings.from_env()"` — succeeds.
- A fresh tmp DB migrates to `user_version` 2 and accepts all 11 status values (verified directly).

## Known Stubs

None. Fixtures carry `[ASSUMED]` provenance for two live-probe-pending values (A1/A3) but are fully valid, parseable, and consumed by passing tests; resolution is scoped to 04-05's live NAS probes, not a code stub.

## Self-Check: PASSED

- migration_0002.sql, db.py, repo.py, config.py, conftest.py, test_state_repo.py and all six fixture JSONs present on disk.
- Commits 669dbee, 8680149, ba09adc all present in `git log`.
