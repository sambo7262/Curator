# Phase 5 Research Seed — Autonomy, Sharing & Self-Recovery

> Pre-planning notes captured before Phase 5 research/discuss runs.
> The Phase 5 researcher/planner MUST read and address these.

## Deferred from Phase 2 (perf) — batch the detection-pass writes

**Finding (live on NAS, 2026-05-30):** `core/gap_detector.detect_gaps` calls `repo.upsert_gap`
once per item, and the ledger connection runs `PRAGMA synchronous=FULL` in autocommit — so the
initial bulk detection of a large Lidarr library does **one `fsync` per row**. Observed ~5–6
rows/sec on the DS423+ volume; a first pass over a 1500+ gap library takes minutes.

**This is NOT a bug** — it's correct and durable; it was simply slow on the first full load.
`synchronous=FULL` is intentional (WR-06: a lost `status='imported'` would re-trigger acquisition).

**Phase 5 action:** when building the scheduled detection loop, wrap each detection pass's upserts
in a **single explicit transaction** (one `fsync` for the batch instead of one per row) — keeps
full durability on each committed pass while making bulk detection ~100× faster. Options: wrap the
whole `detect_gaps` pass in `BEGIN/COMMIT`, or batch per-adapter / per-page. **Constraints to preserve:**
- STATE-02 dedup via `ON CONFLICT(arr_app, arr_id)` and the status-never-clobbered rule (do not regress).
- Do NOT lower `synchronous` below FULL (durability of lifecycle status is load-bearing).
- Keep the firewall (no *arr field names in core/state) and the single-writer model.
- Touch points: `app/core/gap_detector.py` (detect_gaps), `app/state/repo.py` (a batch/txn wrapper),
  and the new scheduler that calls detection.

## Phase 5 scope reminders (from ROADMAP)
Requirements: GAP-03, STATE-03, SHARE-01, SHARE-02, REL-01, REL-02, REL-03. The grace-gated daemon,
exponential backoff + permanent-unavailable memory, slskd sharing (leech-block avoidance), and
self-recovery from infra outages (an outage must consume zero per-item attempts) + surfacing stuck
items. The `POST /detect` endpoint (Phase 2) is the in-process trigger the scheduler will call on
the app's single connection.
