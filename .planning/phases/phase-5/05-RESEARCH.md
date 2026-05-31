# Phase 5: Autonomy, Sharing & Self-Recovery - Research

**Researched:** 2026-05-31
**Domain:** Scheduled-daemon orchestration over a synchronous single-writer SQLite loop (Python 3.12 / FastAPI), slskd `/api/v0` shares automation, exponential-backoff retry state machine, crash-recovery reconciliation, server-rendered status HTML
**Confidence:** HIGH (slskd shares API pinned to a maintained integration + slskd source; scheduler/concurrency model derived directly from the existing code's invariants; all schema/eligibility work is local SQL)

## Summary

Phase 5 adds **no new acquisition capability** — it wraps the already-proven Phase-4 `acquire_item` loop in a scheduled, bounded, self-healing daemon. The entire phase is buildable with **zero new external dependencies**: stdlib `threading` + `concurrent.futures.ThreadPoolExecutor`, the existing `httpx`/`sqlite3`, and the existing `SlskdClient`. The dominant design constraint — repeated in every Phase-2/3/4 decision and proven by the firewall greps — is the **single-writer SQLite model**: exactly one connection (`app.state.db`) does all ledger writes, serialized by the existing `threading.Lock`. Concurrency (`MAX_CONCURRENT`) parallelizes only the **download/IO** (slskd searches, transfers, *arr import HTTP), never the ledger writes.

The scheduler should be a **plain daemon thread running a poll loop** (not APScheduler, not asyncio) because (a) `acquire_item` and every adapter/client in this codebase is **synchronous blocking httpx** — introducing `AsyncIOScheduler` would force an async rewrite of the entire proven loop; (b) the existing `/detect` 409 guard is already a `threading.Lock`, so a thread-based scheduler reuses the exact established serialization primitive; (c) a single daemon thread + a small worker pool needs no new package and no multi-worker double-firing concern (uvicorn runs one worker here). The eligibility query, backoff schema, and dormant re-check are all **local SQL over the `items` table** via `migration_0003` (add `attempt_count`, `next_attempt_at`, `last_checked_at`, and the `permanently-unavailable` status). The slskd shares ensure/self-heal (D-10) maps to two confirmed `/api/v0` calls: read `GET /api/v0/application` → `shares.files` for the count, and `PUT /api/v0/shares` to trigger a rescan.

**Primary recommendation:** Build a single stdlib daemon-thread scheduler that, each cycle, runs (1) batched `detect_gaps` in one transaction, (2) a shares ensure/self-heal check, (3) an eligibility SQL select, then dispatches up to `MAX_CONCURRENT` `acquire_item` calls through a `ThreadPoolExecutor` while routing **all** ledger writes back through the single app connection guarded by the existing lock. Add `migration_0003` for the backoff/attempt/last-checked columns and the `permanently-unavailable` status. Serve a bare server-rendered HTML `GET /status` + a `GET /status.json` from the same ledger queries. Stage rollout with three env flags (`ACQ_ENABLED`, `ACQ_DRY_RUN`, `MAX_CONCURRENT`). No new pip dependency.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Grace window & Usenet-race avoidance (GAP-03)**
- **D-01:** Grace window = **3 days**, env-tunable. Clock starts from the ledger's `discovered_at` (first detected as wanted). Because Phase-2's status-preserving upsert never clobbers `discovered_at`, the existing ~1,493-gap backlog keeps its original first-seen timestamp → **already past grace at launch → immediately eligible**; only items first detected after launch wait the fresh 3 days. CRITICAL: grace is per-item Usenet politeness, NOT rollout safety — the backlog flood is held back by the concurrency cap (D-04/D-05), not by grace.
- **D-02:** Race avoidance = **check the *arr download queue** (the `queue` adapter method stubbed in Phase 2 — actually named `get_queue_status` in base.py) and skip any item with an active/queued Usenet grab. Combined with wanted-list semantics (a Usenet-imported item leaves the *arr wanted list, so `get_wanted` never returns it), this fully closes the race. Time-only grace and a direct SABnzbd integration were both rejected.

**Daemon, cadence & bounded rollout (REL-01 + bounded-rollout decision)**
- **D-03:** Curator runs as a continuous **daemon with a scheduled poll loop, default 6h interval** (env-tunable), no manual triggering. Each cycle: detect gaps → filter to eligible (grace + queue + backoff) → acquire up to the concurrency cap.
- **D-04:** Concurrency cap `MAX_CONCURRENT`, **steady-state = 3** simultaneous acquisitions (env-tunable).
- **D-05:** Staged rollout via **simple env flags + a global kill-switch**: `ACQ_DRY_RUN` (search + gate + log the would-be winner, ZERO side effects — no download/import/status change), `MAX_CONCURRENT` (int), and an `ACQ_ENABLED` kill-switch to halt instantly. Owner manually promotes: **dry-run → first live pass at cap=1 → raise to 3.**
- **D-06:** Live validation (REL acceptance test) = **observe the first capped daemon pass at `MAX_CONCURRENT=1`** — one album flows end-to-end (search → gate → download → ManualImport `move` → verify-by-requery → purge staging) and the owner watches it. This IS the "one full live import test." No separate single-item manual trigger is built — this supersedes the RESEARCH-SEED.md "single-item trigger" suggestion.

**Retry backoff & give-up (STATE-03)**
- **D-07:** **3 failed acquisition attempts** → mark item **permanently-unavailable** (a new ledger status). Requires a per-item attempt counter.
- **D-08:** **Exponential backoff** between retries: **1h → 6h → 24h** (capped at 24h). Requires a per-item next-eligible timestamp; an item is skipped until its backoff elapses.
- **D-09:** Permanently-unavailable items get a **30-day dormant re-check** (long-TTL): after 30 days they re-enter the eligible pool once (a new uploader may have appeared). Requires a last-checked timestamp.

**Sharing automation (SHARE-01/02)**
- **D-10:** **Ensure + self-heal**, NOT config-ownership: slskd.yml keeps the owner-set share dirs; Curator each cycle verifies shared-file count > 0 via the slskd API, triggers a rescan if it dropped, and surfaces an issue (D-12) if it can't recover. Curator does NOT rewrite slskd.yml. Researcher MUST confirm slskd's API surface — **CONFIRMED below (Architecture Patterns / slskd Shares).**
- **D-11:** Shared content = **`/data/media/music` + `/data/media/books`, container-internal, read-only** (the clean library — never the download/staging tree). Already configured live 2026-05-31; Curator only *verifies/rescans*, never sets the dirs.

**Issue visibility (REL-03 + owner addition)**
- **D-12:** Phase 5 serves a **bare-bones, read-only HTML status page in the FastAPI app**, browser-viewable (e.g. `GET /status` on `:8674`), listing stuck / quarantined / permanently-unavailable items with counts + reasons, plus healthy throughput. Server-rendered HTML, no JS framework. The same underlying data is also exposed as JSON so the Phase-6 Homepage `customapi` widget can reuse it.
- **D-13:** **Push notifications deferred to Phase 6.** Owner has Pushover configured → Phase 6 wires alerts to it. Phase 5 only EXPOSES issues (D-12), it does not push. **DO NOT research/build Apprise/Pushover here.**

**Self-recovery / startup reconciliation (REL-02)**
- **D-14:** *(Claude's discretion — owner accepted the default.)* On startup, items left in non-terminal acquisition states (downloading/importing) are **cleanly reset and re-attempted from scratch** (no attempt to resume a partial slskd transfer), with a verify-by-requery guard so an item whose import actually completed is NOT re-imported (no double-import, no orphans). **Infra outages** (VPN/slskd/Lidarr/Readarr unreachable, network blips) are classified as infra failures that **do NOT consume a per-item retry attempt** — distinct from a genuine acquisition failure (D-07), which does.

**Carry-forward / technical**
- **D-15:** **Batch the detection-pass writes**: wrap each cycle's `detect_gaps` upserts in a single transaction (one fsync per pass, not per row) — preserve STATE-02 dedup, status-never-clobbered, and `synchronous=FULL`. Do not regress the firewall or single-writer model.
- **D-16:** Preserve the single-writer SQLite model + the `/detect` 409 guard — the scheduler runs on the app's single connection (no concurrent writers). New backoff/status fields land via **migration_0003**.

### Claude's Discretion
- D-14 startup-reconciliation policy (default captured; confirm at plan time).
- Dry-run log format, status-page exact layout, scheduler library choice (APScheduler vs asyncio loop vs plain thread — **this research recommends plain stdlib daemon thread, see Architecture Patterns**).

### Deferred Ideas (OUT OF SCOPE)
- **Push notifications (Apprise → Pushover)** → Phase 6 (Observability & Notifications).
- **Polished Homepage `customapi` widget** → Phase 6 (Phase 5 exposes the JSON it will consume).
- **Raising concurrency beyond 3 / ops tuning** → owner action after the bounded rollout earns trust.
- **Direct SABnzbd integration** for race detection → rejected (the *arr queue check D-02 suffices).
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GAP-03 | Act on an item only after a grace window elapses AND no active/queued Usenet grab exists | Eligibility SQL (`discovered_at` + grace, §Architecture Pattern 4) + `get_queue_status` adapter impl (§Don't Hand-Roll / Pattern 5) |
| STATE-03 | Exponential backoff + permanently remember unavailable items (long-TTL dormant re-check) | `migration_0003` columns + backoff/attempt state machine (§Architecture Pattern 3) |
| SHARE-01 | Configure slskd shares pointing at real read-only library content (anti-leech) | D-11 already configured live; Phase 5 *verifies* via `GET /api/v0/application`.`shares.files` (§slskd Shares API) |
| SHARE-02 | Sharing stays active and scanned (shared-file count > 0) with no manual intervention | Ensure/self-heal cycle: read count → `PUT /api/v0/shares` rescan → surface if unrecoverable (§slskd Shares API, Pattern 6) |
| REL-01 | Runs continuously as a daemon with a scheduled poll loop, no manual triggering | Plain stdlib daemon-thread scheduler (§Architecture Pattern 1) |
| REL-02 | Self-recover from transient failures; classify infra outages (no burned attempt); reconcile on startup (no orphans, no double-import) | Startup reconciliation + infra-vs-genuine-failure classifier (§Architecture Pattern 5) |
| REL-03 | Surface stuck items (exceeded retries / blocked / unresolved) rather than failing silently | `GET /status` HTML + `GET /status.json` over ledger (§Architecture Pattern 7) |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Scheduled poll loop (cadence, kill-switch) | Curator daemon thread (in-process, FastAPI app) | — | A homelab single-container service; a separate process/cron adds deploy surface for no gain. One uvicorn worker = no double-firing. |
| Eligibility filtering (grace, queue, backoff) | Curator core (SQL over ledger) + adapter (queue read) | *arr API (queue state) | Grace/backoff are pure ledger predicates; the *arr download-queue read is *arr-keyed → lives in the adapter behind the firewall. |
| Concurrency-bounded acquisition | Curator core (`ThreadPoolExecutor`, MAX_CONCURRENT) | slskd + *arr (IO) | Download/IO parallelism belongs in the worker pool; the proven `acquire_item` already encapsulates one item end-to-end. |
| Ledger writes (status, attempts, backoff) | Curator state (single SQLite writer connection) | — | Single-writer invariant — load-bearing across all phases. Workers NEVER write directly; they marshal results back to the writer thread. |
| Shares ensure/self-heal | Curator → slskd `/api/v0` | slskd share scanner | Curator reads the count + triggers a rescan; it never owns slskd.yml (D-10). |
| Startup reconciliation | Curator core + state | *arr (verify-by-requery) | Resetting orphaned in-flight rows is a ledger op; the double-import guard re-queries the *arr (adapter). |
| Status visibility | Curator FastAPI (server-rendered HTML + JSON) | — | Read-only view over the ledger; no control surface (project scope). |
| Infra-outage classification | Curator core (exception taxonomy) | slskd/*arr/VPN reachability | Distinguishes "can't reach the world" (no attempt burned) from "this item genuinely failed" (attempt burned). |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `threading` (stdlib) | 3.12 | Daemon scheduler thread + the existing `_detect_lock` writer serialization | Already the codebase's concurrency primitive (`main.py` `_detect_lock`); zero new dependency `[VERIFIED: app/main.py:20]` |
| `concurrent.futures.ThreadPoolExecutor` (stdlib) | 3.12 | Bounded `MAX_CONCURRENT` parallel `acquire_item` workers | Bounded pool size IS the concurrency cap; synchronous-friendly (acquire_item is blocking httpx) `[CITED: docs.python.org/3/library/concurrent.futures]` |
| `httpx` | 0.28.1 (pinned) | Existing slskd + *arr clients (incl. the new shares + queue calls) | Already pinned + human-verified (Phase 2 checkpoint) `[VERIFIED: requirements.txt]` |
| `sqlite3` (stdlib) | 3.12 | The single-writer ledger connection + `migration_0003` | The established persistence boundary; WAL + `synchronous=FULL` `[VERIFIED: app/state/db.py]` |
| `fastapi` | 0.115.6 (pinned) | The app hosting the scheduler lifecycle + `GET /status` HTML/JSON | Already the app framework `[VERIFIED: requirements.txt]` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `html` (stdlib) | 3.12 | `html.escape()` for the status page (peer/album titles are untrusted) | Rendering any ledger string into HTML — MANDATORY (XSS/HTML-injection defense, see Pitfalls) |
| FastAPI `HTMLResponse` | (fastapi) | Return server-rendered HTML from `GET /status` | The bare status page (D-12) — no template engine, no JS framework |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Plain daemon thread | **APScheduler (`AsyncIOScheduler`/`BackgroundScheduler`)** | Adds a dependency + a package-legitimacy checkpoint; `AsyncIOScheduler` would force an async rewrite of the synchronous `acquire_item`/adapter stack. `BackgroundScheduler` is thread-based (compatible) but still a new dep for a single interval job — not worth it. **REJECTED.** |
| `ThreadPoolExecutor` | `asyncio` + `run_in_executor` + `Semaphore` | The whole loop is synchronous blocking httpx + a `threading.Lock`-guarded sqlite connection; going async buys nothing and risks "asyncio is not thread-safe" footguns with the single connection. **REJECTED** — keep it synchronous. |
| In-process scheduler | Separate cron/systemd process | More deploy surface on Synology Container Manager; loses the shared single writer connection + the in-app `/detect` lock. **REJECTED.** |
| Jinja2 templates | f-string + `html.escape` | A single bare read-only page does not justify a template-engine dependency. **REJECTED** (use stdlib). |

**Installation:**
```bash
# NONE. Phase 5 introduces zero new runtime dependencies — stdlib threading/concurrent.futures/sqlite3/html
# + the already-pinned fastapi/httpx. requirements.txt is unchanged.
```

**Version verification:** No package install in this phase. The Python version is `python:3.12-slim` `[VERIFIED: Dockerfile FROM python:3.12-slim]`; `threading`, `concurrent.futures`, `sqlite3`, `html` are all stdlib in 3.12.

## Package Legitimacy Audit

**Not applicable — this phase installs zero external packages.** All work uses the Python 3.12 standard library (`threading`, `concurrent.futures`, `sqlite3`, `html`, `time`, `datetime`) plus the already-pinned, already-human-verified `fastapi==0.115.6` and `httpx==0.28.1`. No new line is added to `requirements.txt`, so there is no slopcheck/registry surface for this phase.

**If the planner later decides to introduce APScheduler** (against this research's recommendation), it MUST run the Package Legitimacy Gate first: `apscheduler` on PyPI is the legitimate package (`agronholm/apscheduler`), but it should still pass a `checkpoint:human-verify` mirroring the Phase-2 httpx / Phase-3 rapidfuzz precedent. This research recommends NOT introducing it.

## Architecture Patterns

### System Architecture Diagram

```
                         FastAPI app (single uvicorn worker, :8674)
                         ─ app.state.db = ONE sqlite3 WAL connection (the single writer)
                         ─ _detect_lock : threading.Lock  (serializes ALL ledger writes)
                                    │
   ┌────────────────────────────────┼──────────────────────────────────────────────┐
   │                                │                                               │
[startup]                   [scheduler daemon thread]                      [HTTP request handlers]
reconcile()                 every ACQ_POLL_SECONDS (6h):                   GET /status      → HTML (ledger read)
 ─ reset orphaned            if not ACQ_ENABLED: skip                      GET /status.json → JSON  (ledger read)
   downloading/importing     1. with _detect_lock:                        POST /detect     → manual pass (existing)
   rows (D-14)                   detect_gaps() in ONE txn (D-15)           GET /healthz /readyz (existing)
 ─ verify-by-requery         2. shares_ensure():
   guard (no double             GET /api/v0/application → shares.files
   import)                       if 0 → PUT /api/v0/shares (rescan)
                                 if still 0 → mark a "share" issue
                             3. with _detect_lock:
                                 eligible = SQL select
                                  (grace ✓ AND backoff ✓ AND
                                   NOT permanently-unavailable*  *unless 30d dormant)
                             4. for item in eligible[:room]:
                                 if get_queue_status(item) active → skip (D-02)
                                 submit acquire_item → ThreadPoolExecutor(max=MAX_CONCURRENT)
                                       │                          │              │
                                       ▼                          ▼              ▼
                              [worker 1]                  [worker 2]      [worker 3]   (download/IO ONLY)
                              acquire_item(item, conn?, …) ← see "writer marshaling" note
                                 search slskd ─ gate ─ download ─ import ─ verify ─ purge
                                 returns "imported"|"quarantined"|"stuck"|("infra-skip")
                                       │
                                       ▼
                             5. apply_result(item, outcome)  ← back on the writer (with _detect_lock):
                                 imported    → status=imported, attempt_count reset
                                 quarantined/stuck (genuine fail) → attempt_count++,
                                     next_attempt_at = now + backoff(attempt_count) [1h/6h/24h];
                                     attempt_count ≥ 3 → status=permanently-unavailable
                                 infra-skip  → NO attempt burned, leave for next cycle (REL-02)
```
**Writer marshaling note:** the load-bearing rule is that every `repo.set_status` / attempt / backoff write goes through the single `app.state.db` connection under `_detect_lock`. Two viable shapes (planner picks one — see Pattern 2):
- **(A) Workers do IO only, the writer thread does all status writes.** `acquire_item` is refactored so its status transitions are returned as outcomes/callbacks the scheduler applies, OR
- **(B) Each status write inside `acquire_item` grabs `_detect_lock` for the duration of that single `conn.execute`.** sqlite3 with `check_same_thread=False` (already set, `db.py:35`) permits cross-thread use of one connection IFF access is serialized — the lock provides that serialization.
Shape (B) is the smaller change (acquire_item already takes `conn`); shape (A) is cleaner but a bigger refactor. **Recommendation: (B)** — wrap the writer connection in a tiny lock-guarding proxy so `acquire_item` is unchanged and every `.execute` is serialized.

### Recommended Project Structure
```
app/
├── core/
│   ├── scheduler.py     # NEW: the daemon loop (run_cycle, eligibility select, dispatch pool, apply_result, backoff calc)
│   ├── reconcile.py     # NEW: startup reset of orphaned in-flight rows + verify-by-requery guard (D-14)
│   ├── shares.py        # NEW: ensure_shares() — read count, rescan, surface (D-10); pure-ish, takes the slskd client
│   ├── acquire.py       # EXISTING: unchanged loop, possibly wrapped writer connection (Pattern 2B)
│   └── gap_detector.py  # EXISTING: detect_gaps — wrap its upserts in one txn (D-15)
├── adapters/
│   ├── slskd.py         # EXISTING: ADD get_shared_file_count() + rescan_shares()
│   ├── lidarr.py        # EXISTING: ADD get_queue_status() (the D-02 race check)
│   └── readarr.py       # EXISTING: ADD get_queue_status() best-effort
├── state/
│   ├── migration_0003.sql  # NEW: + attempt_count, next_attempt_at, last_checked_at; status enum + 'permanently-unavailable'
│   ├── db.py            # EXISTING: register migration_0003 in MIGRATIONS list
│   └── repo.py          # EXISTING: ADD eligibility select + attempt/backoff mutators + status-counts query
├── config.py            # EXISTING: ADD the Phase-5 env tunables
├── main.py              # EXISTING: start/stop the scheduler thread in startup/shutdown; ADD GET /status + /status.json
└── status_page.py       # NEW (optional): the bare HTML renderer (html.escape over a ledger snapshot)
```

### Pattern 1: Stdlib daemon-thread scheduler with a stop-event (REL-01)
**What:** A single `threading.Thread(daemon=True)` started in FastAPI `startup` that loops `while not stop_event.wait(poll_seconds):` and runs one cycle. Stopped cleanly in `shutdown` by setting the event.
**When to use:** The whole phase's cadence (D-03 6h loop).
**Why a thread, not asyncio:** the loop calls synchronous blocking `acquire_item`; `stop_event.wait(timeout)` is an interruptible sleep (clean shutdown, no busy-wait). Mirrors the existing synchronous `poll_hook = time.sleep` seam in `acquire.py:242`.
```python
# Source: pattern derived from app/main.py (_detect_lock + on_event startup/shutdown) + stdlib threading
# app/core/scheduler.py
import threading, logging
log = logging.getLogger(__name__)

class Scheduler:
    def __init__(self, app, settings):
        self._app = app
        self._settings = settings
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="curator-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=30)

    def _run(self):
        # run a reconcile pass immediately on boot, then loop on the interval
        try:
            run_cycle(self._app, self._settings, first_pass=True)
        except Exception:
            log.exception("scheduler boot cycle failed (will retry next interval)")
        while not self._stop.wait(self._settings.acq_poll_seconds):
            if not self._settings.acq_enabled:        # D-05 kill-switch (re-read each cycle)
                log.info("ACQ_ENABLED is false — skipping cycle")
                continue
            try:
                run_cycle(self._app, self._settings)
            except Exception:
                log.exception("scheduler cycle failed (loop continues)")  # a cycle crash NEVER kills the daemon
```
**CRITICAL:** the loop body is wrapped in `try/except` so one bad cycle (e.g. a transient *arr 500 that escaped the per-item handling) never terminates the daemon (REL-01: "runs continuously"). The kill-switch and `MAX_CONCURRENT` are re-read from `settings` each cycle so the owner can change them without a restart (D-05 "easy to manage"). *(Note: Settings is a frozen singleton read from env at import; to make `ACQ_ENABLED` flippable without a restart, either re-read the specific env var each cycle or rebuild `Settings.from_env()` per cycle — planner decides; re-reading the single env var is simplest.)*

### Pattern 2: Single-writer preservation under bounded concurrency (D-04/D-16)
**What:** `MAX_CONCURRENT` workers run `acquire_item` in parallel, but a single `threading.Lock` (reuse `_detect_lock`, or a dedicated `_writer_lock`) serializes every ledger write so the one sqlite connection is never used concurrently.
**When to use:** Every cycle's acquisition dispatch.
**Example (Shape B — recommended, minimal change):**
```python
# Source: derived from app/main.py:20 (_detect_lock) + app/state/db.py:35 (check_same_thread=False)
# A lock-guarding proxy so acquire_item's existing `conn.execute(...)` calls are serialized for free.
class LockedConn:
    def __init__(self, conn, lock):
        self._conn, self._lock = conn, lock
    def execute(self, *a, **k):
        with self._lock:
            return self._conn.execute(*a, **k)
    # expose row factory / other read methods as needed; reads under WAL are concurrent-safe but
    # the SAME connection object is not — so route reads through the lock too (cheap at this volume).

# in run_cycle:
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=settings.max_concurrent) as pool:
    futures = {pool.submit(acquire_item, item, adapter, slskd, LockedConn(conn, writer_lock), settings): item
               for item in batch}
    for fut in as_completed(futures):
        item = futures[fut]
        outcome = fut.result()           # "imported" | "quarantined" | "stuck"
        apply_result(conn, writer_lock, item, outcome, settings)   # backoff/attempt write, also locked
```
**Anti-pattern:** giving each worker its OWN sqlite connection. That breaks the single-writer model (concurrent writers → `database is locked`, lost-update on attempt counters) — the explicitly forbidden design (D-16, BL-02, the `/detect` 409 guard rationale).

### Pattern 3: Backoff / attempt state machine (STATE-03, D-07/08/09)
**What:** `migration_0003` adds three columns; `apply_result` mutates them; the eligibility select reads them.
```sql
-- app/state/migration_0003.sql  (NO `;` inside string literals — the runner splits on top-level `;`)
-- (1) Add the backoff/attempt/dormant columns (idempotent-safe via the user_version gate, not IF NOT EXISTS:
--     ALTER TABLE ADD COLUMN is fine here because migration_0003 runs exactly once per user_version bump).
ALTER TABLE items ADD COLUMN attempt_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN next_attempt_at TEXT;          -- ISO8601 UTC; NULL = eligible now (no backoff pending)
ALTER TABLE items ADD COLUMN last_checked_at TEXT;          -- ISO8601 UTC; last time we attempted (drives 30d dormant)

-- (2) Widen the status CHECK enum to add 'permanently-unavailable' (table-rebuild, same technique as 0002).
ALTER TABLE items RENAME TO items_old;
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  arr_app TEXT NOT NULL, arr_id TEXT NOT NULL, kind TEXT NOT NULL, gap_type TEXT NOT NULL,
  title TEXT, artist_or_author TEXT, foreign_id TEXT, quality_profile_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','searching','grabbed','downloaded','imported','unavailable',
                      'blacklisted','downloading','importing','quarantined','stuck',
                      'permanently-unavailable')),
  discovered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, raw_json TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_checked_at TEXT,
  UNIQUE (arr_app, arr_id)
);
INSERT INTO items SELECT * FROM items_old;
DROP TABLE items_old;
CREATE INDEX IF NOT EXISTS idx_items_status   ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind ON items(arr_app, kind);
CREATE INDEX IF NOT EXISTS idx_items_next_attempt ON items(next_attempt_at);
```
**IMPORTANT ordering caveat:** the column-rebuild for the enum widen must include the three new columns in the rebuilt table, so do the rebuild that already has them (don't `ADD COLUMN` then rebuild without copying — `INSERT … SELECT *` aligns by position). The planner should put the rebuild **with all columns present** as a single coherent migration (either: rebuild-with-all-columns in one step, OR ADD COLUMNs first then rebuild copying them explicitly). **Recommendation: one rebuild that defines the full column set, with `INSERT INTO items (col list) SELECT col list FROM items_old` using an explicit column list** (safer than `SELECT *` once the shapes diverge mid-migration). Verify against a v0002 DB in a test (mirrors 02-02's "v1-only-then-full-migrate" test).

**Backoff calc (D-08, capped 1h→6h→24h):**
```python
# Source: D-08 (CONTEXT.md). Pure function — table-driven, capped.
BACKOFF_SECONDS = [3600, 21600, 86400]   # attempt 1→1h, 2→6h, 3+→24h (capped)
def backoff_for(attempt_count: int) -> int:
    idx = min(max(attempt_count, 1), len(BACKOFF_SECONDS)) - 1
    return BACKOFF_SECONDS[idx]
```
**apply_result logic (genuine failure path, D-07/08):** `attempt_count += 1`; `last_checked_at = now`; if `attempt_count >= settings.acq_max_attempts (3)` → `status = 'permanently-unavailable'`, `next_attempt_at = now + 30d` (the dormant re-check anchor, D-09); else `next_attempt_at = now + backoff_for(attempt_count)`.

### Pattern 4: Eligibility select (GAP-03 grace + D-08 backoff + D-09 dormant)
**What:** One SQL predicate selects the items the cycle may act on, BEFORE the per-item queue check (D-02, which is an *arr HTTP call and so is done per-item in Python, not in SQL).
```sql
-- Eligible = a pending/stuck/quarantined gap whose grace has elapsed and whose backoff has elapsed,
-- OR a permanently-unavailable item dormant for >= 30 days (the one-shot re-check, D-09).
-- (Times are ISO8601 UTC strings — lexicographic compare is valid for the Z-suffixed format used by repo._now_iso.)
SELECT * FROM items
WHERE
  (
    status IN ('pending','stuck','quarantined')               -- not in-flight, not imported, not permanent
    AND discovered_at <= :grace_cutoff                        -- GAP-03 grace (now - 3d) ≥ first-seen
    AND (next_attempt_at IS NULL OR next_attempt_at <= :now)  -- D-08 backoff elapsed
  )
  OR
  (
    status = 'permanently-unavailable'                        -- D-09 dormant re-check
    AND (last_checked_at IS NULL OR last_checked_at <= :dormant_cutoff)  -- now - 30d
  )
ORDER BY discovered_at ASC                                    -- oldest gaps first (fair drain of the backlog)
LIMIT :room;                                                  -- room = MAX_CONCURRENT (or a per-cycle cap)
```
**Notes:**
- `:grace_cutoff = now - grace_seconds`, `:dormant_cutoff = now - dormant_seconds`, all computed in Python as ISO8601 strings (reuse `repo._now_iso` style) and bound via `?` placeholders (T-02-03).
- Which statuses are "retry-eligible" is a planner call: at minimum `pending`; `stuck`/`quarantined` SHOULD also be retry-eligible (that is the whole point of backoff — a stuck item retries after its backoff), which is why they appear above. `imported`/`searching`/`downloading`/`importing` are NEVER eligible (satisfied or in-flight).
- The queue check (D-02) is intentionally NOT in this SQL — it requires an *arr API call per item, so it runs in Python right before dispatch (Pattern 5).

### Pattern 5: Infra-outage classification + the D-02 queue check (REL-02, GAP-03)
**What:** Before acting on an eligible item, ask the adapter "is there an active/queued Usenet grab?" (`get_queue_status`). When acting, distinguish "the world is unreachable" (infra) from "this specific item genuinely failed" so only the latter burns an attempt.
**The classifier:** an infra outage is detectable as a *connection/timeout-class* exception when talking to slskd/*arr/VPN, or a "shares unreachable / search-submit failed because slskd is down" condition — as opposed to a clean "searched, nothing passed the gate" (genuine: nothing on Soulseek) or "downloaded but import failed/verify-false" (genuine: bad files). Concretely:
```python
# Source: pattern over httpx exception taxonomy + the acquire_item outcome strings.
import httpx
INFRA_EXC = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)

def run_one(item, adapter, slskd, conn, settings):
    # D-02 race check FIRST — skip (don't burn an attempt) if a Usenet grab is active/queued.
    try:
        if adapter.get_queue_status(item):       # truthy = active/queued grab exists
            return "skip-usenet-active"          # not a failure; re-eligible next cycle
    except INFRA_EXC:
        return "infra-skip"                      # *arr unreachable → infra, no attempt burned (REL-02)
    try:
        return acquire_item(item, adapter, slskd, conn, settings)   # "imported"|"quarantined"|"stuck"
    except INFRA_EXC:
        return "infra-skip"                      # slskd/VPN dropped mid-flow → infra, no attempt burned
```
**Key distinction (REL-02):** `acquire_item` already returns `"stuck"` for *genuine* "nothing passed the gate" / "exhausted candidates", and `"quarantined"` for *genuine* "downloaded but didn't import" — those DO burn an attempt. An exception in the `INFRA_EXC` family (slskd/*arr/VPN unreachable) returns `infra-skip` and burns NOTHING. The planner must verify that `acquire_item`'s internal `_safe_call`/`raise_for_status` posture surfaces infra faults as catchable exceptions here rather than silently mapping a connection error to `"stuck"` — **OPEN QUESTION A1, see Assumptions Log** (currently `_safe_call` swallows a manifest/profile fetch failure to `None`→`stuck`, which would wrongly burn an attempt on an *arr outage; this needs a small adjustment so an infra fault on the decision-input fetch is distinguishable from a genuine not-found).

### Pattern 6: slskd shares ensure/self-heal (SHARE-01/02, D-10) — see slskd Shares API below.

### Pattern 7: Server-rendered status page (REL-03, D-12)
**What:** `GET /status` returns `HTMLResponse` built from a ledger snapshot; `GET /status.json` returns the same data as JSON (the Phase-6 widget contract). Both are pure reads (no lock contention concern, but route the read through the connection consistently).
```python
# Source: FastAPI HTMLResponse + stdlib html.escape (XSS defense — titles are peer/*arr-derived, untrusted)
from fastapi.responses import HTMLResponse, JSONResponse
from html import escape

@app.get("/status.json")
def status_json():
    conn = app.state.db
    return {
        "counts": repo.status_counts(conn),     # {imported: N, stuck: N, quarantined: N, permanently-unavailable: N, ...}
        "stuck":   [_row_view(r) for r in repo.list_by_status(conn, "stuck")],
        "quarantined": [_row_view(r) for r in repo.list_by_status(conn, "quarantined")],
        "permanently_unavailable": [_row_view(r) for r in repo.list_by_status(conn, "permanently-unavailable")],
        "shares_ok": app.state.shares_ok,        # last shares ensure result (bool)
        "throughput": repo.imported_recent(conn), # healthy-throughput signal (e.g. imported in last 24h)
    }

@app.get("/status", response_class=HTMLResponse)
def status_html():
    d = status_json()
    rows = "".join(
        f"<tr><td>{escape(i['app'])}:{escape(i['id'])}</td><td>{escape(i['title'] or '')}</td>"
        f"<td>{escape(i['reason'] or '')}</td></tr>"
        for i in d["stuck"] + d["quarantined"] + d["permanently_unavailable"]
    )
    return f"<html><body><h1>Curator status</h1>...<table>{rows}</table></body></html>"
```
**MANDATORY:** every interpolated ledger string passes through `html.escape()` — album/artist/quarantine-reason strings originate from untrusted *arr/Soulseek peer data (a malicious or odd filename could otherwise inject markup). This is the status-page analogue of the `?`-placeholder rule.

### Anti-Patterns to Avoid
- **Per-worker sqlite connection** → breaks single-writer (D-16). Workers do IO; one connection writes, serialized by the lock.
- **APScheduler `AsyncIOScheduler`** → forces an async rewrite of a proven synchronous loop. Use a plain daemon thread.
- **Letting a cycle exception kill the daemon** → wrap the loop body in `try/except` (REL-01 "runs continuously").
- **Burning an attempt on an infra outage** → classify `INFRA_EXC` separately (REL-02). A VPN flap must not push an item toward permanently-unavailable.
- **Rewriting slskd.yml to "fix" shares** → D-10 forbids it. Only read the count + `PUT /api/v0/shares` rescan + surface.
- **Unescaped HTML on the status page** → XSS/markup injection from peer/*arr titles. Always `html.escape`.
- **Resuming a partial slskd transfer on restart** → D-14 says reset-and-reattempt-from-scratch; don't try to resume bytes.

## slskd Shares API (the D-10 OPEN QUESTION — RESOLVED)

**Confirmed endpoints (slskd `/api/v0`, the same client/base as `app/adapters/slskd.py`):**

| Need | Method + Path | Response | Confidence |
|------|---------------|----------|------------|
| **(a) Read shared-file count** | `GET /api/v0/application` → `body["shares"]["files"]` (and `["shares"]["directories"]`) | Application-state object; `shares.files` is the integer shared-file count | HIGH `[VERIFIED: gethomepage/homepage slskd component reads `appData.shares?.files`]` |
| **(b) Trigger a rescan** | `PUT /api/v0/shares` | `204 No Content` on success; `409 Conflict` if a scan is already in progress | HIGH `[CITED: slskd SharesController.cs — PUT api/v0/shares "Initiates a scan of the configured shares"]` |
| Cancel a running scan (optional) | `DELETE /api/v0/shares` | `204` cancelled / `404` no scan running | MEDIUM `[CITED: SharesController.cs]` (not needed for D-10) |
| List shares (alternative count source) | `GET /api/v0/shares` | dict host→share collection (per-share dir/file counts) | MEDIUM `[CITED: SharesController.cs]` — `GET /application` is the preferred count source (it's what the maintained Homepage widget uses) |

**How to detect "count dropped to 0" (D-10/SHARE-02):**
```python
# app/adapters/slskd.py — ADD these two methods (same defensive .get() posture, same X-API-Key header).
def get_shared_file_count(self) -> int:
    """SHARE-02: read slskd's current shared-file count from the application state.
    GET /api/v0/application -> body['shares']['files'] (.get()-defensive: absent → 0)."""
    r = self._client.get(f"{self._base}/application", headers=self._headers, timeout=30.0)
    r.raise_for_status()
    body = r.json() if isinstance(r.json(), dict) else {}
    shares = body.get("shares") if isinstance(body.get("shares"), dict) else {}
    files = shares.get("files")
    return files if isinstance(files, int) else 0

def rescan_shares(self) -> bool:
    """SHARE-02 self-heal: PUT /api/v0/shares to initiate a scan. 204 → True (started);
    409 → False (a scan is already in progress — treat as 'already healing', not an error)."""
    r = self._client.put(f"{self._base}/shares", headers=self._headers, timeout=30.0)
    if r.status_code == 409:
        return False
    r.raise_for_status()
    return True
```
**Ensure/self-heal cycle (D-10, core/shares.py):** read count → if `> 0` mark `shares_ok=True` (done) → if `0`, call `rescan_shares()` and record that a rescan was triggered; on the NEXT cycle re-read the count → if still `0` after a rescan window, mark a **"share" issue** surfaced on `/status` (SHARE-02 "surface if it can't recover"). Do NOT block acquisition on shares (a zero-share state is a leech risk, not a hard stop) — but DO surface it prominently. The rescan is async in slskd (the `PUT` returns 204 immediately and the scan runs in the background), so the count check is **eventually-consistent across cycles**, not within one cycle.

**Provenance note:** the `shares.files` field path is `[VERIFIED]` against the *maintained, working* Homepage integration (`gethomepage/homepage` reads exactly `appData.shares?.files`), which is stronger evidence than any single doc page. The `PUT /api/v0/shares` rescan route is `[CITED]` from the slskd `SharesController.cs` source. The owner can confirm both live in one minute (the slskd Swagger UI is available with `SLSKD_SWAGGER=true`, or just `curl http://192.168.86.37:5030/api/v0/application -H "X-API-Key: …" | jq .shares`). **Recommend the planner add a tiny live-confirm checkpoint** (mirroring Phase-4's A1/A2/A3 live probes) to pin `shares.files` and the `PUT` status code before relying on them — autonomous:false, but offline code uses these confirmed shapes so nothing blocks.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Recurring interval timer | A `while True: time.sleep(6h)` busy-wait | `threading.Event().wait(timeout)` | Interruptible (clean shutdown), no busy spin; stdlib |
| Bounded parallelism | Manual thread list + join bookkeeping | `concurrent.futures.ThreadPoolExecutor(max_workers=N)` | The pool size IS the cap; `as_completed` gives results as they land |
| The single-item acquisition loop | Re-implement search→gate→download→import | **Existing `acquire_item`** (`app/core/acquire.py`) | Already proven + live-pinned (A1/A2/A3); Phase 5 only schedules it |
| slskd transfer/search/cancel | New HTTP calls | **Existing `SlskdClient`** | Already the pinned client; just add 2 shares methods |
| *arr ManualImport / verify | New import logic | **Existing `lidarr.py`/`readarr.py`** import methods | Done + live-pinned in Phase 4 |
| Cross-thread sqlite safety | A new DB engine / connection pool | The **single connection + `threading.Lock`** | The established invariant; sqlite is fine single-writer-serialized |
| HTML escaping | Manual `.replace("<","&lt;")` | `html.escape()` (stdlib) | Covers `& < > " '` correctly |
| ISO8601 UTC timestamps | New time format | **Existing `repo._now_iso()`** style | Z-suffixed, lexicographically comparable — the eligibility SQL relies on it |

**Key insight:** Phase 5 is almost entirely *composition + scheduling* over already-built, already-proven parts. The genuinely new code is small: a scheduler thread, a `migration_0003`, an eligibility/backoff repo surface, two slskd shares methods, a `get_queue_status` adapter method, a reconcile pass, and a status page. Resist re-building anything from Phases 2–4.

## Runtime State Inventory

> This is an autonomy/sharing phase, not a rename/refactor. No string-rename runtime state to migrate. The relevant "runtime state" is the **existing ledger contents** the new schema/loop must respect:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | The **~1,493-gap ledger** already on the NAS (`/db/curator.sqlite`), all with original `discovered_at` (pre-grace at launch, D-01) and `status` in {pending, possibly some imported/quarantined/stuck from the Phase-4 live test} | `migration_0003` must preserve every existing row through the table-rebuild (explicit column-list `INSERT … SELECT`, mirroring 02-02's preservation test). New columns default `attempt_count=0`, `next_attempt_at=NULL`, `last_checked_at=NULL` → all existing gaps are immediately backoff-eligible (correct). |
| Live service config | slskd shares (music+books read-only) configured live 2026-05-31 — in slskd.yml, NOT owned by Curator (D-10/D-11). One album (ZHU – BLACK MIDAS) already imported live via the Phase-4 test → that row may be `imported` in the ledger | Curator only *verifies* shares (`shares.files > 0`), never rewrites slskd.yml. Reconcile must not re-act on the already-imported album (verify-by-requery guard, D-14). |
| OS-registered state | None — the daemon is in-process (a thread inside the FastAPI container), no Task Scheduler / systemd / pm2 registration | None — verified by design (in-app scheduler, Pattern 1). |
| Secrets/env vars | `SLSKD_API_KEY`, `LIDARR_API_KEY`, `READARR_API_KEY` already in `.env` (Phase 1). New Phase-5 vars are non-secret tunables (`ACQ_ENABLED`, `MAX_CONCURRENT`, grace/backoff/poll seconds) | Add the new tunables to `.env.example` + DEPLOY.md; no new secret enters the stack (no Plex token, no Pushover token — D-13 defers push). |
| Build artifacts | None new — same Docker image, same `requirements.txt` (no new dep) | A rebuild/redeploy is still needed to ship the new code + run `migration_0003` on the NAS DB. Flag in DEPLOY.md (proactive-NAS-setup memory). |

## Common Pitfalls

### Pitfall 1: The backlog floods on first enable
**What goes wrong:** The ~1,493 gaps are ALL past grace at launch (D-01). If the cycle dispatched all eligible items, it would start ~1,493 parallel Soulseek downloads — disk pressure, leech/rate risk, unobservable.
**Why it happens:** Grace is per-item politeness, not flood control (D-01 is explicit). The only flood control is `MAX_CONCURRENT` + the per-cycle `LIMIT`.
**How to avoid:** The eligibility select `LIMIT`s to `room` (≤ `MAX_CONCURRENT`, or a slightly larger per-cycle cap), and the `ThreadPoolExecutor(max_workers=MAX_CONCURRENT)` hard-caps concurrency. Staged rollout (D-05: dry-run → cap=1 → cap=3) means the first real pass moves exactly ONE item. **Warning sign:** more than `MAX_CONCURRENT` active slskd transfers.

### Pitfall 2: An infra outage burns attempts → items wrongly go permanently-unavailable
**What goes wrong:** A VPN flap or slskd restart during a cycle makes every in-flight download fail; if each failure increments `attempt_count`, three bad cycles push real, available albums to `permanently-unavailable`.
**Why it happens:** Conflating "the world is unreachable" with "this item genuinely failed" (REL-02).
**How to avoid:** Classify `INFRA_EXC` (connection/timeout-class httpx exceptions) as `infra-skip` → NO attempt burned, item stays eligible (Pattern 5). **Open Question A1:** `acquire.py`'s `_safe_call` currently swallows a manifest/profile fetch failure to `None`→`stuck`; on an *arr outage this would mark `stuck` (and if `stuck` burns an attempt, that's wrong). The planner must ensure an infra fault on any adapter/slskd call is distinguishable from a genuine "not found / nothing on Soulseek." **Warning sign:** a burst of `permanently-unavailable` transitions clustered in time (a tell-tale outage signature, not real unavailability).

### Pitfall 3: Double-import on restart
**What goes wrong:** Container restarts mid-import; on boot the item is `importing`; reconcile re-attempts; the *arr already imported it → a duplicate grab / orphaned staging.
**Why it happens:** Treating "left in `importing`" as "needs re-import" without checking reality.
**How to avoid:** Reconcile resets orphaned `downloading`/`importing` rows but FIRST runs the **verify-by-requery guard** (`adapter.verify_imported(item)` — already built, `lidarr.py:330`): if the item LEFT the wanted list, it imported → set `imported`, don't re-attempt; only if still wanted → reset to a retry-eligible status (D-14). **Warning sign:** the same album imported twice, or staging dirs reappearing for imported items.

### Pitfall 4: Two writers corrupt the single sqlite connection
**What goes wrong:** A worker thread and the scheduler (or the manual `/detect`) write the one connection concurrently → `database is locked`, lost attempt-counter updates, or a corrupted statement.
**Why it happens:** Forgetting that `check_same_thread=False` permits cross-thread use only if serialized.
**How to avoid:** Every ledger write goes through the lock (Pattern 2). Reuse the existing `_detect_lock` (so a manual `/detect` and the scheduler also can't collide) or a dedicated writer lock that BOTH the scheduler and `/detect` acquire. **Warning sign:** intermittent `sqlite3.OperationalError: database is locked` or `ProgrammingError: recursive use of cursors`.

### Pitfall 5: The daemon dies on an unhandled cycle exception
**What goes wrong:** A transient *arr 500 escapes per-item handling, the cycle raises, the thread exits, and Curator silently stops filling gaps forever (the opposite of REL-01).
**How to avoid:** Wrap the loop body in `try/except` that logs and continues (Pattern 1). The per-item work is ALSO wrapped (a bad item never kills the cycle). **Warning sign:** `/status` throughput goes to zero and stays there; the scheduler thread is no longer alive.

### Pitfall 6: slskd rescan is async — checking the count in the same cycle reads stale 0
**What goes wrong:** `PUT /api/v0/shares` returns 204 immediately but the scan runs in the background; re-reading `shares.files` in the same cycle still shows 0 → Curator falsely concludes the rescan failed and surfaces a spurious issue.
**How to avoid:** Treat shares self-heal as eventually-consistent ACROSS cycles: rescan this cycle, re-check NEXT cycle; only surface a "share" issue if the count is still 0 a cycle (or N) after a rescan. **Warning sign:** a "shares broken" issue that clears itself a few hours later.

### Pitfall 7: The firewall regresses in new core modules
**What goes wrong:** `scheduler.py`/`reconcile.py`/`shares.py` accidentally read an *arr/slskd wire key (e.g. peeking at a queue JSON field, or `shares.files` in core instead of the adapter).
**How to avoid:** The D-02 queue read lives in `lidarr.py`/`readarr.py` (`get_queue_status` returns a neutral bool/shape); the `shares.files`/rescan reads live in `slskd.py`. The new core modules speak only neutral types + the repo. Extend the existing firewall grep test to cover the new core files (the established `ARR_FIELD_NAMES` grep). **Warning sign:** the firewall grep test fails on a new core module.

## Code Examples

### Wiring the scheduler into the FastAPI lifecycle (main.py)
```python
# Source: app/main.py existing on_event startup/shutdown pattern (lines 23-43)
from core.scheduler import Scheduler
from core.reconcile import reconcile_on_startup

@app.on_event("startup")
def _startup():
    conn = connect(settings.db_path)
    run_migrations(conn)                 # runs migration_0003
    app.state.db = conn
    app.state.shares_ok = True
    reconcile_on_startup(conn, _writer_lock, build_adapters, settings)  # D-14 (reset orphans, verify-guard)
    app.state.scheduler = Scheduler(app, settings)
    app.state.scheduler.start()          # REL-01 daemon

@app.on_event("shutdown")
def _shutdown():
    sched = getattr(app.state, "scheduler", None)
    if sched: sched.stop()
    conn = getattr(app.state, "db", None)
    if conn: conn.close()
```

### Reconcile pass (reconcile.py, D-14 — no double-import)
```python
# Source: composes existing adapter.verify_imported (lidarr.py:330) + repo.set_status + repo.list_by_status
def reconcile_on_startup(conn, lock, build_adapters, settings):
    adapters, clients = build_adapters()
    try:
        by_app = {a.app: a for a in adapters}
        for status in ("downloading", "importing"):
            for row in repo.list_by_status(conn, status):
                adapter = by_app.get(row["arr_app"])
                if adapter is None:
                    continue
                item = _gapitem_from_row(row)
                try:
                    imported = adapter.verify_imported(item)   # did it actually land while we were down?
                except INFRA_EXC:
                    continue                                   # *arr down → leave as-is, retry next boot (no burn)
                with lock:
                    if imported:
                        repo.set_status(conn, item.arr_app, item.arr_id, "imported")  # don't re-import
                    else:
                        repo.set_status(conn, item.arr_app, item.arr_id, "pending")   # reset → re-attempt clean
                        # NOTE: do NOT increment attempt_count here — the interruption was infra, not a genuine fail
    finally:
        for c in clients: c.close()
```

### The D-02 queue check adapter method (lidarr.py)
```python
# Source: Servarr v1 GET /api/v1/queue (the *arr download queue). *arr keys stay in the adapter (firewall).
def get_queue_status(self, item: "GapItem") -> bool:
    """GAP-03/D-02: True iff an active or queued Usenet grab exists for this item (skip — Usenet wins).
    Reads the *arr queue and matches on the item's albumId/bookId. Lidarr primary → raise surfaces a fault
    (the scheduler classifies that raise as infra-skip, NOT a burned attempt)."""
    r = self._client.get(f"{self._base}/api/v1/queue", headers=self._headers,
                         params={"page": 1, "pageSize": 100}, timeout=30.0)
    r.raise_for_status()
    records = (r.json() or {}).get("records", []) if isinstance(r.json(), dict) else []
    aid = item.arr_id
    return any(str(rec.get("albumId")) == aid for rec in records if isinstance(rec, dict))
    # NOTE: confirm the queue record's album-id field name live (`albumId` for Lidarr) — see Assumptions A2.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| FastAPI `@app.on_event("startup")` | `lifespan=` async context manager | FastAPI 0.93+ | `on_event` is deprecated but STILL WORKS in 0.115.6 (the existing code uses it). Planner may migrate to `lifespan` for cleanliness, but it's optional — keep consistent with `main.py`. `[CITED: fastapi.tiangolo.com]` |
| APScheduler 3.x sync schedulers | APScheduler 4.x (data-store backed) | 4.x (2024+) | Irrelevant — this research recommends NO APScheduler. Noted only so the planner doesn't reach for a half-migrated 4.x. |

**Deprecated/outdated:** none affecting this phase (stdlib threading/concurrent.futures are stable; the existing pinned fastapi/httpx are current).

## Validation Architecture

> `workflow.nyquist_validation` is `true` in `.planning/config.json` → this section is REQUIRED.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (existing; `app/tests/`, 205 tests passing as of Phase 4) |
| Config file | none dedicated — tests run from `app/` (conftest.py present); offline-only (Python 3.9 sandbox + 3.12 CI/NAS) `[VERIFIED: app/tests/conftest.py exists]` |
| Quick run command | `cd app && python -m pytest tests/test_scheduler.py -x` |
| Full suite command | `cd app && python -m pytest` |

**Offline-provability rule (inherited):** the dev sandbox is Python 3.9 + no network. Every test must run with fakes (a `FakeSlskd`, `FakeAdapter`, an injected clock, an in-memory/temp sqlite) — NO live slskd/*arr/VPN. Mirror Phase 4's fake-clock + `httpx.MockTransport` pattern. The scheduler's interval sleep must be driven by an injectable clock/stop-event so tests don't actually wait 6h.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| GAP-03 | Grace-elapsed item eligible; fresh item not; Usenet-queued item skipped (no attempt burned) | unit | `pytest tests/test_eligibility.py -x` | ❌ Wave 0 |
| STATE-03 | 3 genuine fails → permanently-unavailable; backoff 1h→6h→24h sets `next_attempt_at`; 30d dormant re-eligible | unit | `pytest tests/test_backoff.py -x` | ❌ Wave 0 |
| SHARE-01/02 | count>0 → ok; count==0 → rescan PUT issued; still 0 next cycle → issue surfaced | unit | `pytest tests/test_shares.py -x` | ❌ Wave 0 |
| REL-01 | Daemon loops on interval; kill-switch skips a cycle; a cycle exception does NOT kill the thread | unit | `pytest tests/test_scheduler.py -x` | ❌ Wave 0 |
| REL-02 | Orphaned `importing` row + import-actually-completed → `imported` (no re-import); still-wanted → reset clean (no attempt burned); INFRA_EXC mid-acquire → infra-skip (no burn) | unit | `pytest tests/test_reconcile.py tests/test_infra_classify.py -x` | ❌ Wave 0 |
| REL-03 | `/status.json` returns the stuck/quarantined/permanently-unavailable lists + counts; `/status` HTML escapes titles | unit | `pytest tests/test_status_page.py -x` | ❌ Wave 0 |
| D-04/D-16 | ≤ MAX_CONCURRENT workers run at once; all ledger writes serialized (no `database is locked`) | unit | `pytest tests/test_concurrency.py -x` | ❌ Wave 0 |
| D-15 | A detection pass commits in ONE transaction; dedup + status-never-clobbered + discovered_at preserved still hold | unit | `pytest tests/test_detect_batch.py -x` | ❌ Wave 0 |
| migration_0003 | A v0002 DB migrates to v0003 with all rows preserved + new columns defaulted; enum accepts `permanently-unavailable` | unit | `pytest tests/test_migration_0003.py -x` | ❌ Wave 0 |
| REL-01/D-06 | **LIVE** acceptance: first capped daemon pass at MAX_CONCURRENT=1 imports one album end-to-end (owner-observed) | manual-only | (on-NAS, owner watches) | n/a — live |

### Sampling Rate
- **Per task commit:** the task's targeted test file (`pytest tests/test_<area>.py -x`)
- **Per wave merge:** full offline suite (`cd app && python -m pytest`) — must stay green (≥ 205 + new)
- **Phase gate:** full suite green + the firewall grep clean over the new core modules, BEFORE `/gsd:verify-work`. The D-06 live pass (MAX_CONCURRENT=1, owner-observed) is the final REL acceptance — it is manual/live and gated behind the owner's staged rollout, not the offline suite.

### Wave 0 Gaps
- [ ] `tests/test_migration_0003.py` — v0002→v0003 row-preservation + enum-accepts-permanently-unavailable (mirror 02-02 migration test)
- [ ] `tests/test_eligibility.py` — the grace/backoff/dormant SQL predicate (GAP-03, STATE-03 read side)
- [ ] `tests/test_backoff.py` — `backoff_for` + `apply_result` state transitions (STATE-03 write side)
- [ ] `tests/test_shares.py` — `get_shared_file_count`/`rescan_shares` + ensure cycle (SHARE-01/02); fake slskd via MockTransport
- [ ] `tests/test_scheduler.py` — loop/kill-switch/exception-resilience with an injected stop-event + clock (REL-01)
- [ ] `tests/test_reconcile.py` + `tests/test_infra_classify.py` — D-14 reconcile + INFRA_EXC classification (REL-02)
- [ ] `tests/test_status_page.py` — `/status.json` shape + `/status` HTML escaping (REL-03)
- [ ] `tests/test_concurrency.py` — ThreadPoolExecutor cap + writer-lock serialization (D-04/D-16)
- [ ] `tests/test_detect_batch.py` — one-transaction detection still preserves dedup/status/discovered_at (D-15)
- [ ] Fixtures: a `FakeSlskd` (application-state with `shares.files`, search/transfer), a queue fixture for `get_queue_status`, a clock/stop-event harness
- [ ] Extend the existing firewall grep test to include `core/scheduler.py`, `core/reconcile.py`, `core/shares.py`

## Security Domain

> `security_enforcement` is not set to `false` in config.json → included. This is an internal homelab daemon (LAN/Tailscale-only firewall rule per memory), but the new HTTP surface + peer-derived data still carry real risks.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | `/status` is read-only on a LAN/Tailscale-only port (existing firewall rule); no auth added (matches `/healthz`,`/detect`). The Phase-6 Homepage widget consumes `/status.json` via the same trusted-LAN posture. |
| V3 Session Management | no | Stateless read endpoints; no sessions. |
| V4 Access Control | partial | Network-layer only (LAN/Tailscale firewall). No new public surface — keep `/status` off any internet-exposed port (it leaks library gaps + titles). |
| V5 Input Validation | **yes** | `html.escape()` on every ledger string rendered into `/status` HTML (peer/*arr titles are untrusted — HTML-injection/XSS). `?`-placeholders for the new eligibility/backoff SQL (T-02-03, never f-string a value). |
| V6 Cryptography | no | No new secrets. `SLSKD_API_KEY` stays in `self._headers` only (never logged — existing T-04-07 posture extends to the new shares calls). D-13 defers Pushover, so no notification token enters the stack. |

### Known Threat Patterns for this phase

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| HTML/markup injection via a malicious album/peer filename rendered on `/status` | Tampering | `html.escape()` on every interpolated string (MANDATORY, Pattern 7) |
| SQL injection via a peer-derived string reaching the new eligibility/backoff SQL | Tampering | `?` placeholders only; the only permitted f-string-into-SQL stays the loop-controlled `user_version` (db.py) |
| API-key leak in a log line / exception when the new shares or queue calls fail | Info disclosure | Key lives only in `self._headers`; never echo the header into a log/exception (extend T-04-07 to the new methods) |
| DoS: the backlog flood (~1,493 parallel downloads) on enable | DoS (self-inflicted) | `MAX_CONCURRENT` pool cap + per-cycle `LIMIT` + staged rollout (Pitfall 1) |
| DoS: a malformed *arr/slskd response crashing the daemon thread | DoS | `.get()`-defensive reads (existing posture) + the cycle-level `try/except` (Pattern 1, 5) |
| Info disclosure: `/status` exposing library gaps to the LAN | Info disclosure | Keep `/status` on the existing LAN/Tailscale-only firewalled port (no new exposure) |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `acquire.py`'s current `_safe_call`/`raise_for_status` posture can be adapted so an INFRA-class fault (slskd/*arr/VPN unreachable) is distinguishable from a genuine "not found / nothing on Soulseek," so infra burns no attempt | Pattern 5, Pitfall 2 | If infra faults are indistinguishable from genuine `stuck`, a VPN flap could push available albums to `permanently-unavailable` (REL-02 violation). MEDIUM — requires a small, contained `acquire_item` adjustment the planner must scope. `[ASSUMED]` |
| A2 | The Lidarr/Readarr download-queue record's matching field for D-02 is `albumId` (Lidarr) / `bookId` (Readarr) and a record's presence = "active/queued grab" | Code Examples (get_queue_status) | Wrong field → the race check never matches → Curator could race Usenet on an in-flight grab (GAP-03 weakened). LOW — confirm live on the NAS queue JSON (a one-line `curl`). `[ASSUMED]` |
| A3 | `GET /api/v0/application` → `shares.files` is an integer shared-file count, and `PUT /api/v0/shares` returns 204/409 | slskd Shares API | The count path is `[VERIFIED]` via the maintained Homepage widget (`appData.shares?.files`); the rescan route is `[CITED]` from slskd source. Residual risk: the application-state shape could differ by slskd version. LOW — owner confirms in one `curl`/Swagger check (recommended live checkpoint). |
| A4 | A frozen `Settings` re-read of `ACQ_ENABLED`/`MAX_CONCURRENT` per cycle (vs a restart) is acceptable to the owner for "easy to manage" (D-05) | Pattern 1 | If the owner expects env changes to need a container restart anyway, the per-cycle re-read is unnecessary complexity. LOW — planner confirms; both work. `[ASSUMED]` |

## Open Questions

1. **Infra-vs-genuine failure boundary inside `acquire_item` (A1).**
   - What we know: `acquire_item` returns `"imported"|"quarantined"|"stuck"`; its `_safe_call` swallows a decision-input fetch failure to `None`→`stuck`; slskd client calls `raise_for_status` (so an slskd outage raises).
   - What's unclear: whether the planner refactors `acquire_item` to surface infra faults as a distinct outcome, OR wraps the call site (Pattern 5) and accepts that a `_safe_call`-swallowed *arr outage becomes a wrongful `stuck`+attempt.
   - Recommendation: wrap at the call site for slskd/VPN faults (the common outage), AND make `_safe_call` re-raise (or tag) connection-class exceptions so an *arr decision-input outage is also classifiable. Scope this as one small task with a dedicated `test_infra_classify.py`.

2. **Which non-terminal statuses are retry-eligible.**
   - What we know: `pending` is eligible; `imported`/in-flight are not.
   - What's unclear: whether `stuck`/`quarantined` re-enter the eligible pool on backoff (this research assumes yes — that IS the backoff mechanism) vs. requiring a manual nudge.
   - Recommendation: make `stuck`+`quarantined` retry-eligible (subject to backoff + the 3-attempt cap), since otherwise STATE-03's backoff has nothing to act on. Confirm with the owner at plan time (it's the difference between "auto-retries failures" and "surfaces failures and stops").

3. **Per-cycle item cap vs. pure MAX_CONCURRENT.**
   - What we know: `MAX_CONCURRENT` caps simultaneous downloads.
   - What's unclear: whether a cycle should also cap the TOTAL items attempted per pass (e.g. process 10 then wait for next cycle) or drain as many as the pool allows until the eligible set is empty.
   - Recommendation: for the bounded rollout, `LIMIT` the eligibility select to a modest per-cycle cap (e.g. `MAX_CONCURRENT * k`) so a 6h cycle doesn't run for hours draining 1,493 gaps in one pass; planner picks `k`.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 stdlib (threading, concurrent.futures, sqlite3, html) | Scheduler, pool, ledger, status page | ✓ | 3.12 (`python:3.12-slim`) | — |
| `fastapi==0.115.6` | scheduler lifecycle + `/status` | ✓ | pinned | — |
| `httpx==0.28.1` | slskd shares + *arr queue calls | ✓ | pinned | — |
| slskd `/api/v0` (live) | SHARE-01/02 ensure/self-heal; download loop | ✓ (NAS, via gluetun-published port) | live | offline tests use a FakeSlskd (no live needed to BUILD) |
| Lidarr/Readarr `/api/v1` (live) | D-02 queue check; verify-by-requery | ✓ (synobridge) | live | offline tests use FakeAdapter |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** the live slskd/*arr services are only needed for the on-NAS D-06 acceptance test; the entire phase BUILDS and is fully unit-tested offline with fakes (Python 3.9 sandbox).

## Project Constraints (from CLAUDE.md)

- **Platform:** Synology DS423+, `linux/amd64` only, Docker via Container Manager. The daemon is in-process (no new container/process).
- **Networking:** slskd is reached ONLY via gluetun's published port (`settings.slskd_url`, NAS-IP:5030) — NEVER a container name (slskd is `network_mode: service:gluetun`, off synobridge). The new shares calls use the SAME `self._base`/`self._headers` as the existing `SlskdClient` (Pitfall 7 already handled). Lidarr/Readarr reached by container name on synobridge.
- **Privacy:** All Soulseek traffic stays inside gluetun+PIA (kill-switch on). A VPN drop = slskd unreachable = an INFRA_EXC (no burned attempt) — the daemon must NOT attempt to "work around" a dropped VPN (it just skips the cycle's downloads and re-checks).
- **Persistence:** `/db/curator.sqlite` single-writer, `synchronous=FULL` preserved (D-15 keeps it). `migration_0003` runs on the existing NAS DB at next deploy — must preserve all ~1,493 rows.
- **Quality:** Unchanged — Phase 5 schedules the Phase-4 loop which already gates BEFORE download.
- **Behavior:** Strictly fallback-only — the D-02 queue check + grace enforce "Usenet always wins first." Fully hands-off (no approval queues) — the staged rollout is env-flag-only.
- **Sharing:** Mandatory (leech-block avoidance) — SHARE-01/02 ensure it stays active (D-10).
- **Observability:** `/status` HTML + `/status.json` (the Phase-6 Homepage `customapi` contract). Apprise/Pushover is Phase 6 (D-13) — DO NOT add here.
- **Deploy:** Single compose, CI-built image. No new dependency → no Dockerfile/requirements change beyond new code. **Proactively flag in DEPLOY.md:** the new env tunables (`ACQ_ENABLED`, `MAX_CONCURRENT`, grace/backoff/poll seconds) + the fact that a redeploy runs `migration_0003` on the live DB (per the proactively-flag-NAS-setup memory).

## Sources

### Primary (HIGH confidence)
- `app/core/acquire.py`, `app/core/gap_detector.py`, `app/state/repo.py`, `app/state/db.py`, `app/state/migration_0002.sql`, `app/state/schema.sql`, `app/adapters/base.py`, `app/adapters/slskd.py`, `app/adapters/lidarr.py`, `app/config.py`, `app/main.py` — the existing codebase (the authoritative constraint surface)
- `.planning/phases/phase-5/05-CONTEXT.md` (D-01..D-16), `.planning/phases/phase-5/RESEARCH-SEED.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md`, `.planning/phases/phase-4/04-05-LIVE-PROBE.md`
- slskd `SharesController.cs` (slskd/slskd master) — `GET/PUT/DELETE /api/v0/shares` routes
- `gethomepage/homepage` slskd widget `component.jsx` — confirms `GET /api/v0/application` → `shares.files` shared-file count (a maintained, working integration)

### Secondary (MEDIUM confidence)
- slskd-python-api docs (readthedocs) — Shares API method surface (`get_all`, `start_scan`, `cancel_scan`, `all_contents`)
- FastAPI docs (background tasks, lifespan/on_event) — scheduler-in-app patterns
- Python docs (`concurrent.futures`, `asyncio` event loop) — thread-pool vs. run_in_executor tradeoffs
- APScheduler discussions (multi-worker double-fire) — basis for the "plain thread" recommendation

### Tertiary (LOW confidence — flagged for live confirm)
- Exact `application`-state JSON shape across slskd versions (A3) — recommend a one-line `curl`/Swagger confirm on the NAS
- The *arr queue record's album-id field name for D-02 (A2) — confirm against live queue JSON

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — zero new deps; everything is stdlib + already-pinned packages verified against `requirements.txt`/`Dockerfile`.
- Architecture (scheduler/concurrency/single-writer): HIGH — derived directly from the codebase's proven invariants (`_detect_lock`, `check_same_thread=False`, synchronous httpx), not from speculation.
- slskd shares API: HIGH for the count path (maintained Homepage widget reads `shares.files`) and the rescan route (slskd source); MEDIUM on the exact application-state JSON across versions → one live confirm recommended.
- Backoff/eligibility/migration: HIGH — pure local SQL mirroring the established migration + repo patterns.
- Pitfalls: HIGH — each is grounded in an existing decision/invariant (single-writer, firewall, fallback-only).

**Research date:** 2026-05-31
**Valid until:** ~2026-06-30 for the slskd API shapes (slskd is fast-moving — re-confirm `shares.files` if slskd is upgraded on the NAS); the architecture/stdlib guidance is stable indefinitely.
