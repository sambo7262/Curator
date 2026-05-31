---
phase: 5
slug: autonomy-sharing-self-recovery
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-31
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (offline, network-free — MockTransport/fakes) |
| **Config file** | none — tests live under `app/tests/`, run from `app/` |
| **Quick run command** | `cd app && python3 -m pytest tests/<changed>.py -q` |
| **Full suite command** | `cd app && python3 -m pytest -q` |
| **Estimated runtime** | ~10–20 seconds (currently 205 tests) |

---

## Sampling Rate

- **After every task commit:** Run the quick command for the changed test module.
- **After every plan wave:** Run the full suite.
- **Before `/gsd:verify-work`:** Full suite must be green.
- **Max feedback latency:** ~20 seconds.

---

## Per-Task Verification Map

> The planner fills this from the PLAN.md tasks. Every behavior-adding task needs an `<automated>` pytest verify;
> infra/VPN/live-NAS behaviors go in Manual-Only below. Maintain the firewall (no *arr/slskd wire vocab in core/state tests).
>
> Wave note: `test_scheduler.py` + `test_concurrency.py` are created DIRECTLY in Wave 2 (05-04) with their
> code (no Wave-0 pre-stub) — mirroring how Phase 4's 04-04 scheduler-equivalent had no separate stub task.
> The Wave-0 (05-01/05-02) test modules below ARE pre-scaffolded RED so downstream waves have a green target.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 0 | STATE-03 | — | migration_0003 adds attempt_count/next_attempt_at/last_checked_at + permanently-unavailable status; ~1493 rows survive | unit | `cd app && python3 -m pytest tests/test_migration_0003.py -q` | ❌ W0 | ⬜ pending |
| 5-01-02 | 01 | 0 | GAP-03 | — | eligibility predicate selects only grace-elapsed + backoff-elapsed + non-permanently-unavailable items | unit | `cd app && python3 -m pytest tests/test_eligibility.py -q` | ❌ W0 | ⬜ pending |
| 5-03-02 | 03 | 1 | REL-02 | T-05-10/11/24 | infra outage classified infra → no attempt burned; genuine failure path; no double-import on restart; searching orphan reset to pending (not stranded) | unit | `cd app && python3 -m pytest tests/test_reconcile.py -q` | ❌ W0 (RED in 05-02) | ⬜ pending |
| 5-03-01 | 03 | 1 | SHARE-01/02 | T-05-09 | ensure-shares reads count via GET /api/v0/application, triggers PUT /api/v0/shares rescan when 0, surfaces if unrecoverable | unit | `cd app && python3 -m pytest tests/test_shares.py -q` | ❌ W0 (RED in 05-02) | ⬜ pending |
| 5-05-01 | 05 | 3 | REL-03 | T-05-20 | GET /status renders stuck/quarantined/permanently-unavailable counts + reasons (HTML + JSON), html.escape XSS defense | unit | `cd app && python3 -m pytest tests/test_status_page.py -q` | ✅ created in 05-05 | ⬜ pending |
| 5-04-02 | 04 | 2 | REL-01 | T-05-16 | daemon thread runs the cycle on the single locked connection, honors ACQ_ENABLED kill-switch + ACQ_DRY_RUN; lifecycle + apply_result transitions | unit | `cd app && python3 -m pytest tests/test_scheduler.py -q` | ✅ created in 05-04 | ⬜ pending |
| 5-04-02 | 04 | 2 | REL-01 | T-05-14/15 | ≤ MAX_CONCURRENT simultaneous acquisitions; single-writer LockedConn holds (no `database is locked`) | unit | `cd app && python3 -m pytest tests/test_concurrency.py -q` | ✅ created in 05-04 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

> Only test modules a Wave-0 task (05-01 / 05-02) actually creates are listed here. `test_scheduler.py` and
> `test_concurrency.py` are NOT Wave-0 stubs — they are created with their code directly in Wave 2 (05-04),
> mirroring Phase 4's 04-04 (no scheduler pre-stub). `test_status_page.py` is created in Wave 3 (05-05).

- [ ] `app/tests/test_migration_0003.py` (05-01) — migration/preservation stubs for STATE-03
- [ ] `app/tests/test_eligibility.py` (05-01) — eligibility predicate (grace + backoff + dormant) stubs for GAP-03 / STATE-03
- [ ] `app/tests/test_backoff.py` (05-01) — backoff_for pure-function stubs for STATE-03
- [ ] `app/tests/test_reconcile.py` (05-02 scaffold RED → 05-03 GREEN) — startup reconciliation + infra-vs-genuine classifier + searching/downloading/importing orphan reset for REL-02
- [ ] `app/tests/test_shares.py` (05-02 scaffold RED → 05-03 GREEN) — slskd ensure/self-heal stubs (FakeSlskd) for SHARE-01/02
- [ ] `app/tests/test_infra_classify.py` (05-02) — INFRA_EXC classifier stubs for REL-02
- [ ] `app/tests/fakes.py` (05-02) — `FakeSlskd` (get_shared_file_count / rescan_shares, rescan call-counted) imported directly by the shares/reconcile/scheduler tests
- [ ] Extend `app/tests/conftest.py` (05-01) — `seed_v0002_ledger` + `frozen_clock` fixtures (the scheduler/reconcile tests consume these read-only)

*Existing pytest infrastructure (app/tests/) covers the framework; only new test modules are needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| First capped daemon pass imports one real album end-to-end | REL-01, GAP-03 | Requires live slskd peers + Lidarr on the NAS | Set `ACQ_DRY_RUN=false`, `MAX_CONCURRENT=1`; watch one item go search→download→import→verify→purge; confirm in Lidarr + `/status` |
| slskd `application` JSON shape + *arr queue album-id field (A2/A3) | SHARE-01/02, GAP-03 | Exact live JSON not confirmable offline | One live `curl` probe on the NAS (mirrors Phase-4 A1/A2/A3); reconcile offline fixtures to it (05-05 Task 3 live checkpoint) |
| Infra outage burns no attempt | REL-02 | Requires forcing a real VPN/slskd/*arr restart | Kill slskd mid-cycle; confirm the item's `attempt_count` did NOT increment and no orphan/double-import on recovery |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
</content>
