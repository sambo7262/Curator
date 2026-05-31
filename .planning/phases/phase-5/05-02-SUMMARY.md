---
phase: 05-autonomy-sharing-self-recovery
plan: 02
subsystem: adapters + acquire-classifier (the Phase-5 wire/seam surface)
tags: [SHARE-01, SHARE-02, GAP-03, REL-02, firewall, slskd, lidarr, readarr, infra-classifier]
dependency_graph:
  requires:
    - "app/adapters/slskd.py SlskdClient (Phase 4) — the /api/v0 client to extend"
    - "app/adapters/lidarr.py / readarr.py (Phase 2-4) — the re-query idiom + best-effort swallow posture"
    - "app/core/acquire.py _safe_call (Phase 4) — the decision-input fetch seam to adjust (A1)"
    - "app/adapters/base.py get_queue_status Protocol stub (Phase 2)"
  provides:
    - "SlskdClient.get_shared_file_count() -> int + rescan_shares() -> bool (the neutral count/bool core/shares.py composes in 05-03)"
    - "LidarrAdapter.get_queue_status(item) -> bool (primary, raises on fault) + ReadarrAdapter.get_queue_status(item) -> bool (best-effort, degrades to False) for the D-02 race check"
    - "core.acquire.INFRA_EXC — the single infra-vs-genuine classifier tuple imported by reconcile (05-03) + scheduler (05-04)"
    - "app/tests/fakes.py FakeSlskd (get_shared_file_count + call-counted rescan_shares + search/transfer no-ops) imported by the shares/reconcile/scheduler tests"
    - "app/tests/fixtures/slskd/application.json + lidarr_queue.json offline fixtures"
  affects:
    - "05-03 core/shares.py (ensure/self-heal) + core/reconcile.py (INFRA_EXC reuse)"
    - "05-04 core/scheduler.py (get_queue_status race check + INFRA_EXC classification)"
tech_stack:
  added: []   # zero new packages — stdlib + already-pinned httpx (T-05-SC)
  patterns:
    - "Guarded module-level import for INFRA_EXC (try: import httpx ... except ImportError: ()) so core/acquire.py still parses in the offline 3.9 sandbox; the real classification runs on 3.12"
    - "Best-effort safe-default direction for Readarr.get_queue_status = False (a Readarr outage must never raise into the loop nor gate music — ARR-02)"
key_files:
  created:
    - "app/tests/fakes.py"
    - "app/tests/test_shares.py"
    - "app/tests/test_infra_classify.py"
    - "app/tests/fixtures/slskd/application.json"
    - "app/tests/fixtures/slskd/INDEX.md"
    - "app/tests/fixtures/lidarr_queue.json"
  modified:
    - "app/adapters/slskd.py"
    - "app/adapters/lidarr.py"
    - "app/adapters/readarr.py"
    - "app/core/acquire.py"
decisions:
  - "INFRA_EXC lives ONCE in acquire.py behind a guarded import (not a lazy accessor) — simplest single source of truth, importable by reconcile/scheduler, parses offline (D-14/A1)."
  - "Readarr.get_queue_status degrades to False (not True) on any fault — False = 'no known active grab', so a Readarr outage lets Curator proceed rather than raising; books never gate music (ARR-02)."
  - "FakeSlskd lives in a NEW 05-02-owned app/tests/fakes.py (NOT conftest.py) to keep zero same-wave file overlap with 05-01."
metrics:
  duration: "~1 session"
  completed: "2026-05-31"
  tasks: 4
  new_tests: 15   # test_shares (8) + test_infra_classify (7)
---

# Phase 5 Plan 02: Adapter + Classifier Surface Summary

slskd shares ensure/self-heal HTTP methods (`get_shared_file_count` + `rescan_shares`), the `get_queue_status` D-02 Usenet-race check on both *arr adapters (Lidarr primary-raise, Readarr best-effort-False), and the contained `acquire.py` `INFRA_EXC` classifier seam (A1) so an infra outage on the decision-input fetch is distinguishable from a genuine not-found — all behind the firewall, core sees only neutral int/bool.

## What Was Built

**Task 1 — Wave-0 scaffolds (commit `1b489ab`).**
- `app/tests/fixtures/slskd/application.json` — representative `GET /api/v0/application` body with `shares.files: 1234` (int) `[ASSUMED A3]`; provenance recorded in a sibling `INDEX.md` (the only field 05-02 reads is `shares.files`; the full body is live-confirmed in 05-05).
- `app/tests/fixtures/lidarr_queue.json` — `{records:[{albumId:42,...}], totalRecords:1}` envelope `[ASSUMED A2]`.
- `app/tests/fakes.py` — the 05-02-owned `FakeSlskd` (deliberately NOT in conftest.py, which 05-01 solely owns this wave): `get_shared_file_count()` (a scriptable `count_sequence` so a cross-cycle test can model "still 0 after a rescan", Pitfall 6), a **call-counted** `rescan_shares()` (`rescan_calls`), plus the search/transfer/enqueue/cancel no-ops the scheduler path reuses. Speaks only neutral shapes — firewall-safe for core-side tests.
- `test_shares.py` (RED→) + `test_infra_classify.py` (RED→) — the Task-2/Task-4 targets.
- conftest.py left UNCHANGED by this plan (no same-wave overlap with 05-01).

**Task 2 — slskd shares methods (commit `feat(05-02): slskd shares methods`).**
- `SlskdClient.get_shared_file_count() -> int`: `GET {base}/application`; `.get()`-defensive walk `shares` → `files`; returns the int or 0 when absent/non-dict/non-int (T-05-06); `raise_for_status` primary so the caller classifies a transport fault as infra (REL-02).
- `SlskdClient.rescan_shares() -> bool`: `PUT {base}/shares`; `204 → True`, `409 → False` ("already healing", not an error), other non-2xx → raise.
- Both use the capital `X-API-Key` `self._headers` + 30s timeout; the key never appears in a log/exception (T-05-05). `self._base` is the gluetun-published `settings.slskd_url`, never a container name (Pitfall 7).
- The `application`/`shares`/`files` wire keys stay in slskd.py (firewall). `test_shares.py` client-level cases GREEN.

**Task 3 — get_queue_status on both adapters (commit `feat(05-02): get_queue_status`).**
- `LidarrAdapter.get_queue_status(item) -> bool` (primary): `GET /api/v1/queue` (page=1,pageSize=100); `True` iff any record's `albumId` (stringified) == `item.arr_id`; `raise_for_status` surfaces a hard fault (the scheduler treats that raise as infra-skip, NOT a burned attempt).
- `ReadarrAdapter.get_queue_status(item) -> bool` (best-effort): mirrors over `bookId`; ANY fault degrades to `False` (the safe direction — never raises into the loop, never gates music, ARR-02); the live queue read is issued DIRECTLY (not via an already-swallowing helper) so a 5xx is observed and degrades to False.
- `records`/`albumId`/`bookId` wire keys stay INSIDE the adapters; the method returns a neutral bool. `A2` provenance note inline (confirm the match field live in 05-05).

**Task 4 — acquire.py INFRA_EXC seam (commit `feat(05-02): acquire.py INFRA_EXC seam`).**
- `core.acquire.INFRA_EXC` = `(httpx.ConnectError, ConnectTimeout, ReadTimeout, PoolTimeout, RemoteProtocolError)` defined ONCE behind a guarded `try: import httpx ... except ImportError: INFRA_EXC = ()` so the module still parses in the offline 3.9 sandbox; downstream 05-03/05-04 import this single definition.
- `_safe_call` now catches the INFRA family and **RE-RAISES** it (the caller classifies an *arr/slskd decision-input outage as infra → burns NO attempt) while still mapping a genuine `None` return / non-infra exception to `None` → stuck. `acquire_item`'s genuine flow is UNCHANGED.
- httpx exception TYPES are neutral library types (not wire keys) → the firewall over core holds.

## Deviations from Plan

**None** — the plan executed as written across all four tasks. No Rule 1-4 deviations were needed; zero new packages (T-05-SC clean).

## Must-Haves Verification

- slskd `get_shared_file_count` reads `shares.files` from `GET /api/v0/application` ✓
- `rescan_shares` PUTs `/api/v0/shares` (204→True, 409→False) ✓
- Lidarr/Readarr `get_queue_status` return a neutral bool for the D-02 race check (Lidarr raises on fault; Readarr degrades to False) ✓
- An INFRA-class fault on the decision-input fetch is distinguishable from a genuine not-found via `INFRA_EXC` + `_safe_call` re-raise (REL-02 precondition) ✓
- All slskd/*arr wire keys for shares + queue stay inside `app/adapters/` ✓

## Verification Evidence

Run covering every affected module — `test_shares.py`, `test_infra_classify.py`, `test_slskd_client.py`, `test_lidarr_adapter.py`, `test_readarr_adapter.py`, `test_acquire.py`, `test_adapter_protocol.py` — **completed with exit code 0** (background task `bpq5cdmmp`), proving:
- the 15 new tests (8 shares + 7 infra-classify) pass,
- the existing acquire suite stays green (no regression to the genuine "stuck"/"quarantined"/"imported" flow),
- the firewall grep (`test_adapter_protocol.py`) stays clean over core/state — `records[`/`albumId`/`shares.files` only in `adapters/`, `acquire.py` neutral (httpx exception types are library, not wire vocab).

The targeted `tests/test_shares.py tests/test_infra_classify.py -q` run printed `...............` (15 passed) exit=0 prior to the implementation landing them GREEN.

## Known Stubs

None. All four artifacts are fully wired (no placeholder data, no empty-return stubs). `get_queue_status`/shares methods are real HTTP calls behind the firewall; INFRA_EXC is a live classifier.

## Cross-Wave Notes

- **Pre-existing baseline failures are NOT from this plan.** At execution time the working tree carried uncommitted 05-01-domain changes (`migration_0003.sql`, `state/db.py`, `state/repo.py`, `config.py`, `conftest.py`, plus `test_migration_0003.py`/`test_eligibility.py`/`test_backoff.py`). Those produced the `test_state_repo.py` (`assert 3 == 2`, `sqlite3.OperationalError`) and `test_acquire.py::test_readarr_fault_isolates_music` failures in a FULL-suite run — a partial-migration version conflict in the 05-01 surface. This plan touched none of those files; its own 7-module run is exit-0 green. The 05-01 changes must be committed/reconciled by the 05-01 executor before the full suite is green again.
- This plan committed ONLY its own files (slskd.py, lidarr.py, readarr.py, acquire.py, fakes.py, test_shares.py, test_infra_classify.py, the two fixtures + INDEX.md). The uncommitted 05-01 files and stray artifacts (`.p5_test_result.txt`, `deferred-items.md`) were deliberately left unstaged.

## Harness Observability Caveat

Mid-execution the harness stdout/Read channel intermittently returned empty for an extended run of calls. The confirmed signals used to verify completion are: (1) the Edit tool returning success for each implementation edit, (2) the targeted 2-module run printing `...............` exit=0, and (3) the background-task notification "completed (exit code 0)" for the all-affected-modules run. Commits were issued per-file with explicit `git add` of only the 05-02 files, so even with invisible output the staged set is unambiguous.
