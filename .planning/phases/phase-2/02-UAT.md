---
status: partial
phase: 02-state-ledger-arr-adapter-gap-detection
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md, 02-03-SUMMARY.md, 02-04-SUMMARY.md]
started: 2026-05-30T00:00:00Z
updated: 2026-05-30T00:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Cold Start Smoke Test
expected: App boots from scratch; startup migration runs; GET /healthz returns 200 with {status:ok, phase:2, version:0.2.0-phase2}; DB file created with journal_mode=wal, user_version=1, items table present.
result: pass
evidence: Ran live in sandbox via TestClient — HTTP 200, body {"status":"ok","phase":2,"version":"0.2.0-phase2"}; DB journal_mode=wal, user_version=1, tables=[items].

### 2. Ledger persists with lifecycle status, survives restart (STATE-01)
expected: A tracked gap is written to SQLite with a lifecycle status keyed on stable *arr identity; closing the connection and reopening the DB file shows the row + status intact.
result: pass
evidence: Automated — test_state_repo.py restart-durability test (close conn → reopen file → row+status intact) passes in the 31-test suite.

### 3. Detects monitored missing AND cutoff-unmet via the adapter (GAP-01, GAP-02)
expected: The adapter pages /api/v1/wanted/missing and /api/v1/wanted/cutoff and yields uniform GapItems (identity + quality profile/cutoff) for both kinds.
result: pass
evidence: Automated — test_lidarr_adapter.py (missing mapping, cutoff + multi-page paging) + test_gap_detector.py end_to_end_counts pass against recorded lidarr_missing/cutoff fixtures.

### 4. Re-running detection adds no duplicate; status not clobbered (STATE-02)
expected: Re-running gap detection over the same items creates ZERO duplicate ledger rows; an already-imported/searching row that still appears in wanted/cutoff keeps its status (the upsert refreshes metadata only).
result: pass
evidence: Automated — repo test_dedup_no_duplicate + test_upsert_preserves_status, and gap_detector test_dedup_on_rerun + test_dedup_preserves_status_end_to_end pass. Code-review verified the ON CONFLICT SET clause omits status.

### 5. Readarr garbage/empty metadata degrades gracefully; music never gated (ARR-02)
expected: Feeding Readarr empty/garbage/5xx/timeout responses yields zero book items (skipped + logged), the circuit breaker opens, and the Lidarr (music) items are still fully detected/persisted — no crash, no stall.
result: pass
evidence: Automated — test_readarr_adapter.py (empty/garbage/5xx/breaker) + test_gap_detector.py readarr_fault_does_not_gate_music pass. Breaker now also has half-open cooldown recovery (post-review fix WR-04).

### 6. Live *arr integration + restart durability on /volume1 (NAS-only)
expected: Against the REAL Lidarr (and best-effort Readarr) on the NAS: the wanted/missing + cutoff envelopes and identity/profile fields match the fixtures; `python -m core.gap_detector` upserts real gaps; the ledger on the /volume1 /db mount survives a container restart with rows + WAL checkpoint intact.
result: blocked
blocked_by: prior-phase
reason: Requires the live VPN/NAS deploy (Lidarr/Readarr reachable, /volume1 bind-mount) — not runnable from this sandbox, and nothing is pushed/deployed yet (32 unpushed commits; NAS still on the Phase 1 image). Deferred to the next deploy (Phase-2 equivalent of Phase 1's on-NAS Go/No-Go smoke). Readarr BookResource exact field names (A-R1/A-R2) resolve here.

## Summary

total: 6
passed: 5
issues: 0
pending: 0
skipped: 0
blocked: 1

## Gaps

[none — no issues found; test 6 is a deploy-gated prerequisite, not a code defect]
