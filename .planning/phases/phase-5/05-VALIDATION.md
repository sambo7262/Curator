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

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 5-01-01 | 01 | 0 | STATE-03 | — | migration_0003 adds attempt_count/next_attempt_at/last_checked_at + permanently-unavailable status; ~1493 rows survive | unit | `cd app && python3 -m pytest tests/test_state_repo.py -q` | ❌ W0 | ⬜ pending |
| 5-0x-xx | xx | x | GAP-03 | — | eligibility predicate selects only grace-elapsed + backoff-elapsed + non-permanently-unavailable items | unit | `cd app && python3 -m pytest tests/test_scheduler.py -q` | ❌ W0 | ⬜ pending |
| 5-0x-xx | xx | x | REL-02 | T-5-xx | infra outage (slskd/*arr/VPN unreachable) classified infra → no attempt burned; genuine failure burns one; no double-import on restart | unit | `cd app && python3 -m pytest tests/test_reconcile.py -q` | ❌ W0 | ⬜ pending |
| 5-0x-xx | xx | x | SHARE-01/02 | — | ensure-shares reads count via GET /api/v0/application, triggers PUT /api/v0/shares rescan when 0, surfaces if unrecoverable | unit | `cd app && python3 -m pytest tests/test_shares.py -q` | ❌ W0 | ⬜ pending |
| 5-0x-xx | xx | x | REL-03 | — | GET /status renders stuck/quarantined/permanently-unavailable counts + reasons (HTML + JSON) | unit | `cd app && python3 -m pytest tests/test_status_page.py -q` | ❌ W0 | ⬜ pending |
| 5-0x-xx | xx | x | REL-01 | — | daemon thread runs the cycle on the single locked connection, honors ACQ_ENABLED kill-switch + ACQ_DRY_RUN + MAX_CONCURRENT | unit | `cd app && python3 -m pytest tests/test_daemon.py -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `app/tests/test_scheduler.py` — eligibility predicate (grace + backoff + dormant) stubs for GAP-03 / STATE-03
- [ ] `app/tests/test_reconcile.py` — startup reconciliation + infra-vs-genuine classifier stubs for REL-02
- [ ] `app/tests/test_shares.py` — slskd ensure/self-heal stubs (fake slskd client) for SHARE-01/02
- [ ] `app/tests/test_daemon.py` — daemon-thread lifecycle / kill-switch / dry-run / concurrency stubs for REL-01
- [ ] `app/tests/test_status_page.py` — status page HTML+JSON stubs for REL-03
- [ ] Extend `app/tests/conftest.py` — fake clock + fake slskd `application`/`shares` fixtures + fake *arr queue fixtures

*Existing pytest infrastructure (app/tests/) covers the framework; only new test modules are needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| First capped daemon pass imports one real album end-to-end | REL-01, GAP-03 | Requires live slskd peers + Lidarr on the NAS | Set `ACQ_DRY_RUN=false`, `MAX_CONCURRENT=1`; watch one item go search→download→import→verify→purge; confirm in Lidarr + `/status` |
| slskd `application` JSON shape + *arr queue album-id field (A2/A3) | SHARE-01/02, GAP-03 | Exact live JSON not confirmable offline | One live `curl` probe on the NAS (mirrors Phase-4 A1/A2/A3); reconcile offline fixtures to it |
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
