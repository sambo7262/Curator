---
phase: 5
plan: 03
subsystem: core orchestration — shares ensure/self-heal + startup reconciliation (Wave 1)
tags: [SHARE-01, SHARE-02, REL-02, D-10, D-14, firewall, self-heal, reconcile, no-burn]
dependency_graph:
  requires:
    - "05-01: repo.list_by_status / set_status / record_attempt + migration_0003 (searching/permanently-unavailable states, attempt_count column)"
    - "05-02: slskd.get_shared_file_count() / rescan_shares() (neutral int/bool seam) + core.acquire.INFRA_EXC (the single classifier) + tests.fakes.FakeSlskd"
    - "Phase 4: adapter.verify_imported(item) re-query (D-03) + gap_detector.build_adapters (caller-owns-close)"
  provides:
    - "core.shares.ensure_shares(slskd, app_state) -> bool — the D-10 ensure/self-heal cycle the scheduler (05-04) calls each cycle"
    - "core.reconcile.reconcile_on_startup(conn, lock, build_adapters, settings) -> None — the D-14 orphan reset main.py (05-05) calls at boot"
    - "core.reconcile.ORPHAN_STATES + _gapitem_from_row (neutral row->GapItem mapper)"
  affects:
    - "05-04 scheduler (calls ensure_shares each cycle; reuses the same INFRA_EXC import seam)"
    - "05-05 main.py lifecycle (calls reconcile_on_startup at startup; surfaces app_state.shares_ok on /status)"
tech_stack:
  added: []   # zero new packages — stdlib + already-pinned httpx (T-05-SC clean)
  patterns:
    - "Eventually-consistent-across-cycles self-heal: trigger one async rescan, never re-read in the same cycle (Pitfall 6)"
    - "Verify-by-requery double-import guard before any orphan reset (Pitfall 3)"
    - "No-burn reset: set_status ONLY (never record_attempt) so an infra interruption never increments attempt_count (D-14)"
    - "Reuse-not-redefine the single INFRA_EXC classifier (imported from core.acquire)"
key_files:
  created:
    - app/core/shares.py
    - app/core/reconcile.py
    - app/tests/test_reconcile.py
  modified:
    - app/tests/test_shares.py   # +4 ensure_shares cycle cases (the client-level cases were 05-02's)
decisions:
  - "reconcile loops searching/downloading/importing — searching INCLUDED because select_eligible never re-picks it (a mid-search kill would strand the orphan forever, T-05-24)"
  - "A clean reset to pending burns NO attempt (set_status only, never record_attempt) — the interruption was infra, not a genuine fail (D-14)"
  - "ensure_shares does NOT re-read the count after a rescan in the same cycle (rescan is async — Pitfall 6); the issue is surfaced/cleared across cycles"
  - "INFRA_EXC is imported from core.acquire (the 05-02 single definition), never redefined — the infra-vs-genuine boundary lives in one place"
  - "REL-02 left In-progress, not complete: only its STARTUP half (reconcile) lands here; the scheduler infra-skip half is 05-04 and the live wiring is 05-05"
metrics:
  duration: ~15m
  completed: 2026-05-31
  tasks: 2
  new_tests: 10   # test_reconcile (6) + test_shares ensure_shares cases (4)
---

# Phase 5 Plan 03: Wave-1 Core Services (Shares Self-Heal + Startup Reconcile) Summary

Composed two neutral Wave-1 core services over the Wave-0 adapter/repo surface: `core/shares.py` (the D-10 SHARE-01/02 ensure/self-heal — read the neutral shared-file count, trigger one async rescan on zero, surface across cycles, never block acquisition) and `core/reconcile.py` (the D-14/REL-02 startup half — reset orphaned searching/downloading/importing rows with a verify-by-requery double-import guard, burning no attempt on a clean reset or an infra fault). Both are firewall-clean orchestration over the Wave-0 neutral int/bool seam, `verify_imported`, and the repo DAOs; the shared `INFRA_EXC` is imported from `core.acquire`, never redefined.

## What Was Built

**Task 1 — core/shares.py (commit `2f3a9c1`).**
- `ensure_shares(slskd, app_state) -> bool`: reads the NEUTRAL `get_shared_file_count()` int (the `shares.files` wire key stays in `adapters/slskd.py`). `count > 0` → `app_state.shares_ok = True`, return True. `count == 0` → call `rescan_shares()` EXACTLY ONCE, `app_state.shares_ok = False`, return False — and DELIBERATELY does NOT re-read the count in the same cycle (the rescan is async; a same-cycle re-read would observe a stale 0 — Pitfall 6). The share issue is therefore surfaced/cleared ACROSS cycles: this cycle triggers + surfaces, a LATER cycle observes the recovered count and clears `shares_ok`. Never blocks acquisition and never raises on a clean zero count (D-10 — a zero-share state is a leech risk to surface, not a hard stop); NEVER rewrites slskd.yml.
- `test_shares.py` extended with 4 `ensure_shares` cycle cases (importing `FakeSlskd` from `tests.fakes` directly — NOT a conftest fixture, and this plan does not touch conftest.py): positive-count→ok/no-rescan; zero→single-rescan/surfaced/no-same-cycle-re-read (asserts `count_calls == 1`); 409 (`rescan_result=False`, "already healing")→still surfaced; and a 3-cycle `count_sequence=[0,0,1234]` proving the cross-cycle eventually-consistent recovery (one count read per cycle, rescan only while 0, cleared only when the count recovers).

**Task 2 — core/reconcile.py (commit `84d4f1d`).**
- `reconcile_on_startup(conn, lock, build_adapters, settings) -> None`: loops `ORPHAN_STATES = ("searching", "downloading", "importing")`. `searching` is included deliberately — `select_eligible` only re-picks pending/stuck/quarantined/permanently-unavailable, so a `searching` orphan (killed mid-search) that is not reset is stranded FOREVER (T-05-24 / D-14 "no orphaned in-flight items"). For each orphan it builds a neutral `GapItem` via `_gapitem_from_row` (reads only neutral ledger columns) and calls `adapter.verify_imported(item)`:
  - True → `set_status('imported')`, do NOT re-import (no double-import, Pitfall 3 — `execute_import` is never called by reconcile).
  - False → `set_status('pending')` — STATUS ONLY, so `attempt_count` is UNTOUCHED (the interruption was infra, not a genuine fail — NO `record_attempt`, no burn, D-14).
  - raises `INFRA_EXC` → `continue`: leave the row exactly as-is, retry next boot (no status change, no burn — REL-02).
- Imports the SINGLE `INFRA_EXC` from `core.acquire` (the 05-02 deliverable — reused, never redefined). Every ledger write is serialized through the shared writer `lock` (single-writer, D-16); adapters/clients are built via the injected `build_adapters` and EVERY client is closed in `finally` (CR-02). A per-row failure is logged and skipped — one bad orphan can never block the boot reconcile.
- `test_reconcile.py` (offline; builds a real temp SQLite ledger via `state.db.connect` + `run_migrations`, seeds orphans through the repo DAOs, drives a scriptable `FakeAdapter` behind an injected `build_adapters`): (a) `importing`+verify True → `imported`, `execute_import_calls == 0`; (b) `downloading`+verify False → `pending`, attempt_count unchanged; (c) `searching`+verify False → `pending`, attempt_count unchanged (the D-14/T-05-24 not-stranded headline); (d) verify raising `INFRA_EXC[0]` (a real httpx infra type) → status unchanged, no burn; plus a mixed three-state sweep that also asserts the client was closed in `finally` (CR-02).

## must_haves Verification

| Truth | Status | Evidence |
|-------|--------|----------|
| ensure_shares >0 → shares_ok True; ==0 → rescan + shares_ok False | PASS | `test_ensure_shares_positive_count_is_ok` + `test_ensure_shares_zero_count_triggers_single_rescan_and_surfaces` |
| shares self-heal eventually-consistent across cycles (no same-cycle re-read after async rescan) | PASS | `test_ensure_shares_recovers_across_cycles_not_within_one` (count read once/cycle; cleared only when count recovers) + the zero-branch `count_calls == 1` assertion |
| reconcile resets searching/downloading/importing orphans; import-completed → imported (no re-import) | PASS | `test_importing_orphan_that_imported_becomes_imported_no_double_import` (`execute_import_calls == 0`) + the mixed-sweep test |
| still-wanted → pending WITHOUT burning an attempt | PASS | `test_downloading_orphan_..._without_burning_attempt` (attempt_count 2→2) |
| searching orphan → pending, no burn, never stranded (D-14/T-05-24/REL-02) | PASS | `test_searching_orphan_resets_to_pending_not_stranded` (attempt_count 1→1) |
| infra fault during reconcile → leave row as-is, no burn | PASS | `test_infra_fault_during_verify_leaves_row_as_is_no_burn` (status stays `downloading`, attempt_count 1→1) |
| core/shares.py + core/reconcile.py contain zero *arr/slskd wire vocabulary | PASS | `tests/test_adapter_protocol.py` (the `ARR_FIELD_NAMES` grep auto-scans all of `core/` via rglob) green; manual grep for `shares.files`/`albumId`/`records[`/etc. over both modules found nothing |

## Tests

- Targeted (Task 1): `tests/test_shares.py tests/test_adapter_protocol.py` → **20 passed**, exit 0.
- Targeted (Task 2): `tests/test_reconcile.py tests/test_adapter_protocol.py` → **6 passed**, exit 0.
- Full suite: `python3 -m pytest -q` → **268 passed, 0 failed**, exit 0 (was 262 before this plan; +6 new `test_reconcile.py` cases and +4 `ensure_shares` cases added to the existing `test_shares.py`). The two pre-existing Phase-4 slskd-fixture failures noted in 05-01's deferred-items did NOT surface in this run (clean 268-green).

## Deviations from Plan

**None functional.** The plan executed as written across both tasks — no Rule 1-4 deviations were required; zero new packages (T-05-SC clean).

Two small, plan-faithful test-construction choices (not behavioral deviations):
- `test_reconcile.py` builds its own temp ledger via `state.db.connect` + `run_migrations` and seeds rows through the repo DAOs, rather than depending on conftest's `seed_v0002_ledger`. This is offline, deterministic, and exercises the real migration_0003 schema (including the `searching` state and the `attempt_count` column the no-burn assertions read) — equivalent to the conftest seed for this plan's needs and avoids coupling the reconcile proof to a v0002-shaped fixture.
- The infra-fault case (d) raises `INFRA_EXC[0]` (the first real httpx exception type in the shared tuple) and `pytest.skip`s if httpx is absent (the offline 3.9 sandbox, where `INFRA_EXC == ()`), so the test is honest on both the dev sandbox and CI/NAS 3.12.

## REL-02 Status Note (scope honesty)

`SHARE-01` and `SHARE-02` are marked **complete** (the ensure/self-heal cycle is fully delivered and proven). `REL-02` is left **In-progress**: this plan delivers only its STARTUP half (the reconcile orphan reset + double-import guard + no-burn classification on boot). REL-02's other halves — the scheduler's per-cycle infra-skip (no burned attempt on a transient mid-cycle outage) and the live lifecycle wiring (`main.py` calling `reconcile_on_startup` at boot) — land in 05-04 and 05-05 respectively. Marking REL-02 complete now would overstate coverage.

## Threat Surface

No new security-relevant surface beyond the plan's `<threat_model>`. T-05-09 (rescan retry-storm) is mitigated — `ensure_shares` triggers exactly one rescan per zero-count cycle and never re-reads/loops in-cycle (asserted). T-05-10 (double-import on restart) is mitigated — the verify-by-requery guard runs before any reset and `execute_import` is asserted never-called. T-05-11 (wrong-attempt-burn on infra) is mitigated — `INFRA_EXC → continue` and a clean reset uses `set_status` only (no `record_attempt`). T-05-12 (concurrent writers) — all reconcile writes go through the shared writer lock. T-05-24 (`searching` orphan stranded) is mitigated and proven. T-05-SC — zero new packages. No threat flags.

## Known Stubs

None. Both modules are fully wired: `ensure_shares` calls the real neutral slskd seam; `reconcile_on_startup` composes the real `verify_imported` + repo DAOs + the shared `INFRA_EXC`. No placeholder data, no empty-return stubs.

## Task Commits

1. **Task 1 — core/shares.py ensure/self-heal cycle + test_shares cases** — `2f3a9c1` (feat)
2. **Task 2 — core/reconcile.py startup orphan reset + test_reconcile.py** — `84d4f1d` (feat)

Plan metadata (SUMMARY + STATE + ROADMAP + REQUIREMENTS) committed separately as the final docs commit.

## Self-Check: PASSED

- Created files exist: `app/core/shares.py`, `app/core/reconcile.py`, `app/tests/test_reconcile.py`, `.planning/phases/phase-5/05-03-SUMMARY.md` — all FOUND.
- Task commits exist: `2f3a9c1` (feat shares) and `84d4f1d` (feat reconcile) — both present in `git log`.
- Test result is REAL: `python3 -m pytest -q` exited 0 with **268 passed** (captured to /tmp/full.txt; the `268 passed` line was extracted via grep). This is the actual measured result, not an assumed one.
