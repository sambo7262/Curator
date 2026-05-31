---
phase: 04-acquisition-staging-clean-import
plan: 02
subsystem: slskd-client + staging-lifecycle
tags: [slskd, httpx, rest-client, path-traversal, security, staging, quarantine, ttl, tdd, offline-test]
requires:
  - "LidarrAdapter constructor + .get()-defensive httpx pattern (Phase 2, adapters/lidarr.py)"
  - "slskd/* offline fixtures (Phase 4 plan 01, tests/fixtures/slskd/)"
  - "conftest load_fixture subpath loader (Phase 4 plan 01)"
provides:
  - "SlskdClient: search/search_state/search_responses/enqueue/transfer/cancel over /api/v0 with X-API-Key"
  - "module-level A3 transfer terminal-state constants (re-pinned live by 04-05)"
  - "core/staging.py: staging_path + assert_under_root + purge_staging + quarantine_staging + purge_expired_quarantine"
  - "path-traversal + shallow-root security guard gating every rmtree/move"
affects:
  - "04-03 import methods (no direct dep; both feed 04-04)"
  - "04-04 acquire loop (consumes SlskdClient for search/download/watch/cancel + staging helpers for isolation/purge/quarantine)"
  - "04-05 live probes (re-pin A2 batchId + A3 transfer-state strings against the named constants)"
tech-stack:
  added: []   # no new runtime dependency (T-04-SC: httpx already pinned; respx not required — MockTransport used)
  patterns:
    - "thin defensive REST client mirroring lidarr.py (constructor fail-fast, .get() never subscript, raise_for_status primary posture)"
    - "resolve()+strict-parents path-traversal guard + shallow-root deny-list before EVERY destructive fs op"
    - "respx-free httpx.MockTransport recorder asserting method/path/params/headers/body offline"
key-files:
  created:
    - app/adapters/slskd.py
    - app/core/staging.py
    - app/tests/test_slskd_client.py
    - app/tests/test_staging.py
  modified: []
decisions:
  - "slskd base appends /api/v0 and header is capital X-API-Key (not Servarr's X-Api-Key); host from settings.slskd_url (gluetun port), never a container name (Pitfall 7)"
  - "slskd is the primary download path → raise_for_status on every call (mirrors Lidarr); CircuitBreaker seam left for Phase 5, not wrapped here"
  - "Shallow-root refusal floors the configured root at >=3 path parts AND an explicit /, /data, /data/media deny-list — a mis-set quarantine_root can never wipe the library (T-04-05)"
  - "REQUIREMENTS (ACQ-01/02/03, IMPORT-01/05) left Pending — this plan lands the mechanical surfaces only; they are fully satisfied once 04-04 composes the loop and 04-05 verifies live"
metrics:
  duration_minutes: 7
  completed: 2026-05-31
  tasks: 2
  files_created: 4
  files_modified: 0
  tests_added: 33
  suite: "172 passed (was 139)"
---

# Phase 4 Plan 02: SlskdClient + Staging Lifecycle Summary

Built the two net-new mechanical surfaces of Phase 4 with zero live services: a thin, defensive Curator→slskd REST client (`SlskdClient` over `/api/v0` with the capital `X-API-Key` header) and the pure-stdlib filesystem staging/quarantine lifecycle (`core/staging.py`) whose every destructive op is gated by a resolve()+strict-parents path-traversal guard plus a shallow-root deny-list. Both are fully unit-proven offline (httpx `MockTransport` + `tmp_path`), including the IMPORT-01 hardlink `samefile` path-identity proof. 33 new tests; suite 172 passed (was 139). No new runtime dependency.

## What Was Built

**Task 1 — SlskdClient (RED `47a1824` → GREEN `a184e71`):** `app/adapters/slskd.py` mirrors `lidarr.py`'s constructor exactly with two slskd differences — `self._base = base_url.rstrip("/") + "/api/v0"` and `self._headers = {"X-API-Key": api_key}` (capital `API`). A None/empty key raises `ValueError("SLSKD_API_KEY is required ...")` at construction (CR-01 fail-fast, slskd is the primary download path). Six methods over the verified endpoint contract: `search(text)` POSTs `{"searchText": text}` and returns `.get("id")` (None-safe); `search_state(sid)` GETs the search dict; `search_responses(sid)` GETs the per-peer list (non-list body → `[]`, T-04-06); `enqueue(username, files)` POSTs the files list; `transfer(username, tid)` GETs the transfer dict (`state`/`bytesTransferred` read via `.get()`, never KeyError); `cancel(username, tid, remove=True)` DELETEs with `remove=true|false` in the query. Every call uses `raise_for_status()` (primary posture — a hard fault surfaces, not swallowed; the `CircuitBreaker` seam is left for Phase 5). The X-API-Key lives only in `_headers`, sourced from settings, never logged (T-04-07). Module-level A3 named constants (`STATE_COMPLETED_SUCCEEDED`/`STATE_IN_PROGRESS`/`STATE_FAILED` + `TERMINAL_SUCCESS/FAILURE_SUBSTRINGS`) expose the `[ASSUMED]` terminal-state vocabulary for 04-04 to interpret and 04-05 to re-pin live — the client itself never buries state parsing, it returns the raw dict. 16 tests via a respx-free `MockTransport` recorder assert method/path/params/headers/body for each method (base ends `/api/v0`, header is `x-api-key`, 5xx raises).

**Task 2 — staging.py (RED `3692e2d` → GREEN `03997ae`):** `app/core/staging.py` is a pure stdlib (pathlib/shutil/os/time) core module — firewall-clean, speaks only filesystem paths, no httpx, no *arr/slskd vocabulary. Five functions: `staging_path(downloads_root, batch_id)` computes `downloads_root/{batch_id}` and NEVER creates it (slskd materializes the dir via batchId routing); `assert_under_root(path, root)` is the security guard — it `resolve()`s both first (defeating `../` and symlink escapes), refuses a `root` resolving to `/`, `/data`, `/data/media` or with `< 3` non-anchor parts (T-04-05), then requires the resolved root to be in the resolved path's `parents` (strict-under, T-04-04); `purge_staging(staging_dir, root)` (D-05) and `quarantine_staging(staging_dir, quarantine_root, label)` (D-06, moves to `{label}-{int(time.time())}`, returns the new path) and `purge_expired_quarantine(quarantine_root, ttl_seconds)` (D-06, rmtrees subdirs whose `st_mtime` is older than `now-ttl`, returns the count) each call `assert_under_root` BEFORE any `rmtree`/`move`. 17 tests via `tmp_path` cover the dotdot-escape, symlink-escape, root-equals-target, and parametrized shallow-root (`/`, `/data`, `/data/media`) refusals; the gated purge/move/TTL behaviors; and the IMPORT-01 `os.link` + `os.path.samefile` path-identity proof (a Move within one filesystem is an atomic, zero-copy hardlink).

## Provenance Markers ([ASSUMED] — pending 04-05 live probes)

- Transfer terminal/in-progress `state` strings (A3) — encoded as module-level named constants in `slskd.py` (`STATE_COMPLETED_SUCCEEDED` etc.) with a comment instructing 04-05 to re-pin the live strings in one place; the client exposes the raw dict, so changing the constants will not touch the client logic.
- `batchId` settability on the enqueue body (A2) — out of scope for this plan (enqueue posts the bare files list per the verified contract); 04-04/04-05 decide the batchId routing.

## Deviations from Plan

None — both tasks executed exactly as written via the TDD RED→GREEN cycle, no auto-fixes required. No REFACTOR commit was needed (both implementations were clean on first GREEN).

## Requirements

ACQ-01, ACQ-02, ACQ-03, IMPORT-01, IMPORT-05 are **contributed to but NOT yet fully satisfied** by this plan — it lands only the mechanical client + staging surfaces. They are completed when 04-04 composes the search→gate→download→watch→import→purge/quarantine loop and 04-05 verifies it live. REQUIREMENTS.md rows therefore remain `Pending` (marking them complete now would overstate the delivery).

## Verification

- `cd app && python3 -m pytest tests/test_slskd_client.py tests/test_staging.py -x -q` — 33 passed.
- `cd app && python3 -m pytest` — **172 passed** (was 139), 4 pre-existing FastAPI `on_event` deprecation warnings (out of scope).
- `tests/test_adapter_protocol.py::test_core_state_have_no_arr_field_names` — passes with the new `core/staging.py` in scope (firewall holds; staging speaks only filesystem paths).
- The traversal-escape, symlink-escape, and shallow-root refusal tests all assert a raised `ValueError`; the hardlink `samefile` test proves IMPORT-01 within one filesystem.

## Known Stubs

None. The A2/A3 `[ASSUMED]` values are named constants (valid, parseable, consumed by passing tests) whose live resolution is scoped to 04-05's NAS probes — not code stubs.

## Self-Check: PASSED

- app/adapters/slskd.py, app/core/staging.py, app/tests/test_slskd_client.py, app/tests/test_staging.py all present on disk.
- Commits 47a1824, a184e71, 3692e2d, 03997ae all present in `git log`.
