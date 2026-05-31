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

## Bounded rollout + live validation — DO NOT firehose the wanted list (owner decision, 2026-05-31)

**Context:** Phase 4 delivered the full single-item `acquire_item` loop (offline-proven + live-pinned to
the real slskd/Lidarr shapes in 04-05). It has NO production trigger yet — by design (the daemon is
Phase 5). The ledger already holds **~1,493 gaps**. The owner's explicit concern: switching the Phase-5
daemon on naively would start a full autonomous flow across the entire wanted list at once (mass parallel
Soulseek downloads, disk pressure, leech/rate risk, hard to observe/abort).

**Owner decision:** do NOT build a throwaway Phase-4 smoke-test trigger now. Instead, **bake a safe,
bounded, controlled live-validation path into Phase 5** — prove ONE acquisition end-to-end in prod
*without* unleashing the whole flow. The Phase 5 plan MUST include, and stage in this order:

1. **Dry-run / observe mode** — run search + `gate.evaluate` against LIVE slskd for a gap and LOG the
   would-be winner + decision, with ZERO side effects (no download, no import, no ledger status change).
   Validates matching/gating against real peers safely. (A config flag, e.g. `ACQ_DRY_RUN`.)
2. **Single-item live trigger ("prod unit test")** — acquire exactly ONE owner-named gap end-to-end
   (download→import→verify→purge), independent of the scheduler. This is the controlled smoke test,
   now a Phase-5 deliverable rather than ad-hoc. (Owner wanted: pick a missing album known to be on
   Soulseek, watch it import hands-off.)
3. **Bounded autonomy before full autonomy** — a global enable kill-switch + a per-run cap
   (max items per pass) + max-concurrent downloads, so the first real daemon run is small and
   observable. Never go 0 → 1493 unattended. Tie into the grace window (Usenet-first / fallback-only).

**Constraints:** keep the firewall + single-writer model; the trigger/daemon must consume zero per-item
attempts on infra outage (REL-02/03); the staged path above is the acceptance route for "Phase 5 is safe
to leave running." Touch points: a small acquire trigger (endpoint or CLI on the single app connection,
mirroring `POST /detect`), the scheduler, and `app/config.py` (dry-run + caps + kill-switch tunables).

## Phase 5 scope reminders (from ROADMAP)
Requirements: GAP-03, STATE-03, SHARE-01, SHARE-02, REL-01, REL-02, REL-03. The grace-gated daemon,
exponential backoff + permanent-unavailable memory, slskd sharing (leech-block avoidance), and
self-recovery from infra outages (an outage must consume zero per-item attempts) + surfacing stuck
items. The `POST /detect` endpoint (Phase 2) is the in-process trigger the scheduler will call on
the app's single connection.
