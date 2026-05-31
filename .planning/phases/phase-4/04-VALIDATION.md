---
phase: 4
slug: acquisition-staging-clean-import
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-05-31
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Per-task map is populated by the planner; infra/sampling/Wave-0 below are derived from 04-RESEARCH.md §Validation Architecture.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | none — pytest discovery under `app/` (existing) |
| **Quick run command** | `cd app && python3 -m pytest -q` |
| **Full suite command** | `cd app && python3 -m pytest` |
| **Estimated runtime** | ~1–2 seconds |

New test dependency expected (Wave 0): `respx` (httpx mock transport) for faking the slskd + *arr REST surfaces offline. Pin + human-verify per the package-legitimacy checkpoint precedent (Phase 2 httpx / Phase 3 rapidfuzz).

---

## Sampling Rate

- **After every task commit:** Run `cd app && python3 -m pytest -q`
- **After every plan wave:** Run `cd app && python3 -m pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** ~2 seconds

---

## Per-Task Verification Map

*Populated by the planner once PLAN.md files exist. Every task covering ACQ-01/02/03 + IMPORT-01/02/03/05 must map to an automated `respx`/fake-backed test or a Wave-0 fixture dependency, except the live-NAS behaviors below. (IMPORT-04 is satisfied externally by the owner's Plex auto-scan — revised D-04 — not by Curator code or tests.)*

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| (planner fills) | | | | | | | | | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `respx` pinned in `app/requirements.txt` (httpx mock transport) — human-verified legitimacy
- [ ] `app/tests/conftest.py` — shared fixtures: fake slskd search/transfer responses, fake *arr manualimport mapping + ManualImport command, temp `/data` staging tree
- [ ] slskd search/transfer response fixtures (drive `Candidate.from_slskd` + stall detection)
- [ ] *arr ManualImport mapping + command fixtures (drive D-09 wanted-file filtering + import verification)

*Existing infrastructure (pytest, app/tests) covers the harness; only the fakes/fixtures above are new.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| First real slskd download against live peers | ACQ-02 | Requires live VPN/PF + real Soulseek peers; can't be faked authentically | Gated by D-11 (slskd shares configured, shared-count > 0). Trigger an acquisition for one known gap; confirm transfer completes into the staging dir. |
| Exact slskd terminal transfer-`state` strings (A3) | ACQ-03 | slskd source uses compound state flags; exact strings need a live probe | Wave-0 live probe on NAS: enqueue one download, log `GET /transfers/downloads/...` state transitions. |
| Exact ManualImport POST envelope + importMode casing (A1) | IMPORT-02 | Highest-value live verification | Capture one real Lidarr "Manual Import" POST via browser DevTools on NAS; confirm Curator's payload matches. |
| slskd `batchId` settability on enqueue (A2) | IMPORT-01 | Determines staging-path strategy; documented fallback exists | Live probe: enqueue with batchId, confirm `downloads/{batchId}/...` path; else use remote-folder fallback. |
| End-to-end import lands in `/volume1` | IMPORT-03 | Needs live *arr + library volume | After a live import, re-query *arr and confirm the item left the wanted/missing list (D-03 — import landed in `/volume1`). |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (respx + fakes/fixtures)
- [ ] No watch-mode flags
- [ ] Feedback latency < 2s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
