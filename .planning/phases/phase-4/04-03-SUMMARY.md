---
phase: 04-acquisition-staging-clean-import
plan: 03
subsystem: arr-agnostic-import-path
tags: [manualimport, lidarr, readarr, firewall, verify-by-requery, best-effort, tdd, offline-test]
requires:
  - "ArrAdapter Protocol stubs manual_import_candidates/execute_import/verify_imported (base.py, Phase 2)"
  - "LidarrAdapter get_manifest GET + get_wanted paging + X-Api-Key primary posture (Phase 2/3)"
  - "ReadarrAdapter _paged swallow block + best-effort posture (Phase 2/3)"
  - "manualimport/get_mapping.json + expected_post.json fixtures (Phase 4 plan 01)"
provides:
  - "LidarrAdapter.manual_import_candidates (adapter-filtered importable subset) + execute_import(Move) + verify_imported (re-query)"
  - "ReadarrAdapter best-effort import methods (swallow->[]/None/False)"
  - "the explicit *arr-agnostic ManualImport(Move) path 04-04's loop calls behind the firewall"
affects:
  - "04-04 acquire loop (calls candidates -> execute_import -> verify_imported as neutral steps; core stays *arr-key-blind)"
  - "04-05 live probes (re-pin [ASSUMED A1] importMode casing + files[] envelope against expected_post.json in one place)"
tech-stack:
  added: []   # no new runtime dependency (T-04-SC: httpx already pinned; MockTransport, not respx)
  patterns:
    - "adapter-side importability filter: rejections/tracks read IN-adapter so core never inspects *arr keys"
    - "explicit ManualImport(Move) per-file files[] command — never a DownloadedAlbumsScan blind rescan (T-04-09)"
    - "verify-by-requery: True iff the item LEFT the wanted/missing list ('downloaded' != 'imported', D-03)"
    - "Readarr whole-body swallow->safe-default on every import method (ARR-02); verify direct-requery so a fault -> False not fake-True"
key-files:
  created: []
  modified:
    - app/adapters/lidarr.py
    - app/adapters/readarr.py
    - app/adapters/base.py
    - app/tests/test_lidarr_adapter.py
    - app/tests/test_readarr_adapter.py
decisions:
  - "manual_import_candidates returns the ALREADY-FILTERED importable subset (empty rejections + non-empty tracks); core gets opaque dicts and never reads an *arr key"
  - "Readarr verify_imported issues the wanted re-query DIRECTLY (not via get_wanted, whose _paged already swallows to []) so a 5xx degrades to False, never a fake True (Pitfall 5)"
  - "importMode casing 'Move' + files[] envelope flagged [ASSUMED A1] inline, pinned live in 04-05 via the single expected_post.json fixture"
requirements: [IMPORT-02, IMPORT-03, IMPORT-05]
metrics:
  duration_minutes: 9
  completed: 2026-05-31
  tasks: 2
  files_created: 0
  files_modified: 5
  tests_added: 13
  suite: "185 passed (was 172)"
---

# Phase 4 Plan 03: *arr-Agnostic ManualImport Path Summary

Implemented the explicit, *arr-agnostic import path that is the entire reason Curator exists — replacing Soularr's blind drop-folder rescan with an explicit per-file `ManualImport(Move)` of only the wanted files, plus a re-query that proves a real import (`downloaded` never counts as `imported`). Lidarr (primary) raises on hard faults; Readarr (best-effort) degrades silently so books never gate music. The importability decision (reading the *arr `rejections`/`tracks` keys) lives entirely inside the adapter, so `manual_import_candidates` hands core only the pre-filtered importable subset and core stays *arr-key-blind. All proven offline with `httpx.MockTransport`; 13 new tests; suite 185 passed (was 172). No new dependency.

## What Was Built

**Task 1 — Lidarr import methods (RED `6a0b3a2` → GREEN `d6c6364`):** Three methods on `LidarrAdapter`, all *arr wire vocabulary local to `lidarr.py`:
- `manual_import_candidates(path, download_id=None)` GETs `/api/v1/manualimport` with `folder` (base.py's `path` → *arr `folder`), `downloadId`, `filterExistingFiles=true`, `replaceExistingFiles=true` (timeout 60, `raise_for_status`). The ADAPTER then filters to the importable subset — keep only resources with an empty `rejections` AND a non-empty `tracks` — and returns those opaque dicts. The `rejections`/`tracks` reads that drive the importability decision stay in the adapter; core passes the list straight back to `execute_import` and never reads an *arr key.
- `execute_import(decisions)` POSTs `/api/v1/command` with `name="ManualImport"`, `importMode="Move"` (the atomic-hardlink contract within `/data`, D-09), and a per-decision `files[]` entry carrying `path/artistId/albumId/albumReleaseId/trackIds/quality/indexerFlags/disableReleaseSwitching=False/downloadId` (all read via `.get()` where a field may be absent). NEVER a `DownloadedAlbumsScan` (T-04-09 — asserted). The casing is flagged `[ASSUMED A1]` inline pending the 04-05 live capture.
- `verify_imported(item)` re-queries `get_wanted()` and returns `item.arr_id not in still_wanted` — True only if the item LEFT the wanted/missing+cutoff list (D-03; a completed download is never treated as imported).
- `base.py`: dropped "Stubbed in Phase 2" on the three now-implemented Protocol methods and documented that `manual_import_candidates` returns the already-filtered importable subset (Protocol signatures unchanged).

**Task 2 — Readarr best-effort import methods (RED `d46dd7d` → GREEN `f38b35c`):** The same three methods on `ReadarrAdapter`, each wrapping its whole body in the readarr swallow block `except (httpx.HTTPError, ValueError, TypeError, KeyError): log.warning(...); return <safe_default>` with safe defaults `[]` / `None` / `False`. `manual_import_candidates` mirrors Lidarr's in-adapter importability filter so core stays key-blind; on any fault returns `[]`. `execute_import` mirrors the `ManualImport(Move)` shape with book-identity wire fields (`bookId`/`editionId`/`authorId`) kept adapter-local (A5); on any fault returns `None`. `verify_imported` issues the wanted re-query DIRECTLY rather than through `get_wanted()` (whose `_paged` already swallows a fault to `[]`, which would yield a fake True) so a 5xx is observed here and degrades to `False` — Pitfall 5: a false-negative forces quarantine (safe), a false-positive would skip cleanup. A book outage can never raise into the loop (ARR-02).

## Provenance Markers ([ASSUMED] — pending 04-05 live probes)

- ManualImport `importMode` casing (`"Move"`) + the `files[]` element key set (A1) — flagged inline in both adapters' `execute_import` and asserted against the single `manualimport/expected_post.json` fixture, so the live capture pins them in ONE place.

## Deviations from Plan

None — both tasks executed exactly as written via the TDD RED→GREEN cycle. No REFACTOR commit was needed (both implementations were clean on first GREEN). No auto-fixes required.

One implementation nuance worth recording (within plan scope, not a deviation): the plan suggested `verify_imported` could "re-use get_wanted's read." For Lidarr (primary, `get_wanted` raises on fault) re-using `get_wanted()` is correct and the 5xx test proves the fault surfaces. For Readarr, `get_wanted` already swallows to `[]`, which would make a faulting re-query return a fake True; so the Readarr `verify_imported` issues the wanted re-query directly under its own swallow, degrading to False on fault per Pitfall 5. Both satisfy the plan's behavior contract.

## Requirements

- **IMPORT-02** (Manual-Import only the wanted files via the *arr command API) — the explicit `ManualImport(Move)` per-file path is implemented behind the firewall.
- **IMPORT-03** (verify the item actually imported) — `verify_imported` re-queries and returns True only when the item left the wanted list (D-03).
- **IMPORT-05** (quarantine-on-failure / purge-on-success) — contributed to: the safe-default semantics (`[]`/`None`/`False`) feed the quarantine-vs-purge decision; fully composed by 04-04's loop.

Marked complete in REQUIREMENTS.md where the adapter surface fully delivers (IMPORT-02/03); IMPORT-05's cleanup composition lands in 04-04.

## Verification

- `cd app && python3 -m pytest tests/test_lidarr_adapter.py tests/test_readarr_adapter.py -x -q` — all green.
- `cd app && python3 -m pytest` — **185 passed** (was 172), 4 pre-existing FastAPI `on_event` deprecation warnings (out of scope).
- `tests/test_adapter_protocol.py::test_core_state_have_no_arr_field_names` — passes (the firewall holds: all new *arr keys + the rejections/tracks reads live only in `lidarr.py`/`readarr.py`; app/core + app/state untouched).
- The POSTed command name is asserted `== "ManualImport"` and `!= "DownloadedAlbumsScan"` (T-04-09); the importable filter excludes the rejected fixture resource (id 103); `verify_imported` distinguishes downloaded from imported in both directions.

## Known Stubs

None. The `[ASSUMED A1]` importMode casing / files[] envelope is a live-probe-pending value (valid, parseable, consumed by passing tests against `expected_post.json`), resolved in 04-05 — not a code stub.

## Threat Flags

None — no new security surface beyond the plan's threat register (T-04-08/09/10/11 all mitigated as specified; no new endpoints, auth paths, or schema changes).

## Self-Check: PASSED

- app/adapters/lidarr.py, app/adapters/readarr.py, app/adapters/base.py, app/tests/test_lidarr_adapter.py, app/tests/test_readarr_adapter.py all present on disk.
- Commits 6a0b3a2, d6c6364, d46dd7d, f38b35c all present in `git log`.
