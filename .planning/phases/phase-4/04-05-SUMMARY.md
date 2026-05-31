---
phase: 04-acquisition-staging-clean-import
plan: 05
subsystem: acquisition-loop-reconciliation
tags: [slskd, lidarr, readarr, manualimport, staging, firewall, live-probe, offline-test, A1, A2, A3]

# Dependency graph
requires:
  - phase: 04-04
    provides: "core/acquire.acquire_item composition loop + neutral slskd progress seams (TransferHandle/TransferProgress) + A3 [ASSUMED] terminal-state constants"
  - phase: 04-03
    provides: "lidarr/readarr execute_import + manual_import_candidates (the [ASSUMED] A1 ManualImport envelope)"
  - phase: 04-02
    provides: "SlskdClient + core/staging.py (staging_path/purge/quarantine)"
provides:
  - "A3 PINNED: slskd terminal-state interpreted by the live-observed 'Completed, Succeeded' + the robust substring rule (TERMINAL iff 'Completed', SUCCESS iff also 'Succeeded', else FAILURE)"
  - "A2 PINNED: import + purge/quarantine target = staging_root/<leaf-of-remote-folder> (no batchId/username subdir), via TransferHandle.landing_dir_name (neutral) — acquire stays firewall-clean"
  - "A1 PINNED: ManualImport envelope — importMode lowercase 'move' (D-09 hardlink), top-level replaceExistingFiles/sendUpdatesToClient, per-file full QualityModel, no per-file downloadId"
  - "offline suite re-pinned to the live truth (205 passed), fixtures reconciled, no weakened assertions"
affects:
  - "Phase 5 daemon/scheduling (calls acquire_item per gap — now production-trustworthy against the real slskd/Lidarr shapes)"

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "live-probe reconciliation: capture the real wire shapes once on the NAS, fold them back into the offline fixtures/constants so the offline suite remains the source of truth — pinned to reality"
    - "A2 neutral landing-dir seam: the slskd client derives the remote-folder leaf and exposes it as TransferHandle.landing_dir_name (a plain dir name), so acquire points import+purge at the real folder WITHOUT typing any wire key (firewall holds; the token 'folder' never appears in acquire.py executable OR docstring text)"
    - "A3 robust terminal rule lives in the client's transfer_progress: 'Completed' gates terminality, then the completion half disambiguates — open-ended failure family, not a hard-pinned unobserved literal"

key-files:
  created:
    - .planning/phases/phase-4/04-05-SUMMARY.md
  modified:
    - app/adapters/slskd.py
    - app/adapters/lidarr.py
    - app/adapters/readarr.py
    - app/core/acquire.py
    - app/core/staging.py
    - app/tests/fixtures/slskd/transfer_completed.json
    - app/tests/fixtures/slskd/transfer_failed.json
    - app/tests/fixtures/manualimport/expected_post.json
    - app/tests/fixtures/manualimport/get_mapping.json
    - app/tests/test_slskd_client.py
    - app/tests/test_lidarr_adapter.py
    - app/tests/test_acquire.py
    - .planning/phases/phase-4/04-05-LIVE-PROBE.md

key-decisions:
  - "A2 reconciliation was a real code change, not a no-op: Waves 0-2 assumed a Curator-computed curator-{app}-{id} staging dir, but slskd lands files at staging_root/<leaf-of-remote-folder> with no batchId/username subdir — acquire now resolves the staging path from the handle's landing leaf AFTER enqueue so the import+purge hit the REAL folder; the curator-{app}-{id} label is demoted to a leaf-less fallback"
  - "A1 importMode is the lowercase 'move' (Curator's deliberate D-09 choice) — NOT the UI default 'copy' and NOT the old [ASSUMED] capital 'Move'; per-file downloadId was DROPPED from the POST body (command-queue metadata, not part of the captured envelope)"
  - "A3 keeps the substring rule authoritative (only the success family was observed live); named failure/cancelled constants are documentation, and any terminal 'Completed, *' that is not 'Succeeded' is treated as a failure"
  - "the *arr-firewall constraint forbids the literal token 'folder' even in acquire.py DOCSTRINGS (the grep only strips # comments) — the leaf derivation lives entirely in the slskd client; acquire reads the neutral handle.landing_dir_name"

patterns-established:
  - "no weakened assertions: fixtures/constants were moved to the live truth first, then tests re-pinned to assert reality (the lidarr import test was STRENGTHENED — asserts move + absence of downloadId)"

requirements-completed: [ACQ-02, ACQ-03, IMPORT-01, IMPORT-02]

# Metrics
duration: 33min
completed: 2026-05-31
---

# Phase 4 Plan 05: Live-Probe Reconciliation Summary

**The offline acquisition suite is now pinned to the live NAS reality: slskd terminal state 'Completed, Succeeded' (A3 robust substring rule), the import/purge target resolved to staging_root/<leaf-of-remote-folder> (A2, no batchId), and the Lidarr ManualImport envelope with lowercase importMode 'move' (A1) — 205 tests green, firewall clean, no weakened assertions.**

## Performance

- **Duration:** ~33 min
- **Started:** 2026-05-31T16:58:00Z (approx)
- **Completed:** 2026-05-31T17:31:00Z
- **Tasks:** 1 executed (Task 3, `type:auto`). Tasks 1 & 2 were `checkpoint:human` and were completed by the owner before this run (observations recorded in 04-05-LIVE-PROBE.md).
- **Files modified:** 13

## Accomplishments

- **A3 (slskd terminal state):** Re-pinned `STATE_COMPLETED_SUCCEEDED = "Completed, Succeeded"` (live-observed) and dropped the `[ASSUMED]` markers. Rewrote `transfer_progress` to the robust A3 rule — a transfer is TERMINAL only once its `state` contains `"Completed"`, SUCCESS iff it also contains `"Succeeded"`, and any other terminal `"Completed, *"` (Errored/Cancelled/…) is a FAILURE → fall to the next candidate. Kept named failure/cancelled constants as documentation (the substring rule, not an unobserved exact literal, is authoritative). `transfer_completed.json` / `transfer_failed.json` re-pinned.
- **A2 (download landing path):** slskd lands files at `staging_root/<leaf-of-remote-folder>/` with NO batchId/username subdir (it uses only the last segment of the peer's remote folder; peer paths use `\` separators). Added `TransferHandle.landing_dir_name` + `_remote_folder_leaf` (splits on both `\` and `/`) in `slskd.py`; `enqueue_candidate` now derives the leaf. `acquire.py` resolves the staging dir from the handle's neutral leaf AFTER enqueue and points the import + purge/quarantine at THAT real folder. The `curator-{app}-{id}` label is demoted to a leaf-less fallback. The firewall stays clean (no wire vocabulary, and the token `folder` appears nowhere in acquire.py — not even in a docstring).
- **A1 (Lidarr ManualImport envelope):** Reconciled `lidarr.execute_import` (mirrored in `readarr.execute_import`) to the DevTools-captured envelope — top-level `name:"ManualImport"`, `importMode:"move"` (lowercase; Curator's deliberate D-09 hardlink choice, not the UI `"copy"`, not the old capital `"Move"`), `replaceExistingFiles:false`, `sendUpdatesToClient:true`; per-file `path/artistId/albumId/albumReleaseId/trackIds/quality (full QualityModel)/indexerFlags/disableReleaseSwitching`, with the per-file `downloadId` REMOVED (command-queue metadata, not body). `expected_post.json` + `get_mapping.json` reconciled (real leaf landing paths); the lidarr test strengthened to assert `move` + absence of `downloadId`.
- **Suite:** 205 passed (was 201; +4 new A2 tests). Firewall grep over `core/acquire.py` clean. No assertion weakened.

## Task Commits

Task 3 was reconciled and committed atomically by A1/A2/A3 concern:

1. **A3 — slskd terminal-state pin** - `94db977` (fix) — slskd.py constants + transfer_progress robust rule + transfer_*.json fixtures (this commit also carries the A2 `TransferHandle.landing_dir_name` + `_remote_folder_leaf` + `enqueue_candidate` slskd.py changes, since both edits live in the same file)
2. **A2 — real landing-dir resolution** - `fba83fb` (fix) — acquire.py leaf resolution + staging.py doc + test_acquire.py (e2e A2 proof + updated pre-created dirs) + test_slskd_client.py (3 leaf unit tests)
3. **A1 — ManualImport envelope pin** - `0c5d786` (fix) — lidarr.py + readarr.py execute_import + expected_post.json + get_mapping.json + test_lidarr_adapter.py

**LIVE-PROBE reconciliation note:** appended to `04-05-LIVE-PROBE.md` (committed with the final metadata commit).

## Files Created/Modified

- `app/adapters/slskd.py` — A3 constants + robust `transfer_progress` rule; A2 `TransferHandle.landing_dir_name` + `_remote_folder_leaf` + leaf derivation in `enqueue_candidate`
- `app/adapters/lidarr.py` — A1 envelope: `importMode:"move"`, top-level `replaceExistingFiles`/`sendUpdatesToClient`, full QualityModel echo, no per-file downloadId
- `app/adapters/readarr.py` — same A1 envelope (shared import path, D-10)
- `app/core/acquire.py` — resolve staging dir from the handle's landing leaf (A2); `_batch_label` demoted to fallback; firewall-clean
- `app/core/staging.py` — `staging_path` doc updated to the remote-folder-leaf reality
- `app/tests/fixtures/slskd/transfer_completed.json` / `transfer_failed.json` — A3 docs/values re-pinned
- `app/tests/fixtures/manualimport/expected_post.json` / `get_mapping.json` — A1 envelope + A2 leaf paths
- `app/tests/test_slskd_client.py` — 3 new A2 unit tests (leaf split, handle leaf, file-dir fallback)
- `app/tests/test_lidarr_adapter.py` — strengthened import test (move + no downloadId)
- `app/tests/test_acquire.py` — new e2e A2 proof (deep backslash peer path → leaf import+purge); pre-created dirs moved from curator-* to the candidate-folder leaf; fake `enqueue_candidate` carries the leaf
- `.planning/phases/phase-4/04-05-LIVE-PROBE.md` — Reconciliation section appended

## Decisions Made

- **A2 forced a real code change** (not the "no change if batchId honored" branch): slskd ignores batchId and uses the remote-folder leaf, so the Wave-0/2 `curator-{app}-{id}` staging assumption did NOT match reality. The import/purge now target the real landing folder. (See key-decisions for the full set.)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] A2 staging-path mismatch: Wave-0/2 imported/purged the wrong folder**
- **Found during:** Task 3 (A2 reconciliation)
- **Issue:** `acquire_item` computed the staging dir as `staging_root/curator-{app}-{id}` and pointed `manual_import_candidates` + `purge_staging`/`quarantine_staging` at it. But slskd actually lands files at `staging_root/<leaf-of-remote-folder>` (A2, observed live). Left unfixed, the ManualImport would scan an empty/nonexistent dir and the purge would miss the real download — the loop would quarantine every real import.
- **Fix:** Added a neutral `TransferHandle.landing_dir_name` (the remote-folder leaf, derived in the slskd client via `_remote_folder_leaf`, splitting on both `\` and `/`). `acquire.py` resolves the staging dir from that handle leaf AFTER enqueue and points import + purge/quarantine at it; `curator-{app}-{id}` is now a leaf-less fallback only. Firewall preserved (no wire vocabulary in acquire; the literal token `folder` avoided even in docstrings, since the grep only strips `#` comments).
- **Files modified:** app/adapters/slskd.py, app/core/acquire.py, app/core/staging.py
- **Verification:** New e2e test `test_landing_dir_is_remote_folder_leaf` (deep `music\ZHU\BLACK MIDAS (2026)` peer path → import+purge under the `BLACK MIDAS (2026)` leaf, asserting no `curator-`/username in the staged path) + 3 slskd unit tests; firewall grep clean; full suite 205 passed.
- **Committed in:** fba83fb (A2 commit); the slskd.py seam additions landed in 94db977.

**2. [Rule 1 — Bug] A1 importMode casing/value + stray per-file downloadId**
- **Found during:** Task 3 (A1 reconciliation)
- **Issue:** The `[ASSUMED]` envelope used capital `"Move"` (the real field is lowercase) and included a per-file `downloadId` that the real captured POST does not carry. A wrong importMode casing risks Lidarr rejecting or mis-handling the command; the stray field is non-canonical.
- **Fix:** Set `importMode:"move"` (lowercase, the deliberate D-09 hardlink value), added top-level `replaceExistingFiles:false` + `sendUpdatesToClient:true` as captured, dropped per-file `downloadId`, kept the full QualityModel echo. Mirrored in readarr.py.
- **Files modified:** app/adapters/lidarr.py, app/adapters/readarr.py, app/tests/fixtures/manualimport/expected_post.json, app/tests/fixtures/manualimport/get_mapping.json, app/tests/test_lidarr_adapter.py
- **Verification:** `test_execute_import_posts_manualimport_move_per_file` strengthened (asserts `move`, top-level fields, and `downloadId` absence); readarr fault tests still green; suite 205 passed.
- **Committed in:** 0c5d786 (A1 commit).

---

**Total deviations:** 2 auto-fixed (both Rule 1 — correctness bugs surfaced by reconciling to the live observations).
**Impact on plan:** Exactly the reconciliation the plan's Task 3 called for. The A2 fix is the most consequential — without it every real import would have been quarantined. No scope creep, no architectural change, no weakened assertions (fixtures/constants moved to truth, then tests re-pinned; the lidarr test was strengthened).

## Issues Encountered

- **Firewall grep tripped on a docstring** — my first `_batch_label` docstring used the word "folder", which the firewall grep (`test_acquire_has_no_arr_field_names`) flagged because `_strip_comment` only strips `#` comments, not docstrings. Reworded to "remote directory"; grep clean. (Resolved during Task 3, no extra commit churn beyond the A2 commit.)
- **pytest summary line swallowed** — the `/Users/Oreo/.cache/claude-tmp` filesystem note held: pytest's final `N passed` summary line did not render. Worked around with `-o console_output_style=count`, which printed the authoritative `[205/205]` progress count (exit 0). Pass count reported via that count.

## User Setup Required

None for this reconciliation. The owner's NAS preconditions (D-11 shares confirmed, D-12 path-identity) were already applied and recorded in 04-05-LIVE-PROBE.md before this run. The first real autonomous end-to-end download is now production-trustworthy against the pinned shapes.

## Next Phase Readiness

- **Phase 4 COMPLETE — all 5 plans done.** The acquisition loop is pinned to the live slskd/Lidarr reality (A1/A2/A3) with the full offline suite green.
- Phase 5 (daemon/scheduling, backoff, SHARE-01/02 self-healing, detection batch-fsync perf) can now wrap `acquire_item` knowing the import/purge target the real landing folder and the ManualImport envelope matches production.

## Self-Check: PASSED

- All claimed files present on disk (slskd.py, lidarr.py, readarr.py, acquire.py, staging.py, the reconciled fixtures, the three test files, and 04-05-SUMMARY.md).
- All three task commits present in git: `94db977` (A3), `fba83fb` (A2), `0c5d786` (A1).
- Full offline suite green: 205 passed (was 201), exit 0; firewall grep over core/acquire.py clean.

---
*Phase: 04-acquisition-staging-clean-import*
*Completed: 2026-05-31*
