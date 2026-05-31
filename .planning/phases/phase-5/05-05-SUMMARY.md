---
phase: 05-autonomy-sharing-self-recovery
plan: 05
subsystem: autonomy-status-lifecycle
tags: [REL-01, REL-02, REL-03, SHARE-02, GAP-03, status-page, scheduler-lifecycle, xss]
requires:
  - "core/scheduler.py Scheduler(app, settings, lock) (05-04)"
  - "core/reconcile.py reconcile_on_startup (05-03)"
  - "core/shares.py ensure_shares + app.state.shares_ok (05-03)"
  - "state/repo.py status_counts / list_by_status / imported_recent (05-01)"
  - "adapters get_queue_status + slskd shares methods (05-02)"
provides:
  - "GET /status (escaped HTML) + GET /status.json (Phase-6 widget contract incl shares_ok)"
  - "core/status_page.py render_status_html(snapshot) — pure, firewall-clean, html.escape"
  - "FastAPI lifecycle: reconcile_on_startup -> Scheduler.start on boot; clean stop on shutdown"
  - "DEPLOY.md Step 9 + .env.example Phase-5 tunables + staged rollout + kill-switch"
affects:
  - "app/main.py (startup/shutdown now run reconcile + daemon; +status routes)"
tech-stack:
  added: []   # zero new dependencies — stdlib html.escape + fastapi HTMLResponse only
  patterns:
    - "Pure ledger-snapshot -> HTML transform (offline-unit-testable, no app/connection/network)"
    - "html.escape on EVERY interpolated ledger string (XSS chokepoint, T-05-20)"
    - "Defensive boot: reconcile build wrapped so a boot infra/config fault never blocks app startup"
key-files:
  created:
    - "app/core/status_page.py"
    - "app/tests/test_status_page.py"
    - ".planning/phases/phase-5/05-05-LIVE-PROBE.md"
  modified:
    - "app/main.py"
    - "DEPLOY.md"
    - ".env.example"
decisions:
  - "Status page rendered via stdlib html.escape + f-string (no template engine) — RESEARCH Pattern 7"
  - "_row_view exposes only neutral fields (app/id/title/reason=status) — no raw *arr record"
  - "Boot reconcile wrapped in try/except so a missing-key/unreachable-*arr boot still brings the app up"
  - "A2/A3 live probe left as a PENDING owner NAS action (no NAS access this session) — mirrors 04-05"
metrics:
  duration_min: 18
  completed: "2026-05-31"
  tasks_completed: 4
  files_touched: 6
  tests_added: 5
  suite: "277 passed, 0 failed (exit 0)"
---

# Phase 5 Plan 05: Status Surface, Scheduler Lifecycle & Live-Probe Closeout Summary

Wired the Phase-5 daemon + crash-reconcile into the FastAPI lifecycle and shipped the REL-03 status
surface (escaped `GET /status` HTML + `GET /status.json`), closing Phase 5 — with the A2/A3 live-NAS
probe scaffolded and left as a PENDING owner action (no NAS access this session).

## What was built

**Task 1 — REL-03 status surface (`ed6d354`).**
- `app/core/status_page.py`: a PURE, firewall-clean `render_status_html(snapshot: dict) -> str`. It
  speaks only the neutral status snapshot dict (counts + the three issue buckets + shares_ok +
  throughput) and stdlib `html.escape`. EVERY interpolated ledger string (title, reason, app, id)
  routes through `html.escape` via a single `_e()` chokepoint (T-05-20 XSS/HTML-injection defense).
  Being pure makes it offline-unit-testable on a hand-built dict — no app, no connection, no network.
- `app/main.py`: `GET /status.json` (counts + `stuck`/`quarantined`/`permanently_unavailable` buckets
  + `shares_ok` + 24h `throughput`) and `GET /status` (HTMLResponse delegating to render_status_html).
  A neutral `_row_view(row)` maps a sqlite3.Row to `{app, id, title, reason}` — never a raw *arr key.
  Both routes are pure reads on `app.state.db` with a 503 guard mirroring `/detect`. Imports are lazy
  inside the handlers (offline-parse-safe). The route docstrings document the LAN/Tailscale-only
  posture (T-05-21 info-disclosure: the page leaks library gaps, so no new exposure, no auth).
- `app/tests/test_status_page.py` (Task 1 block): json buckets/counts/shares_ok/throughput; html
  title listing; the XSS proof (raw `<img onerror>`/`<script>` ABSENT, `&lt;...&gt;` present);
  render_status_html unit-tested directly on a hand-built snapshot.

**Task 2 — scheduler + reconcile lifecycle wiring (`97bf576`).**
- `app/main.py` `_startup`: `run_migrations` (now applies migration_0003) → `app.state.db = conn` →
  `app.state.shares_ok = True` → `reconcile_on_startup(conn, _detect_lock, build_adapters, settings)`
  (D-14 orphan reset + verify-by-requery guard) → `Scheduler(app, settings, _detect_lock).start()`
  (REL-01). The scheduler shares the existing `_detect_lock` (D-16 — no second lock/connection).
- `_shutdown`: `scheduler.stop()` BEFORE `conn.close()` (clean drain; no worker on a closed conn).
- A DISTINCT `test_scheduler_lifecycle` function (separate from Task 1's route/XSS tests): TestClient
  startup builds `app.state.scheduler` (thread alive) + `shares_ok`, `/readyz` + `/status.json` still
  answer, shutdown stops cleanly. `ACQ_ENABLED=false` + an offline `build_adapters` keep it
  WIRING-only — no live acquisition cycle fires.

**Task 3 — A2/A3 live-probe checkpoint scaffold (`1b9e4a0`).** See "Pending owner actions" below.

**Task 4 — deploy docs (`0d0c867`).**
- `DEPLOY.md` Step 9: all 7 `ACQ_*`/`MAX_CONCURRENT` tunables (defaults + meanings), the migration_0003
  redeploy note + DB-backup precaution + a `user_version`/row-count verify, the staged rollout
  (dry-run → cap=1 D-06 acceptance test → cap=3), the `ACQ_ENABLED` kill-switch, and the D-11 note
  that shares stay owner-configured (Curator verifies/rescans, never rewrites slskd.yml).
- `.env.example`: the 7 tunables with defaults + one-line comments. No Pushover/Apprise token (D-13).

## Verification

- **Full suite: `277 passed, 0 failed (exit 0)`** (baseline 272 + 5 new: 4 status-route/XSS/render
  tests + 1 lifecycle test). No regressions. (The pytest summary count line is sometimes truncated by
  the harness; confirmed via `--co` = 277 collected and an all-dots run with exit 0.)
- **Firewall grep clean** (`tests/test_adapter_protocol.py`, exit 0) over all of `app/core` + `app/state`
  including the new `core/status_page.py` — ZERO *arr/slskd wire-vocabulary offenders (direct re-grep
  confirmed: offenders = NONE).
- **Task verifies:** Task 1 `tests/test_status_page.py tests/test_adapter_protocol.py` (6 passed);
  Task 2 `tests/test_status_page.py tests/test_health.py tests/test_detect_endpoint.py` (11 passed);
  Task 4 `deploy-docs-ok`; Task 3 `tests/test_shares.py tests/test_lidarr_adapter.py` (27 passed
  against the `[ASSUMED]` fixtures).
- Local env: Python 3.9 + offline (dev sandbox). All tests run with fakes/temp-sqlite + no network, so
  they pass identically here and on CI/NAS 3.12. No 3.13/3.9-only failures observed this plan.

## Deviations from Plan

**1. [Rule 2 — Auto-add missing critical functionality] Defensive boot reconcile**
- **Found during:** Task 2. Wiring `reconcile_on_startup` into `_startup` made `build_adapters()` run
  eagerly at boot. `build_adapters()` raises `ValueError` when the *arr API keys are absent (the
  offline test default), which crashed app startup — breaking the Task 1 status tests AND the
  pre-existing `test_startup_retains_and_shutdown_closes_db` / `test_detect_endpoint` (which never
  built adapters at boot before).
- **Issue:** A boot-time infra/config fault (e.g. *arr transiently unreachable, or a misconfigured key
  on the NAS) should not crash-loop the whole app — the app should come up and let the scheduler
  reconcile/retry on a later cycle. This is the REL-01/REL-02 philosophy applied to boot.
- **Fix:** Wrapped the `reconcile_on_startup(...)` call in `_startup` in a `try/except` that logs and
  continues; the scheduler still starts (its own cycles are already guarded — Pitfall 5). This both
  fixes the genuine boot-robustness gap AND keeps the pre-existing tests green WITHOUT editing them
  (their startup no longer crashes; the scheduler's own boot cycle fails fast inside its guarded
  `_tick` and the daemon survives).
- **Files modified:** `app/main.py`. **Commit:** `97bf576`.
- No assertion was weakened; no out-of-scope test was edited.

No other deviations — Tasks 1, 3, 4 executed as written.

## Known Stubs

None. The status page is wired to real ledger DAOs (`repo.status_counts` / `list_by_status` /
`imported_recent`) and `app.state.shares_ok`; the lifecycle wiring is real. The only deliberately
deferred work is the A2/A3 live confirmation (a truth-pin, not a stub — the offline code already uses
the research-confirmed shapes and is fully exercised by the passing offline tests).

## Pending owner actions (relay these)

1. **A2/A3 live-NAS probe (Task 3 checkpoint).** This session has NO NAS access, so the two live
   probes are scaffolded in `.planning/phases/phase-5/05-05-LIVE-PROBE.md` with the offline-assumed
   shapes marked `[ASSUMED — pin live on NAS]` (mirroring how Phase 4's 04-05 left its probes to the
   owner). On the next NAS rebuild, run the two `curl` probes in that file:
   - A3: `curl -s http://192.168.86.37:5030/api/v0/application -H "X-API-Key: $SLSKD_API_KEY" | jq '.shares'`
     (confirm `files` is an int) and `curl -s -o /dev/null -w "%{http_code}\n" -X PUT
     http://192.168.86.37:5030/api/v0/shares -H "X-API-Key: $SLSKD_API_KEY"` (expect 204/409).
   - A2: `curl -s 'http://192.168.86.37:8686/api/v1/queue?page=1&pageSize=20' -H "X-Api-Key:
     $LIDARR_API_KEY" | jq '.records[0] | keys'` (confirm the album field is `albumId`).
   Paste the JSON bodies (not the headers — T-05-23) into the probe file. If both match the assumed
   shapes, just drop the `[ASSUMED]` markers (no code change). If either differs, reconcile the
   adapter read + fixture to live truth and re-run `cd app && python3 -m pytest tests/test_shares.py
   tests/test_lidarr_adapter.py -q`. Nothing offline is blocked on this.

2. **D-06 staged rollout (DEPLOY.md Step 9).** This is an owner-driven rollout, NOT a code task — do
   NOT auto-run the daemon. Back up `/db/curator.sqlite` → recreate curator (runs migration_0003) →
   deploy with `ACQ_DRY_RUN=true` and watch one dry-run cycle → set `ACQ_DRY_RUN=false`,
   `MAX_CONCURRENT=1` and watch the FIRST capped pass import ONE album end-to-end (the D-06 acceptance
   test) → raise `MAX_CONCURRENT=3` for steady state. `ACQ_ENABLED=false` halts instantly.

## Self-Check: PASSED
