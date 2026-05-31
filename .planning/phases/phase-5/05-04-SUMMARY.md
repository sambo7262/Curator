---
phase: 5
plan: 04
subsystem: scheduler-daemon-and-batched-detection
tags: [scheduler, daemon, concurrency, threadpool, backoff, detection, rel-01, gap-03, state-03]
dependency_graph:
  requires:
    - "05-01 repo.select_eligible / record_attempt / backoff_for + v0003 ledger + config tunables"
    - "05-02 acquire.INFRA_EXC + adapter.get_queue_status (D-02 race check)"
    - "05-03 core.shares.ensure_shares (self-heal, never blocks acquisition)"
    - "Phase-4 core.acquire.acquire_item + core.gap_detector.detect_gaps/build_adapters"
  provides:
    - "core.scheduler.Scheduler (daemon thread) + run_cycle + run_one + apply_result + LockedConn + dispatch"
    - "core.gap_detector.detect_gaps wrapped in one BEGIN/COMMIT (D-15 single-fsync detection pass)"
  affects:
    - "the autonomous acquisition loop — main.py (05-05) wires Scheduler.start after reconcile_on_startup"
tech-stack:
  added: []
  patterns:
    - "stdlib daemon thread + interruptible threading.Event stop-gate (no busy-wait; clean join on shutdown)"
    - "Single sqlite connection + shared writer lock via LockedConn proxy under ThreadPoolExecutor (D-16)"
    - "Bounded dispatch: ThreadPoolExecutor(max_workers=max_concurrent) + per-cycle LIMIT (room) flood control"
    - "Guarded cycle: ANY cycle exception logged and swallowed so the daemon never dies (REL-01 / Pitfall 5)"
    - "Per-cycle Settings.from_env re-read so ACQ_ENABLED kill-switch + tunables apply live without restart"
key-files:
  created:
    - app/core/scheduler.py
    - app/tests/test_scheduler.py
    - app/tests/test_concurrency.py
    - app/tests/test_detect_batch.py
  modified:
    - app/core/gap_detector.py
decisions:
  - "D-15 detection is one BEGIN/COMMIT (single fsync) — dedup + status-never-clobbered + discovered_at + synchronous=FULL all preserved"
  - "D-16 ONE connection; every write serialized through the shared writer lock; workers never open a second connection"
  - "D-04 MAX_CONCURRENT bounds simultaneous acquisitions; per-cycle room = max_concurrent * 10 caps the ~1493 backlog per pass (Pitfall 1)"
  - "infra-skip / skip-usenet-active / dry-run write NOTHING (item stays eligible, no attempt burned)"
metrics:
  duration: "~17m executor + inline finish (executor stalled at the post-impl suite step on a full temp FS; bookkeeping completed inline)"
  completed: 2026-05-31
---

# Phase 5 Plan 04: Scheduler Daemon + Batched Detection Summary

Composed the Wave-0/1 surface into the autonomous loop at the heart of Phase 5: a stdlib daemon thread runs one self-contained cycle per poll interval — **batched detect → ensure_shares → eligibility select → per-item queue check → bounded dispatch → apply_result** — over a single sqlite connection whose every write is serialized through a shared writer lock, under a `ThreadPoolExecutor` bounded at `MAX_CONCURRENT`. Detection now commits as one transaction (one fsync), and a cycle fault is logged and swallowed so the daemon never dies.

## What Was Built

- **gap_detector.py** — `detect_gaps` wrapped in a single explicit `BEGIN`/`COMMIT` (D-15) so the whole detection pass is one fsync. The connection is autocommit (isolation_level=None), so the explicit BEGIN/COMMIT are the transaction boundary; dedup (UNIQUE arr_app/arr_id), status-never-clobbered, `discovered_at` preservation, and `synchronous=FULL` durability are all unchanged.
- **scheduler.py** —
  - `LockedConn` — thin writer-lock proxy over the one connection; wrapping `conn.execute` under the shared lock serializes acquire_item's own writes for free (no second connection — sqlite3 forbids concurrent use, D-16).
  - `run_one(item, adapter, slskd, conn, settings)` — resolves ONE item to a neutral outcome string: D-02 Usenet-race queue check FIRST (`skip-usenet-active`, no burn); `INFRA_EXC` on the queue check OR mid-acquire → `infra-skip` (no burn, REL-02); `ACQ_DRY_RUN` → `dry-run` log-only (zero side effects, D-05); otherwise the `acquire_item` verdict.
  - `apply_result(...)` — the STATE-03 write side: `imported` resets attempt_count to 0; `quarantined`/`stuck` bump attempt_count + set the backoff anchor, and at `>= acq_max_attempts` transition to `permanently-unavailable` with a dormant recheck; `infra-skip`/`skip-usenet-active`/`dry-run` write nothing. All writes under the lock.
  - `dispatch(...)` — `ThreadPoolExecutor(max_workers=max_concurrent)` over the eligible items, order-preserving outcomes; each worker handed a `LockedConn`.
  - `run_cycle(...)` — the 5-step cycle on the app's single retained connection + shared `_detect_lock`; adapters/slskd clients built lazily and closed in `finally` (CR-02).
  - `Scheduler` — daemon thread: boot cycle once, then loop on the interval via interruptible `stop_event.wait(interval)`; `stop()` sets the event + joins (clean shutdown). `ACQ_ENABLED` + tunables re-read each cycle via `Settings.from_env()` (A4 live kill-switch). `_tick` guards every cycle — any exception is logged and swallowed (Pitfall 5 / REL-01, the daemon never dies).
- **Firewall** — scheduler.py is CORE: speaks only neutral shapes (repo DAOs, neutral queue bool, neutral acquire outcome strings, ensure_shares bool). Zero *arr/slskd wire vocabulary; neutral log identity `app:id`.

## must_haves Verification

| Truth | Status | Evidence |
|-------|--------|----------|
| Detection commits in ONE transaction (one fsync), dedup + status-never-clobbered + discovered_at + synchronous=FULL preserved | PASS | `gap_detector.py:42/49` single BEGIN/COMMIT; `test_detect_batch.py` (one-txn batch tests) |
| Daemon thread loops on poll interval via interruptible stop-event and stops cleanly | PASS | `Scheduler._run` + `stop_event.wait(interval)` + `stop()` join; `test_scheduler.py` lifecycle test |
| A cycle exception is logged and the loop continues (daemon never dies) | PASS | `_tick` try/except `Exception` → `log.exception`, no re-raise (scheduler.py:318) |
| ACQ_ENABLED=false skips a cycle; ACQ_DRY_RUN zero side effects; MAX_CONCURRENT bounds acquisitions | PASS | `_tick` kill-switch (scheduler.py:314); `run_one` dry-run short-circuit (:101); `dispatch` ThreadPoolExecutor(max_workers=max_concurrent) (:173); `test_scheduler.py` + `test_concurrency.py` |
| Each cycle = batched detect → ensure_shares → eligibility select → per-item queue check → bounded dispatch → apply_result; infra-skip burns no attempt | PASS | `run_cycle` steps 1–5; `apply_result` no-write set for infra-skip/usenet/dry-run (:128) |
| All ledger writes serialized on the single connection via the shared writer lock under bounded concurrency | PASS | `LockedConn` + `with lock:` around every write; `test_concurrency.py` |

## Tests

- **Full suite: 272 passed, 0 failed** (`python3 -m pytest -q`, exit 0) — the green baseline (251 after the 05-01 migration FK fix) plus 05-04's new detection/scheduler/concurrency cases, with zero regressions.
- New modules: `test_detect_batch.py` (D-15 one-txn detection), `test_scheduler.py` (daemon lifecycle + kill-switch + dry-run + cycle-never-dies), `test_concurrency.py` (single-writer serialization under the bounded pool).

## Deviations from Plan

The executor agent completed all four code commits (TDD red→green: `1f5f4f3` detect-batch tests → `5c479f8` one-txn detect_gaps → `60fbe0a` scheduler tests → `beebc1a` scheduler.py → `512fdb2` test arg fix) and the suite passed, but **stalled at the post-implementation "run the full suite + write SUMMARY" step** because the harness temp filesystem hit ENOSPC (the session-wide instability root cause). The agent was stopped cleanly; this SUMMARY + the STATE/ROADMAP bookkeeping were completed **inline** (no subagent) after independently verifying on disk: all must-have symbols present in committed `scheduler.py`/`gap_detector.py`, working tree clean, and the full suite green at 272/0. No code changes were needed during the inline finish — the committed implementation matches the plan's `<action>` specs.

## Threat Surface

No new external surface. The scheduler is the orchestration loop; it adds no new package (T-05-SC) and no new wire vocabulary (firewall verified). Bounded concurrency + the single-writer lock prevent the multi-writer corruption class; the per-cycle room cap prevents firehosing the ~1493-gap backlog (Pitfall 1).

## Task Commits

1. **Task 1 — detect_gaps batch-transaction tests (RED)** — `1f5f4f3` (test)
2. **Task 1 — wrap detect_gaps in one BEGIN/COMMIT (GREEN, D-15)** — `5c479f8` (feat)
3. **Task 2 — scheduler + concurrency tests (RED)** — `60fbe0a` (test)
4. **Task 2 — scheduler.py daemon loop + LockedConn + run_one + apply_result (GREEN)** — `beebc1a` (feat)
5. **Task 2 — test helper fix (gap_type arg)** — `512fdb2` (fix)

**Plan metadata:** this commit (`docs(05-04)` — SUMMARY + STATE + ROADMAP).

## Self-Check: PASSED

- Created files exist on disk: `app/core/scheduler.py`, `app/tests/test_scheduler.py`, `app/tests/test_concurrency.py`, `app/tests/test_detect_batch.py` — all FOUND.
- All must-have symbols verified present in committed code (grep over `app/core/scheduler.py` + `app/core/gap_detector.py`).
- Task commits exist in `git log` (`1f5f4f3`, `5c479f8`, `60fbe0a`, `beebc1a`, `512fdb2`).
- Full suite: **272 passed, 0 failed** (exit 0), no regressions vs the 251 baseline.
