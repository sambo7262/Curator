# Phase 5: Autonomy, Sharing & Self-Recovery - Context

**Gathered:** 2026-05-31
**Status:** Ready for planning

<domain>
## Phase Boundary

Make the closed Phase-4 acquisition loop run **itself**, indefinitely, and politely: a scheduled
daemon detects gaps each cycle, acts only on grace-elapsed + Usenet-clear items, downloads up to a
bounded concurrency, applies exponential backoff with permanent-unavailable memory, auto-ensures
slskd sharing stays active, self-recovers from infra outages without burning per-item attempts or
double-importing, and surfaces stuck items in a browser-viewable status page.

Requirements in scope: **GAP-03, STATE-03, SHARE-01, SHARE-02, REL-01, REL-02, REL-03.**

NOT in scope (later phases): push notifications (Apprise/Pushover) + the polished Homepage widget
(Phase 6); raising concurrency beyond the bounded default / ops tuning (owner action post-trust).
This phase clarifies HOW to make Phase 4 autonomous — it adds no new acquisition capability.
</domain>

<decisions>
## Implementation Decisions

### Grace window & Usenet-race avoidance (GAP-03)
- **D-01:** Grace window = **3 days**, env-tunable. The clock starts from the ledger's `discovered_at`
  (first detected as wanted). Because Phase-2's status-preserving upsert never clobbers `discovered_at`
  on re-detect, the existing ~1,493-gap backlog keeps its original first-seen timestamp and is therefore
  **already past grace at launch → immediately eligible**; only items first detected after launch wait the
  fresh 3 days. CRITICAL: grace is per-item Usenet politeness, NOT rollout safety — the backlog flood is
  held back by the concurrency cap (D-04/D-05), not by grace.
- **D-02:** Race avoidance = **check the *arr download queue** (the `queue` adapter method stubbed in
  Phase 2) and skip any item with an active/queued Usenet grab. Combined with wanted-list semantics
  (a Usenet-imported item leaves the *arr wanted list, so `get_wanted` never returns it), this fully
  closes the race: the only window is an in-flight grab, which the queue check catches. Time-only grace
  and a direct SABnzbd integration were both rejected.

### Daemon, cadence & bounded rollout (REL-01 + owner bounded-rollout decision)
- **D-03:** Curator runs as a continuous **daemon with a scheduled poll loop, default 6h interval**
  (env-tunable), no manual triggering. Each cycle: detect gaps → filter to eligible (grace + queue +
  backoff) → acquire up to the concurrency cap.
- **D-04:** Concurrency cap `MAX_CONCURRENT`, **steady-state = 3** simultaneous acquisitions (env-tunable).
- **D-05:** Staged rollout via **simple env flags + a global kill-switch** (owner constraint: "easy to
  manage", no auto-promotion ceremony): `ACQ_DRY_RUN` (search + gate + log the would-be winner, ZERO
  side effects — no download/import/status change), `MAX_CONCURRENT` (int), and an `ACQ_ENABLED`
  kill-switch to halt instantly. Owner manually promotes: **dry-run → first live pass at cap=1 → raise
  to 3.**
- **D-06:** Live validation (REL acceptance test) = **observe the first capped daemon pass at
  `MAX_CONCURRENT=1`** — one album flows end-to-end (search → gate → download → ManualImport `move` →
  verify-by-requery → purge staging) and the owner watches it. This IS the "one full live import test."
  No separate single-item manual trigger is built — this supersedes the RESEARCH-SEED.md "single-item
  trigger" suggestion (owner prefers observing the daemon; simpler).

### Retry backoff & give-up (STATE-03)
- **D-07:** **3 failed acquisition attempts** → mark the item **permanently-unavailable** (a new ledger
  status). Requires a per-item attempt counter.
- **D-08:** **Exponential backoff** between retries: **1h → 6h → 24h** (capped at 24h). Requires a
  per-item next-eligible timestamp; an item is skipped until its backoff elapses.
- **D-09:** Permanently-unavailable items get a **30-day dormant re-check** (long-TTL): after 30 days they
  re-enter the eligible pool once (a new uploader may have appeared). Requires a last-checked timestamp.

### Sharing automation (SHARE-01/02)
- **D-10:** **Ensure + self-heal**, NOT config-ownership: slskd.yml keeps the owner-set share dirs;
  Curator each cycle verifies shared-file count > 0 via the slskd API, triggers a rescan if it dropped,
  and surfaces an issue (D-12) if it can't recover. Curator does NOT rewrite slskd.yml (avoids breaking
  slskd config). Researcher MUST confirm slskd's API surface for reading share count + triggering a rescan.
- **D-11:** Shared content = **`/data/media/music` + `/data/media/books`, container-internal, read-only**
  (the clean library — never the download/staging tree). Matches what the owner configured live 2026-05-31.

### Issue visibility (REL-03 + owner addition)
- **D-12:** Phase 5 serves a **bare-bones, read-only HTML status page in the FastAPI app**, browser-viewable
  (e.g. `GET /status` on `:8674`), listing stuck / quarantined / permanently-unavailable items with counts
  + reasons, plus healthy throughput. "Simple UI via browser" — server-rendered HTML, no JS framework. The
  same underlying data is also exposed as JSON so the Phase-6 Homepage `customapi` widget can reuse it.
- **D-13:** **Push notifications deferred to Phase 6.** Owner has **Pushover** configured (Apprise supports
  Pushover natively) → Phase 6 wires alerts to it. Phase 5 only EXPOSES issues (D-12), it does not push.

### Self-recovery / startup reconciliation (REL-02)
- **D-14:** *(Claude's discretion — owner accepted the default, may revisit at plan time.)* On startup,
  items left in non-terminal acquisition states (downloading/importing) are **cleanly reset and re-attempted
  from scratch** (no attempt to resume a partial slskd transfer), with a verify-by-requery guard so an item
  whose import actually completed is NOT re-imported (no double-import, no orphans). **Infra outages**
  (VPN/slskd/Lidarr/Readarr unreachable, network blips) are classified as infra failures that **do NOT
  consume a per-item retry attempt** — distinct from a genuine acquisition failure (D-07), which does.

### Carry-forward / technical (planner handles — not re-litigated)
- **D-15:** **Batch the detection-pass writes** (RESEARCH-SEED.md): wrap each cycle's `detect_gaps` upserts
  in a single transaction (one fsync per pass, not per row) — preserve STATE-02 dedup, status-never-clobbered,
  and `synchronous=FULL`. Do not regress the firewall or single-writer model.
- **D-16:** Preserve the single-writer SQLite model + the `/detect` 409 guard — the scheduler must run on the
  app's single connection (no concurrent writers). New backoff/status fields land via **migration_0003**.

### Claude's Discretion
- D-14 startup-reconciliation policy (default captured; confirm at plan time).
- Dry-run log format, status-page exact layout, scheduler library choice (APScheduler vs asyncio loop) —
  planner/researcher decide.
</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase-5 pre-planning (MANDATORY)
- `.planning/phases/phase-5/RESEARCH-SEED.md` — bounded-rollout decision (dry-run → single-item → bounded
  autonomy, never firehose the ~1493-gap list), the detection batch-fsync perf fix (D-15), and the Phase-5
  scope reminders. The researcher/planner MUST address every item here.

### Phase / requirement scope
- `.planning/ROADMAP.md` §"Phase 5: Autonomy, Sharing & Self-Recovery" — goal + the 5 success criteria.
- `.planning/REQUIREMENTS.md` — GAP-03, STATE-03, SHARE-01, SHARE-02, REL-01, REL-02, REL-03 (full text).
- `.planning/PROJECT.md` — the fallback-only / Usenet-first core principle the daemon must honor.

### What this phase builds on (Phase 4 reality)
- `.planning/phases/phase-4/04-CONTEXT.md` — D-01..D-12 acquisition decisions the daemon wraps.
- `.planning/phases/phase-4/04-05-LIVE-PROBE.md` — the live slskd/Lidarr shapes `acquire_item` is pinned to
  (A1 ManualImport `move` envelope, A2 remote-folder-leaf landing, A3 `Completed, Succeeded` terminal rule).
- `DEPLOY.md` §"Step 8 — Phase 4 setup" — NAS path-identity, the bare-key SLSKD_API_KEY rule, shares setup.

No external ADRs/specs — requirements fully captured in the decisions above.
</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app/core/acquire.py` `acquire_item(...)` — the single-item loop the daemon wraps with scheduling +
  concurrency + grace/queue/backoff gating. Returns "imported" | "quarantined" | "stuck".
- `app/core/gap_detector.py` `detect_gaps` — the detection pass the daemon runs each cycle (apply D-15 batch txn).
- `app/state/repo.py` — status lifecycle (`set_status`), staged_files DAOs. ADD: attempt counter,
  next-eligible/backoff timestamp, permanently-unavailable status, last-checked (dormant re-check) — migration_0003.
- `app/adapters/*.py` (`ArrAdapter`) — has a **stubbed `queue` method** (Phase 2) for the D-02 race check;
  the slskd client (search/transfer) needs a shares-count read + rescan call for D-10.
- `app/config.py` `Settings.from_env()` — where new env tunables land: grace seconds, poll interval,
  `MAX_CONCURRENT`, `ACQ_DRY_RUN`, `ACQ_ENABLED`, retry ceiling, backoff schedule, dormant re-check TTL.
- `app/main.py` — FastAPI app with `POST /detect` (single-writer, 409 guard). The scheduler lives here;
  add the `GET /status` browser page + JSON.

### Established Patterns
- `items.status` enum was widened in Phase 4 (downloading/importing/imported/quarantined/stuck) — Phase 5
  adds `permanently-unavailable` + backoff fields via migration_0003.
- Single-writer SQLite + `/detect` 409 guard — the scheduler MUST use the app's single connection (no
  concurrent writers); concurrency D-04 parallelizes the *download/IO*, not ledger writes.
- *arr firewall (core/state neutral); status-preserving upsert (preserves status + discovered_at — load-bearing for D-01).

### Integration Points
- Scheduler: `detect_gaps` (batched) → eligibility filter (grace D-01 + queue D-02 + backoff D-08/09) →
  `acquire_item` per eligible item up to `MAX_CONCURRENT` (D-04), respecting `ACQ_DRY_RUN`/`ACQ_ENABLED`.
- slskd shares ensure/self-heal via the slskd client (D-10).
- `GET /status` reads the ledger for the issue view (D-12).
</code_context>

<specifics>
## Specific Ideas

- "Simple UI via browser works for me" — a bare HTML status page, not raw JSON dumped in the browser (D-12).
- Owner has **Pushover** set up; wants push kept simple → it's a Phase-6 wiring, not Phase 5 (D-13).
- "Easy to manage from my perspective" — rollout = simple env flags, no auto-promotion ceremony (D-05).
- The first live daemon pass at cap=1 = the owner's "one full test where an album is downloaded and the
  rest of the system works in good imports" (D-06).
- Owner's own framing validated the design: "if the file is in the *arr it leaves the wanted queue" → the
  race only exists for in-flight grabs, which the queue check (D-02) catches.
</specifics>

<deferred>
## Deferred Ideas

- **Push notifications (Apprise → Pushover)** → Phase 6 (Observability & Notifications). Owner has Pushover ready.
- **Polished Homepage `customapi` widget** → Phase 6 (Phase 5 exposes the JSON it will consume).
- **Raising concurrency beyond 3 / ops tuning** → owner action after the bounded rollout earns trust.
- **Direct SABnzbd integration** for race detection → rejected (the *arr queue check D-02 suffices).

None of these are scope creep into Phase 5 — discussion stayed within the autonomy/sharing/recovery boundary.
</deferred>

---

*Phase: 5-autonomy-sharing-self-recovery*
*Context gathered: 2026-05-31*
