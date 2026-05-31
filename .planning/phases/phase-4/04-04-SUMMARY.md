---
phase: 04-acquisition-staging-clean-import
plan: 04
subsystem: acquisition-loop-composition
tags: [acquire, orchestrator, firewall, stall-watch, collection-window, quarantine, ttl, offline-test, fake-clock]
requires:
  - "core/gate.evaluate + core/selector.select (Phase 3, consumed UNCHANGED)"
  - "core/candidate.Candidate.from_slskd factory (Phase 3)"
  - "adapters/slskd.py SlskdClient search/transfer/cancel + A3 terminal-state constants (04-02)"
  - "core/staging.py staging_path/purge_staging/quarantine_staging/purge_expired_quarantine (04-02)"
  - "adapters base ArrAdapter get_manifest/get_quality_profile + manual_import_candidates/execute_import/verify_imported (04-03, pre-filtered importable subset)"
  - "state/repo.set_status (acquisition enums) + record_staged_file/record_quarantine (04-01)"
  - "config.settings Phase-4 tunables (04-01)"
provides:
  - "core/acquire.acquire_item — the single-item search→gate→download→stall-watch→import→verify→purge/quarantine loop (returns imported|quarantined|stuck)"
  - "core/acquire.build_acquire_clients — lazy-httpx SlskdClient factory (caller owns close, CR-02)"
  - "core/acquire.TransferProgress — the NEUTRAL transfer progress shape the slskd client hands the stall watch"
  - "adapters/slskd neutral seams: search_is_complete, enqueue_candidate→TransferHandle, transfer_progress, cancel_transfer"
  - "the firewall grep extended over core/acquire.py (test_gate.py)"
affects:
  - "04-05 live probes (re-pin A3 terminal-state strings + A1 ManualImport envelope; acquire consumes the named constants, no literal change needed in core)"
  - "Phase 5 daemon/scheduling (will call acquire_item per eligible gap; backoff/grace-window wrap it)"
tech-stack:
  added: []
  patterns:
    - "neutral progress seam: slskd wire keys (state/bytesTransferred/isComplete) + uploader identity interpreted IN the client; core sees only TransferProgress + an opaque TransferHandle"
    - "no-progress stall watch over a fake monotonic clock (reset timer on any byte advance, cancel on stall, fall to next candidate, exhausted→stuck)"
    - "gate.evaluate ONCE per search; runner-up order derived by re-running the SAME gate over remaining candidates (re-use gate/selector, never re-judge match)"
    - "adapter returns the already-filtered importable subset; core consumes AS-IS (empty→quarantine, non-empty→execute_import), never reads an *arr key"
    - "import-fault / verify-False / empty-subset → quarantine (move + record reason), verified import → purge (D-05); a Readarr fault quarantines that book only (ARR-02)"
key-files:
  created:
    - app/core/acquire.py
    - app/tests/test_acquire.py
  modified:
    - app/adapters/slskd.py
    - app/tests/test_gate.py
decisions:
  - "acquire.py never reads the SELECTOR-ONLY uploader identity: enqueue_candidate takes the whole Candidate and returns an OPAQUE TransferHandle; the matching!=selection grep and the *arr/slskd firewall grep both hold over the new core module"
  - "the slskd client owns the neutral progress seam (search_is_complete / transfer_progress→TransferProgress / cancel_transfer) so no slskd wire key crosses into core"
  - "manifest/profile unavailable (adapter None OR raise) → stuck WITHOUT searching; runner-up fallback re-runs the SAME gate rather than re-deriving match precision"
  - "no Plex call anywhere — IMPORT-04 satisfied externally by the owner's Plex auto-scan (revised D-04)"
requirements: [ACQ-01, ACQ-02, ACQ-03, IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-05]
metrics:
  duration_minutes: 17
  completed: 2026-05-31
  tasks: 2
  files_created: 2
  files_modified: 2
  tests_added: 16
  suite: "201 passed (was 185)"
---

# Phase 4 Plan 04: Acquisition Loop Composition Summary

Composed the Phase-4 machine: `core/acquire.acquire_item` is the single-item loop that wires
search → gate → download → no-progress stall-watch → ManualImport → verify-by-requery →
purge/quarantine for one gap, speaking ONLY neutral shapes. It consumes Phase 3's gate/selector
unchanged, the 04-02 slskd client + staging helpers, and the 04-03 adapter import methods (which
already return the pre-filtered importable subset — core passes that list straight to
`execute_import` and never reads an *arr key). The whole loop is proven offline with fake clients +
a fake monotonic clock for every branch (no sleep, no network). The firewall grep is extended over
`core/acquire.py` and holds (zero *arr/slskd wire vocabulary). Curator makes NO Plex call —
IMPORT-04 is satisfied externally by the owner's Plex "scan on new media" auto-scan (revised D-04).
16 new tests; suite **201 passed** (was 185). No new dependency.

## What Was Built

**Task 1 — `core/acquire.py` + neutral slskd seams (`d76cfa8`):**

- `acquire_item(item, adapter, slskd, conn, settings, now=time.monotonic, gate_evaluate=…, build_candidate=…, poll_hook=…) -> "imported"|"quarantined"|"stuck"`, mirroring `gap_detector.detect_gaps`' single-composition-point shape and honoring D-01..D-10:
  - **D-06 housekeeping FIRST:** `staging.purge_expired_quarantine(...)` before the search (the TTL-on-next-run trigger), never raising on it.
  - **Phase-3 contract:** fetch `adapter.get_manifest(item.foreign_id)` + `adapter.get_quality_profile(item.quality_profile_id)`; if either is unavailable (None return OR raise, normalized by `_safe_call`), set status `stuck` and return WITHOUT searching slskd.
  - **D-07 collection window:** one `search`, poll `search_is_complete` until complete or the monotonic window deadline, build Candidates, call `gate.evaluate` ONCE over the full set.
  - **D-08 relaxed retry:** on a full decline, `_relax_query` drops year/edition noise and re-searches + re-evaluates ONCE; still declined → `stuck`.
  - **D-01/D-02 stall + fallback:** `_watch_to_completion` diffs the neutral `TransferProgress.bytes_done` over the injected clock; resets the timer on ANY byte advance (tolerant of slow peers, Pitfall 4); on no advance for `acq_stall_seconds` it `cancel_transfer(remove=True)` and falls to the next gate-accepted candidate; a terminal failure also falls to next; exhausted accepted set → `stuck` (never loops forever).
  - **D-03/D-05/D-06 import:** on terminal success, `_import_and_verify` asks `adapter.manual_import_candidates(staging_path)` (already the importable subset); empty → quarantine; non-empty → `execute_import(that exact list)`; `verify_imported` True → `purge_staging` (D-05) + status `imported`; verify False OR import raise → `quarantine_staging` + `record_quarantine` + status `quarantined` (D-06, never blind-purged).
  - **State transitions:** pending → searching → downloading → importing → imported / quarantined / stuck via `repo.set_status`; a `staged_files` row recorded per download attempt.
  - **NO Plex call** anywhere (revised D-04 — IMPORT-04 external).
- `build_acquire_clients(settings)`: lazy `import httpx`, constructs `SlskdClient`, returns `(slskd, clients)` with the caller owning `close()` (CR-02).
- `TransferProgress` (neutral frozen `(terminal, bytes_done)`) defined in acquire.py.
- **`adapters/slskd.py` neutral seams** (wire vocab stays in the client): `search_is_complete` (reads `isComplete`), `enqueue_candidate(candidate)` → opaque `TransferHandle` (reads the SELECTOR-ONLY `username` + builds the `{filename,size}` body), `transfer_progress(handle)` → `TransferProgress` (interprets `state`/`bytesTransferred` + the A3 `TERMINAL_SUCCESS/FAILURE_SUBSTRINGS`), `cancel_transfer(handle)`.

**Task 2 — firewall grep extension (`ee8feca`):** `test_gate.py::test_acquire_has_no_arr_field_names` greps `core/acquire.py` line-by-line (comment-stripped via `_strip_comment`) for the *arr/slskd wire-vocabulary token set (`folder|downloadId|albumReleaseId|importMode|X-Api-Key|X-API-Key|ManualImport|artistId|albumId|trackIds|searchText|bytesTransferred|isComplete|hasFreeUploadSlot`) and asserts ZERO matches in executable code. Additive — does not weaken the matching!=selection grep or the *arr-field grep in `test_adapter_protocol.py`.

## Deviations from Plan

**[Rule 3 — Blocking] acquire.py must not read the SELECTOR-ONLY uploader identity to enqueue/watch/cancel.**
- **Found during:** Task 1 (first full-suite run after acquire.py landed).
- **Issue:** The natural composition reads `candidate.username` to enqueue the chosen copy and to address the transfer for the stall watch/cancel. That tripped the *pre-existing* matching!=selection grep (`test_selector_only_reads_uploader_fields`), which forbids `.username` reads outside `selector.py` (Pitfall 5 / T-03-06).
- **Fix:** Moved the uploader-identity read into the slskd client. `enqueue_candidate(candidate)` reads `candidate.username` + files there and returns an OPAQUE `TransferHandle`; `transfer_progress(handle)` / `cancel_transfer(handle)` address the transfer via the handle, so acquire.py never names a username. This is the correct firewall posture (the uploader identity is a selection concern, normalized in the client like every other slskd wire detail) and keeps BOTH grep tests green.
- **Files modified:** app/core/acquire.py, app/adapters/slskd.py.
- **Commit:** d76cfa8.

No other deviations — the loop was built as the plan specified. No architectural (Rule 4) changes; no auth gates.

## Requirements

- **ACQ-01** (trigger slskd searches for eligible gaps) — collection-window search + gate-once landed and proven.
- **ACQ-02** (download the chosen candidate into isolated per-item staging, watch to completion) — enqueue + per-item `staging_path` under `staging_root` + the stall watch landed.
- **ACQ-03** (handle partial/failed/stalled; never hold a slot forever) — no-progress stall cancel + next-candidate fallback + exhausted-stuck proven with a fake clock.
- **IMPORT-01** (isolated per-item staging on /data) — deterministic `curator-{app}-{id}` batch label under `staging_root` (matches slskd `directories.downloads`, D-12).
- **IMPORT-02** (ManualImport only the wanted files) — composed via the adapter's pre-filtered subset → `execute_import` (the explicit path, never a blind rescan).
- **IMPORT-03** (verify real import) — `verify_imported` re-query gates the purge; verify-False quarantines (D-03).
- **IMPORT-05** (auto-purge on success / quarantine + reconcile on failure) — purge-on-success (D-05) and quarantine-with-reason on every failure branch (D-06) proven.

All seven are now fully satisfied at the offline-mechanism level (live confirmation of A1/A3 strings is 04-05). Marking complete in REQUIREMENTS.md.

## Verification

- `cd app && python3 -m pytest tests/test_acquire.py -x -q` — 15 green (every D-01..D-10 branch).
- `cd app && python3 -m pytest tests/test_gate.py::test_acquire_has_no_arr_field_names` — green; negative control confirmed (a deliberate `downloadId` leak fails it), then reverted.
- `cd app && python3 -m pytest` — **201 passed** (was 185), 4 pre-existing FastAPI `on_event` deprecation warnings (out of scope).
- Manual grep over `core/acquire.py` for the wire-vocabulary token set — zero hits (firewall clean).

## Known Stubs

None. The A3 terminal-state strings + A1 ManualImport envelope are `[ASSUMED]` named-constant / fixture values (valid, consumed by passing tests), pinned live in 04-05 — not code stubs. acquire.py references them only through the client seam, so the live pin changes nothing in core.

## Threat Flags

None — no new security surface beyond the plan's threat register. T-04-12 (stall cancel), T-04-13 (purge/quarantine via `assert_under_root`), T-04-14 (import the adapter's pre-filtered subset only), T-04-15 (Readarr fault isolates), T-04-16 (neutral log identity only) are all mitigated as specified. No new endpoints, auth paths, or schema changes.

## Self-Check: PASSED

- app/core/acquire.py, app/tests/test_acquire.py present on disk; app/adapters/slskd.py, app/tests/test_gate.py modified.
- Commits d76cfa8 (Task 1) and ee8feca (Task 2) present in `git log`.
