---
phase: 5
plan: 01
subsystem: state-persistence-and-config
tags: [migration, sqlite, backoff, eligibility, config, state-03, gap-03]
dependency_graph:
  requires:
    - "migration_0002 (Phase-4 acquisition state machine + table-rebuild technique)"
    - "repo.upsert_gap status-preserving dedup (Phase 2 — keeps discovered_at, load-bearing for grace)"
    - "Settings.from_env frozen singleton + Phase-3/4 fail-fast cast precedent"
  provides:
    - "items.attempt_count / next_attempt_at / last_checked_at columns + permanently-unavailable status"
    - "repo.select_eligible / record_attempt / status_counts / imported_recent / backoff_for DAOs"
    - "Settings Phase-5 scheduler/backoff tunables (the daemon, reconcile, status page all read these)"
    - "conftest seed_v0002_ledger + frozen_clock fixtures for downstream Wave 1-3 tests"
  affects:
    - "app/state schema (user_version 2 -> 3) + repo DAO surface + config Settings"
tech-stack:
  added: []
  patterns:
    - "Explicit-column-list INSERT ... SELECT table rebuild (shapes diverge -> NOT SELECT *) inside the runner's single BEGIN/COMMIT"
    - "Two-branch eligibility predicate (grace+backoff retryable OR dormant-TTL permanently-unavailable), all ?-bound"
    - "Capped exponential backoff_for via index-clamp into a constant ladder"
    - "Frozen-Settings env tunables with fail-fast int/float casts + truthy/falsey bool parse"
key-files:
  created:
    - app/state/migration_0003.sql
    - app/tests/test_migration_0003.py
    - app/tests/test_eligibility.py
    - app/tests/test_backoff.py
  modified:
    - app/state/db.py
    - app/state/repo.py
    - app/config.py
    - app/tests/conftest.py
    - app/tests/test_state_repo.py
decisions:
  - "stuck AND quarantined are retry-eligible — that retry IS the backoff mechanism (OQ-2 resolved per D-08)"
  - "New columns omitted from the INSERT carry-over so preserved rows take table defaults (attempt_count=0, NULLs)"
  - "Phase-5 tunables carry NO secrets (D-13 defers Pushover to Phase 6)"
metrics:
  duration: ~25m (verify-and-commit of pre-written tree)
  completed: 2026-05-31
---

# Phase 5 Plan 01: Autonomy Persistence + Config Foundation Summary

Laid the Phase-5 persistence + config substrate: migration_0003 adds the backoff/attempt/dormant columns and the `permanently-unavailable` status while preserving the live ~1493-row ledger, the repo gains the eligibility/backoff/attempt/counts DAO surface the scheduler reads and writes, and Settings carries the 7 env tunables (grace/poll/concurrency/attempts/dormant + the dry-run + kill-switch flags) that drive the daemon and bounded rollout — all firewall-clean, `?`-bound, and fail-fast.

## What Was Built

- **migration_0003.sql** — single coherent table-rebuild mirroring migration_0002: renames `items` aside, recreates it with all 13 prior columns PLUS `attempt_count INTEGER NOT NULL DEFAULT 0`, `next_attempt_at TEXT`, `last_checked_at TEXT`, and the status CHECK widened to add `permanently-unavailable`. Carry-over uses an **explicit 13-column `INSERT ... SELECT`** (not `SELECT *`, because shapes now diverge); the three new columns are deliberately omitted so every preserved row takes its table defaults (0 / NULL / NULL). Recreates `idx_items_status` + `idx_items_app_kind` and adds `idx_items_next_attempt`. No `;` inside any string literal (runner statement-split safety). Runs inside the runner's single BEGIN/COMMIT so a mid-migration crash rolls back wholly.
- **db.py** — registers `_SCHEMA_0003` loader + appends `("0003", _SCHEMA_0003)` after 0002 (shipped migrations untouched).
- **repo.py** — `backoff_for(attempt) -> int` + `BACKOFF_SECONDS=[3600,21600,86400]` (D-08, capped 1h/6h/24h, clamps non-positive); `select_eligible(conn, grace_cutoff, now, dormant_cutoff, room)` two-branch predicate (pending/stuck/quarantined past grace AND past backoff, OR permanently-unavailable past the 30-day dormant TTL), `ORDER BY discovered_at ASC LIMIT room`; `record_attempt(...)`; `status_counts(conn)`; `imported_recent(conn, since_iso)`. Every value `?`-bound, every timestamp via `_now_iso()`, zero *arr/slskd vocab.
- **config.py** — 7 frozen-Settings fields: `acq_enabled=True` (D-05 kill-switch), `acq_dry_run=False` (D-05), `max_concurrent=3` (D-04), `acq_poll_interval_seconds=21600.0` (D-03 6h), `acq_grace_seconds=259200.0` (D-01 3d), `acq_max_attempts=3` (D-07), `acq_dormant_seconds=2592000.0` (D-09 30d). `from_env` reads each with a fail-fast int/float cast (bools via truthy/falsey parse). No secrets.
- **Tests + fixtures** — `conftest.py` `seed_v0002_ledger` (v1-only-then-full-migrate preservation harness analog) + `frozen_clock`; `test_migration_0003.py` (preservation + defaults + enum + idempotent), `test_eligibility.py` (grace/backoff/dormant/order/limit/terminal-never), `test_backoff.py` (ladder/cap/clamp/shape), plus Phase-5 repo + config cases in `test_state_repo.py`.

## must_haves Verification

| Truth | Status | Evidence |
|-------|--------|----------|
| v0002 -> v0003 migrates preserving ~1493 rows + defaulted new columns | PASS | `test_v0002_rows_survive_migration_to_v0003` + `test_new_columns_default_correctly` (count unchanged, defaults 0/NULL/NULL) |
| status enum accepts `permanently-unavailable` | PASS | `test_permanently_unavailable_status_accepted` (set_status no longer raises) |
| select_eligible respects grace + backoff + 30d-dormant | PASS | 8 cases in `test_eligibility.py` (grace held/elapsed, backoff future/past, dormant recheck/recent, terminal-never, order+limit) |
| backoff_for = 1h/6h/24h capped for attempts 1/2/3+ | PASS | `test_backoff_schedule_1h_6h_24h` + `test_backoff_caps_at_24h` |
| Phase-5 env tunables load with fail-fast casts | PASS | `test_settings_phase5_defaults` + `test_settings_phase5_env_override_and_failfast`; verify printed `config-ok` |

## Tests

- New Phase-5 modules: **27 passed** (`test_migration_0003` + `test_eligibility` + `test_backoff`).
- Firewall: `test_adapter_protocol.py` clean over `state/` (no *arr/slskd vocab in repo.py / migration_0003.sql).
- Full suite: **247 passed, 2 failed** — both failures pre-existing and out of scope (see Deferred Issues).

## Deviations from Plan

This plan's implementation (and, as it turned out, a large slice of later Phase-5 waves) was discovered **already written but uncommitted** in the working tree from a prior interrupted execution. This run treated the 05-01-scoped slice as the deliverable: verified every acceptance criterion and `must_haves` truth against it, then committed the four 05-01 tasks atomically per the plan's task boundaries (the parallel 05-02 Wave-0 work was committed by its own agent — commits `1b489ab`..`b260094`). The 05-01 code matches the plan's `<action>` specs exactly (explicit-column INSERT, two-branch predicate, capped backoff, 7 tunables) — no functional changes were needed. The leftover untracked future-wave files (scheduler/reconcile/shares + their tests + a stray `main.py.orig`) were left in place for plans 05-03/04/05 to own; see Deferred Issues for the `test_scheduler.py` leak they cause in the full suite.

Housekeeping (not behavioral deviations):
- Removed the stray `.p5_test_result.txt` artifact left by the prior run.
- Restored `.planning/phases/phase-5/05-VALIDATION.md`, which showed as modified with a **zero-content** diff (mtime touch only).
- Left the untracked future-wave files (`app/core/scheduler.py`, `reconcile.py`, `shares.py`, `app/tests/test_{scheduler,concurrency,reconcile,status_page}.py`, `app/main.py.orig`) untouched — out of 05-01 scope.

No Rule 1/2/3 auto-fixes were required — the implementation was already correct and firewall-clean.

## Deferred Issues (out of scope — SCOPE BOUNDARY)

Two **pre-existing** Phase-4 slskd-fixture test failures surfaced in the full suite, both RED at clean HEAD before any 05-01 change and both untouched by this plan:

- `tests/test_state_repo.py::test_search_responses_fixture_builds_candidates` — `app/tests/fixtures/slskd/search_responses.json` is committed as an empty `[]` in HEAD, so `audio_file_count == 12` / `username == 'good_seeder'` cannot pass.
- `tests/test_state_repo.py::test_transfer_fixtures_parse` — the slskd transfer_* fixtures in HEAD don't carry the expected state/bytes signals.

`git diff HEAD~4 HEAD` confirms neither test function nor either fixture is part of 05-01's changes. Logged to `.planning/phases/phase-5/deferred-items.md` for a fixture-restore follow-up (candidate: the 05-05 live-probe checkpoint). STATE.md's "suite 205 passed" reflects the Phase-4 CI run on Python 3.12; these surface on the local Python 3.13 box.

## Threat Surface

No new security-relevant surface beyond the plan's threat_model. T-05-01 (row preservation) is proven by the count-unchanged migration test; T-05-02 (`?`-bound eligibility/attempt SQL) holds — no f-string into SQL in any new DAO. No new packages (T-05-SC — Phase 5 installs zero).

## Task Commits

1. **Task 1 — Wave-0 test scaffolds + conftest fixtures** — `fd4c53b` (test)
2. **Task 2 — migration_0003 + db.py registration** — `b20874b` (feat)
3. **Task 3 — repo eligibility/backoff/attempt/counts DAOs + repo tests** — `4a7b334` (feat)
4. **Task 4 — Phase-5 config tunables** — `2ac7d32` (feat)

**Plan metadata:** `cf70831` (docs: complete plan — SUMMARY + STATE + ROADMAP + deferred-items)

## Self-Check: PASSED

- Created files exist: `app/state/migration_0003.sql`, `app/tests/test_migration_0003.py`, `app/tests/test_eligibility.py`, `app/tests/test_backoff.py`, `.planning/phases/phase-5/05-01-SUMMARY.md` — all FOUND.
- Task commits exist: `fd4c53b` (test scaffolds), `b20874b` (migration_0003 + db.py), `4a7b334` (repo DAOs), `2ac7d32` (config) — all present in `git log`.
