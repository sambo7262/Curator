# Phase 5 — Deferred Items

Out-of-scope discoveries logged during execution (not fixed — unrelated to the active plan's task changes).

## From 05-01 execution (2026-05-31)

### 1. Cross-module `db.MIGRATIONS` leak from the UNCOMMITTED future-wave `app/tests/test_scheduler.py`

- **Symptom:** In the full suite, ~16 tests fail with `sqlite3.OperationalError: no such table: main.items_old`
  (cascading into `test_acquire.py` and the Phase-4 `test_state_repo` migration/staged-file tests).
- **Root cause:** A fixture in the untracked `app/tests/test_scheduler.py` mutates the module-global
  `state.db.MIGRATIONS` and does not restore it on teardown, leaving a half-applied schema for every test that
  runs afterward. Proven by bisection: `pytest tests/test_scheduler.py tests/test_acquire.py` → 1 failed;
  `pytest --ignore=tests/test_scheduler.py` → cascade gone (296 passed, only the 2 pre-existing fixture
  failures below remain).
- **Why deferred:** `test_scheduler.py` + `core/scheduler.py` are **untracked** leftovers from a prior
  interrupted full-Phase-5 run; they belong to **plan 05-04** (Wave 2), not 05-01. The full untracked set:
  `app/core/scheduler.py`, `app/core/reconcile.py`, `app/core/shares.py`,
  `app/tests/test_scheduler.py`, `app/tests/test_concurrency.py`, `app/tests/test_reconcile.py`,
  `app/tests/test_status_page.py`, and a stray `app/main.py.orig`. Out of 05-01 scope per the executor
  SCOPE BOUNDARY rule.
- **Action owner:** plan 05-04 — restore `state.db.MIGRATIONS` in the scheduler-test fixture teardown
  (use a `try/finally` or `monkeypatch.setattr`, mirroring the existing `monkeypatch_migrations` helper in
  `test_state_repo.py`). Also fold in / remove the `app/main.py.orig` stray when 05-05 wires `main.py`.

### 2. Two pre-existing Phase-4 slskd-fixture test failures

- `tests/test_state_repo.py::test_search_responses_fixture_builds_candidates`
- `tests/test_state_repo.py::test_transfer_fixtures_parse`
  - **Status:** RED at clean HEAD before any 05-01 change; both committed in HEAD. `git diff HEAD~4 HEAD`
    shows 0 changes to either test function or its fixtures, so they are not 05-01's doing.
  - **Note:** These surface on the local **Python 3.13** box but not the Phase-4 CI run on Python 3.12
    (STATE.md "suite 205 passed"). A fixture/runtime-reconciliation follow-up (candidate: the 05-05
    live-probe checkpoint) should reconcile them.
  - **Action owner:** out of 05-01 scope; flag for the verifier / a fixture-reconciliation follow-up.
