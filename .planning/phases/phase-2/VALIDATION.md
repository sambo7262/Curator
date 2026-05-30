---
phase: 2
slug: state-ledger-arr-adapter-gap-detection
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-05-30
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from RESEARCH.md "## Validation Architecture". The dev sandbox is Python 3.9 + offline,
> so all *arr interaction is verified against **recorded JSON fixtures**; no live Lidarr/Readarr
> is required to ship Phase 2.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x (already present — `app/tests/test_health.py`) |
| **Config file** | `pyproject.toml` (pytest config) |
| **Quick run command** | `pytest -q` |
| **Full suite command** | `pytest` |
| **Estimated runtime** | ~5–15 seconds (no network; fixtures + in-memory/temp SQLite) |

---

## Sampling Rate

- **After every task commit:** Run `pytest -q`
- **After every plan wave:** Run `pytest`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

> Filled by the planner. Each Phase 2 success criterion maps to deterministic, offline-runnable proof:

| Criterion | Requirement | Test Type | Proof (fixture-based, offline) | Status |
|-----------|-------------|-----------|--------------------------------|--------|
| 1. Persists w/ lifecycle status, survives restart | STATE-01 | unit | Insert gap → close connection → reopen DB file → row + status intact | ⬜ pending |
| 2. Detects missing + cutoff via adapter | GAP-01, GAP-02 | unit | `lidarr_missing.json` / `lidarr_cutoff.json` fixtures → adapter yields uniform gap items | ⬜ pending |
| 3. Readarr garbage/empty degrades gracefully | ARR-02 | unit | `readarr_empty.json` / `readarr_garbage.json` → book item skipped + logged, music path unaffected, no crash | ⬜ pending |
| 4. Dedup — no duplicate ledger entry | STATE-02 | unit | Run detection twice over same fixture → `UNIQUE(arr_app, arr_id)` upsert; row count stable; status NOT clobbered | ⬜ pending |
| Adapter exposes identity + profile/cutoff uniformly | ARR-01 | unit | Lidarr + Readarr fixtures → same internal item shape (identity + profile/cutoff fields) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `app/tests/fixtures/` — recorded *arr JSON: `lidarr_missing.json`, `lidarr_cutoff.json`, `readarr_missing.json`, `readarr_empty.json`, `readarr_garbage.json`
- [ ] `app/tests/conftest.py` — shared fixtures: temp SQLite DB path, fixture loader, fake adapter HTTP layer (httpx mock / recorded responses)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Live Lidarr `wanted/missing` + `cutoff` shape matches fixtures | GAP-01, GAP-02 | Needs live *arr on NAS (sandbox offline) | On NAS: hit `GET /api/v1/wanted/missing` against real Lidarr, diff envelope/identity fields vs fixtures |
| Readarr `BookResource` exact foreign-id/profile key names (A-R1/A-R2) | ARR-02 | Readarr field names MEDIUM-confidence; needs live Readarr | On NAS: enable Readarr branch best-effort, confirm defensive parsing handles real payload |
| Restart durability on real `/volume1` mount | STATE-01 | Bind-mount FS behavior is host-specific | On NAS: restart container, confirm ledger rows + WAL checkpoint survive |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references (fixtures + conftest)
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
