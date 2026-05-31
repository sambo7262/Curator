# Phase 5 — Deferred Items

Out-of-scope discoveries logged during execution (not fixed — unrelated to the active plan's task changes).

## From 05-01 execution (2026-05-31)

- **Pre-existing test failures: `tests/test_state_repo.py::test_search_responses_fixture_builds_candidates`
  AND `tests/test_state_repo.py::test_transfer_fixtures_parse`** (both Phase-4 slskd-fixture tests)
  - **Status:** RED at clean HEAD (before any 05-01 change). Both tests are committed in HEAD; their fixtures
    `app/tests/fixtures/slskd/search_responses.json` (empty `[]`, num responses: 0) and the transfer_* fixtures
    do not carry the values the assertions expect (`audio_file_count == 12` / `username == 'good_seeder'` and the
    transfer state/bytes signals), so the tests cannot pass locally.
  - **Why deferred:** Neither test function nor either fixture is touched by plan 05-01 (`git diff HEAD~4 HEAD`
    shows 0 changes to those test functions and no fixture changes). Out of scope per the executor SCOPE BOUNDARY
    rule — only issues directly caused by the current task's changes are auto-fixed.
  - **Note:** STATE.md records "suite 205 passed" from the Phase-4 CI run on Python 3.12; this failure surfaces
    on the local Python 3.13 box. The empty `search_responses.json` fixture appears to have been committed empty
    during a prior Phase-4 plan. A future Phase-4/Phase-5 fixture-reconciliation task (or the 05-05 live-probe
    checkpoint) should restore the recorded slskd search-responses fixture content.
  - **Action owner:** out of 05-01 scope; flag for the verifier / a fixture-restore follow-up.
