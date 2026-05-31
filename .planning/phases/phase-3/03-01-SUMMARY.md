---
phase: 03-matching-quality-gating
plan: 01
subsystem: testing
tags: [dataclass, firewall, release-parse, regex, redos, fixtures, nyquist, slskd, quality-gating]

# Dependency graph
requires:
  - phase: 02-arr-firewall-ledger
    provides: GapItem frozen-neutral-dataclass idiom (SP-1), core firewall import discipline (SP-2), offline load_fixture conftest (SP-5), pure-unit test pattern (SP-6)
provides:
  - "Candidate/CandidateFile frozen neutral dataclasses (the Phase 3->4 contract) with audio_file_count/file_titles/audio_files() helpers"
  - "build_candidate/Candidate.from_slskd factory mapping slskd-shaped JSON into the contract types, .get()-defensive on every optional attr"
  - "Manifest frozen neutral dataclass (artist/album/track_count/track_titles|None/kind/year), reused for books"
  - "release_parse.parse() pure ReDoS-safe non-Latin-robust tokenizer -> ParsedRelease (artist/album/year/format/source/edition)"
  - "Labeled fixture corpus (14 candidates + 3 manifests + 2 profiles) + INDEX.md straddling the QUAL-02 cutoff in both directions"
affects: [03-02-matching, 03-03-quality, 03-04-fakeflac, 03-05-gate, 04-acquisition]

# Tech tracking
tech-stack:
  added: []  # stdlib only (re, unicodedata, dataclasses); rapidfuzz is a later-plan dep gated by human-verify
  patterns:
    - "Frozen neutral contract dataclass (SP-1) extended to Candidate/CandidateFile/Manifest"
    - "Anchored/bounded regexes (no nested quantifiers) as the structural ReDoS defense (T-03-01)"
    - "NFKD fold + strip-combining-marks for non-Latin robustness before tokenizing/fuzzing"
    - "slskd-result -> contract factory as the single Phase-3-owned mapping seam (.get()-defensive)"
    - "Labeled-corpus-with-INDEX: every fixture tagged to {manifest, profile, expected_decision} so calibration is proven, not asserted"

key-files:
  created:
    - app/core/candidate.py
    - app/core/manifest.py
    - app/core/release_parse.py
    - app/tests/test_release_parse.py
    - app/tests/fixtures/candidates/INDEX.md
    - app/tests/fixtures/candidates/*.json (14 candidates)
    - app/tests/fixtures/candidates/manifests/*.json (3)
    - app/tests/fixtures/candidates/profiles/*.json (2)
  modified: []

key-decisions:
  - "release_parse.py created in Task 2's scope but committed before candidate.py's runtime import resolves at module load — candidate.py imports it, so on a single sequential tree the working tree stays consistent; the factory (build_candidate/from_slskd) lives in candidate.py and was committed in Task 1, then exercised over the corpus in Task 3."
  - "build_candidate placed in candidate.py (Task 1) rather than added in Task 3, since from_slskd is part of the contract type's surface; Task 3 only authored the corpus + verified the factory."
  - "QualityRank ladder encoded as neutral ints in the profile fixtures (1=mp3-192 .. 5=flac); the rank_for() mapping itself is owned by plan 03-04 and intentionally NOT defined here (file ownership)."
  - "Edition token 'Remastered' matches the full word (alternation order Remastered|Remaster) — test asserts the actual tokenizer output, not a contrived stem."

patterns-established:
  - "SP-1 contract dataclass: Candidate/CandidateFile/Manifest are the only shapes crossing the Phase 3->4 firewall"
  - "ReDoS-safe tokenizer: all release_parse regexes anchored/bounded; adversarial 2000+ char folder asserted bounded-time"
  - "Boundary-straddle corpus (Nyquist): known_good_mp3_320 ACCEPT vs below_cutoff_mp3 DECLINE both against mp3_320_cutoff"

requirements-completed: [MATCH-01]

# Metrics
duration: 18min
completed: 2026-05-30
---

# Phase 3 Plan 01: Wave-0 Contract Dataclasses, Release Tokenizer & Labeled Corpus Summary

**Frozen neutral Candidate/CandidateFile/Manifest contract + a ReDoS-safe NFKD-folding release_parse tokenizer + a 14-case labeled slskd fixture corpus that straddles the QUAL-02 cutoff in both directions, all offline and *arr-free.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-05-30
- **Completed:** 2026-05-30
- **Tasks:** 3
- **Files modified:** 22 created (3 core modules, 1 test module, 14 candidate fixtures, 3 manifests, 2 profiles, 1 INDEX)

## Accomplishments
- Defined the Phase 3->4 contract: `Candidate`/`CandidateFile`/`Manifest` frozen neutral dataclasses with zero *arr field names, plus the `audio_file_count`/`file_titles`/`audio_files()` helpers and the `build_candidate`/`Candidate.from_slskd` slskd-result factory (`.get()`-defensive on every optional attr).
- Built `release_parse.parse()` — a pure, ReDoS-safe (anchored/bounded regexes, no nested quantifiers), non-Latin-robust (NFKD fold + strip combining marks) tokenizer that returns all-None on garbage/empty/non-str input and never raises.
- Authored the full labeled fixture corpus (14 candidates, 3 manifests, 2 profiles) with `INDEX.md` mapping every fixture to its `{manifest, profile, expected_decision, why}` — including the QUAL-02 permit-direction `known_good_mp3_320` (ACCEPT on `mp3_320_cutoff`) paired against the reject-direction `below_cutoff_mp3`, making the `mp3_320_cutoff` profile load-bearing.

## Task Commits

Each task was committed atomically (hooks enabled, no --no-verify):

1. **Task 1: Define Candidate/CandidateFile/Manifest contract dataclasses** - `c6202e8` (feat)
2. **Task 2: Pure release-name tokenizer + tests** - `6dd0d46` (feat, TDD test+impl)
3. **Task 3: Author labeled fixture corpus + INDEX (factory wired in Task 1)** - `986ca54` (feat)

**Plan metadata:** (this commit) `docs(03-01): complete plan`

## Files Created/Modified
- `app/core/candidate.py` - CandidateFile/Candidate frozen dataclasses + helpers + build_candidate/from_slskd slskd factory
- `app/core/manifest.py` - Manifest frozen dataclass (reused for books per RESEARCH §9)
- `app/core/release_parse.py` - pure ReDoS-safe NFKD-folding release-name tokenizer
- `app/tests/test_release_parse.py` - 9 tests (clean parse, noise-strip, source/edition, non-Latin fold, garbage, non-str, no-separator, adversarial ReDoS bound)
- `app/tests/fixtures/candidates/*.json` - 14 labeled slskd-shaped candidate cases (5 ACCEPT / 9 DECLINE)
- `app/tests/fixtures/candidates/manifests/*.json` - standard_12track, no_titles (graceful omission), non_latin
- `app/tests/fixtures/candidates/profiles/*.json` - lossless_only, mp3_320_cutoff (load-bearing)
- `app/tests/fixtures/candidates/INDEX.md` - the fixture->decision label table + Nyquist boundary-straddle note

## Decisions Made
- `build_candidate`/`from_slskd` live in `candidate.py` (committed Task 1) as part of the contract type's surface; Task 3 authored the corpus and verified the factory over it rather than re-introducing the factory.
- The neutral QualityRank ladder is encoded only as ints in the profile fixtures; `rank_for()` (ext+bitrate -> rank) is intentionally left to plan 03-04 to keep file ownership clean.
- conftest's `load_fixture` already accepts subpaths (`candidates/known_good_flac`), so no conftest edit was needed (SP-5 confirmed).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Separator normalization happened after the artist/album split**
- **Found during:** Task 2 (release_parse tests)
- **Issue:** Folder names using `_-_` as the separator (e.g. `The_Beatles_-_Abbey_Road`) failed to split into artist/album because `_`/`.` were only converted to spaces inside `_clean()` AFTER the `" - "` split, so the `" - "` separator was never seen.
- **Fix:** Moved underscore/dot-to-space normalization to BEFORE the `" - "` split in `parse()`.
- **Files modified:** app/core/release_parse.py
- **Verification:** `test_underscores_and_dots_treated_as_separators` now passes; all 9 release_parse tests green.
- **Committed in:** `6dd0d46` (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug). One test-assertion correction (asserted `remaster` where the alternation correctly yields `remastered`) was a wrong test expectation, not a code change.
**Impact on plan:** The fix is required for correct tokenization of a common slskd folder convention. No scope creep; no architectural change.

## Issues Encountered
None beyond the auto-fixed separator-ordering bug above. The dev sandbox is Python 3.9 + offline; release_parse imports no rapidfuzz, so its tests run locally and at CI/NAS identically.

## User Setup Required
None - no external service configuration required (Phase 3 is pure offline gating logic).

## Next Phase Readiness
- The Phase 3->4 contract types and the labeled corpus are in place; plans 03-02 (matching), 03-03 (quality), 03-04 (fakeflac), and 03-05 (gate) can now deserialize the corpus and assert the INDEX-labeled decisions.
- `rapidfuzz` is NOT yet added — the matching plan (03-02) must gate it behind a `checkpoint:human-verify` task (Phase-2 precedent) before importing it.
- The firewall grep test (`test_adapter_protocol.py`) was NOT extended here (that is plan 03-05's scope per PATTERNS.md); the new core modules are already *arr-token-clean (verified by ad-hoc grep), so extending the regex later will pass.

## Self-Check: PASSED

- All created files exist on disk (candidate.py, manifest.py, release_parse.py, test_release_parse.py, INDEX.md, 14 candidate + 3 manifest + 2 profile fixtures).
- All 3 task commits present in git history (c6202e8, 6dd0d46, 986ca54).
- 14 candidate fixtures confirmed; full test suite 42 passed; corpus JSON all valid; firewall grep clean on the 3 new core modules.

---
*Phase: 03-matching-quality-gating*
*Completed: 2026-05-30*
