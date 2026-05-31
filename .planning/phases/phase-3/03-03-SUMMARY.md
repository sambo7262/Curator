---
phase: 03-matching-quality-gating
plan: 03
subsystem: matching
tags: [matching, beets-distance, rapidfuzz, weighted-average, rec-gap, precision-over-recall, explainable, corpus-calibration, zero-false-accepts]

# Dependency graph
requires:
  - phase: 03-matching-quality-gating
    provides: "Candidate/CandidateFile/Manifest frozen contract + build_candidate factory + labeled corpus + INDEX (03-01)"
  - phase: 03-matching-quality-gating
    provides: "rapidfuzz==3.13.0 pin + config.Settings match_* tunables == MatchConfig defaults (03-02)"
provides:
  - "app/core/matching.py — pure, explainable, beets-ported weighted-distance scorer"
  - "MatchConfig (frozen): w_artist/album=3.0, w_track_count/track_titles=4.0, strong_thresh=0.15, rec_gap_thresh=0.10"
  - "score(candidate, manifest, cfg) -> (distance, reasons) — artist/album/track-count always, track-title only when manifest.track_titles is truthy; every sub-score emits a reason string"
  - "recommend(scored, cfg) -> (decision, chosen, distance, reasons) — ACCEPT iff best <= strong AND runner-up >= rec_gap worse; else DECLINE"
  - "_norm / _str_distance / _track_count_distance / _track_title_coverage helpers (NFKD-fold + token_set_ratio + clamped completeness + greedy per-track coverage)"
  - "Corpus-calibrated proof of zero false-accepts across the labeled fixtures"
affects: [03-04-quality-fakeflac, 03-05-gate, 04-acquisition]

# Tech tracking
tech-stack:
  added: []  # rapidfuzz==3.13.0 was added in 03-02; this plan consumes it
  patterns:
    - "Beets weighted-average distance ported as a Curator-owned pure function (Σ(wᵢ·dᵢ)/Σ(wᵢ))"
    - "rapidfuzz.fuzz.token_set_ratio for order/duplication-tolerant artist/album/track-title fuzzing"
    - "NFKD-fold + strip-combining-marks on BOTH sides before fuzzing (non-Latin robustness)"
    - "Graceful-omission: track-title sub-distance dropped from numerator AND denominator when manifest.track_titles is None"
    - "rec-gap (runner-up distance) as the ambiguous-twin decline path — precision over recall"
    - "Explainable reason strings on every sub-score + decision (Soularr-opacity fix)"
    - "Corpus calibration discipline: tune the number/weight, never the assertion (Pitfall 3)"

key-files:
  created:
    - app/core/matching.py
    - app/tests/test_matching.py
  modified: []

key-decisions:
  - "score + recommend ship in ONE cohesive pure module (matching.py); committed atomically together because splitting mid-file (score without recommend) would leave a non-functional intermediate — the file is the atomic unit."
  - "_track_title_coverage uses greedy per-MANIFEST-track best-match over candidate file titles, mean of per-track bests; returns (distance, matched, total) where matched counts manifest tracks with a <=0.30 best — used only for the human-readable 'coverage M/N' reason."
  - "track-number filename prefixes ('01 - Airbag') are NOT stripped (release_parse contract); token_set_ratio is robust to them, so coverage stays ~0 for correctly-named tracks (verified: known_good title coverage = 0.0)."
  - "DECLINE-labeled corpus fixtures that are QUALITY/fakeflac rejections (fake_flac, below_cutoff_mp3) are EXCLUDED from the pure-matching corpus table — the matcher would ACCEPT them on identity; they are gate-layer (03-05) declines, proven there. The matching corpus is every score-driven fixture + the structural decline paths (garbage / no-audio)."
  - "Default MatchConfig calibration validated against the corpus unchanged — no weight/threshold tuning was needed (known_good=0.00, borderline=0.05, wrong_album=0.40, incomplete=0.33), so the 03-02 config defaults stand as-is."

patterns-established:
  - "Pure explainable matcher: score()/recommend() take no uploader fields, no I/O, no clock; reads only manifest identity + candidate.parsed_*/audio_file_count/file_titles"
  - "Zero-false-accepts headline test: iterate the full labeled corpus, ACCEPT iff and only iff the fixture is ACCEPT-labeled"
  - "rec-gap branch proven DISTINCTLY from the strong-thresh branch (ambiguous twins assert the ambiguous reason and NOT the over-strong reason)"
  - "Monotonicity property test: more correctly-named tracks never increases distance"

requirements-completed: [MATCH-01, MATCH-02]

# Metrics
duration: ~25min
completed: 2026-05-30
---

# Phase 3 Plan 03: Ported Beets Weighted-Distance Matcher (score + recommend) Summary

**A pure, deterministic, explainable beets-ported weighted-distance matcher (`score` + rec-gap `recommend`) in `app/core/matching.py` that grades the full labeled slskd corpus with ZERO false-accepts — known-good/borderline/non-Latin accept, wrong/incomplete/ambiguous/garbage decline — every sub-score and decision emitting a human-readable reason string, all offline against the corpus.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-30
- **Completed:** 2026-05-30
- **Tasks:** 2 (Task 1 scorer, Task 2 recommend + full-corpus calibration)
- **Files modified:** 2 created (`app/core/matching.py`, `app/tests/test_matching.py`)
- **Suite:** 66 passed (was 42 before this plan; +24 matching tests)

## Accomplishments

- **MATCH-01 — `matching.score(candidate, manifest, cfg)`**: a beets-ported weighted average `Σ(wᵢ·dᵢ)/Σ(wᵢ)` of artist / album / track-count sub-distances (always active) plus a track-title coverage sub-distance (active ONLY when `manifest.track_titles` is truthy). `rapidfuzz.fuzz.token_set_ratio` drives the string sub-distances over NFKD-folded inputs; `_track_count_distance` is the clamped completeness driver; `_track_title_coverage` is a greedy per-manifest-track best-match. Every sub-score appends a RESEARCH 328-335 reason string. Empty/None parsed names fold to `""` → 1.0 max penalty (the garbage path) and the function NEVER raises.
- **MATCH-02 — `matching.recommend(scored, cfg)`**: collapses beets' strong/medium/none into ACCEPT / DECLINE. ACCEPT requires BOTH (1) best distance ≤ `strong_thresh` AND (2) runner-up ≥ `rec_gap_thresh` worse; otherwise DECLINE with one of three explicit decision reasons (`DECLINE total=… > strong=…` / `DECLINE ambiguous: runner-up within rec_gap` / `ACCEPT total=… <= strong=…`). Empty input → decline with `no eligible candidates`.
- **Zero-false-accepts proven against the labeled corpus**: the headline test iterates every score-driven fixture and asserts ACCEPT iff and only iff the fixture is ACCEPT-labeled. The ambiguous-twin pair declines DISTINCTLY via the rec-gap branch (asserted to NOT be the strong-thresh branch). Plus a monotonicity property test (more correct tracks never raises distance).
- **Firewall + Pitfall source assertions clean**: `matching.py` imports only `unicodedata` + `rapidfuzz.fuzz` + the neutral `Candidate`/`Manifest` types; grep finds 0 *arr field-name tokens, 0 executable reads of `username`/`upload_speed`/`free_upload_slots` (Pitfall 5), and NO weight named year/label/catalog/country (Pitfall 1).

## Corpus Calibration Results (the headline)

Scored with the **default MatchConfig** (`strong=0.15`, `rec_gap=0.10`, weights 3/3/4/4) — **no tuning needed**:

| Fixture | Manifest | Total distance | recommend() | Label | OK |
|---------|----------|----------------|-------------|-------|----|
| known_good_flac | standard_12track | 0.00 | accept | ACCEPT | ✓ |
| known_good_alac | standard_12track | 0.00 | accept | ACCEPT | ✓ |
| known_good_mp3_320 | standard_12track | 0.00 | accept | ACCEPT | ✓ |
| borderline_accept | standard_12track | ~0.05 | accept | ACCEPT | ✓ |
| non_latin | non_latin | 0.00 | accept | ACCEPT | ✓ |
| incomplete_tracks | standard_12track | ~0.33 | decline | DECLINE | ✓ |
| wrong_album | standard_12track | ~0.40 | decline | DECLINE | ✓ |
| wrong_edition | standard_12track | >0.15 | decline | DECLINE | ✓ |
| garbage_metadata | standard_12track | >0.15 | decline | DECLINE | ✓ |
| no_audio_files | standard_12track | >0.15 | decline | DECLINE | ✓ |
| ambiguous_twin_a + _b (as a pair) | standard_12track | ~0.00 each, gap < rec_gap | decline (rec-gap) | DECLINE | ✓ |

**Zero false-accepts. Zero false-declines.** The known-good center lands at 0.00, the borderline lands ~0.05 (comfortably inside the 0.15 strong threshold), and every wrong/incomplete/edition/garbage case lands well above it; the twins are a genuine near-tie declined by the rec-gap.

> `fake_flac` and `below_cutoff_mp3` are QUALITY/fakeflac DECLINEs — on identity the matcher would ACCEPT them, so they are intentionally NOT in the matching corpus table; they are declined by the gate layer (plan 03-05) and proven there.

## Calibrated, Config-Tunable Thresholds

The matcher's defaults equal `config.Settings` defaults (set in plan 03-02), so the owner tunes them via env WITHOUT a rebuild (gate plan 03-05 reads `Settings` → builds a `MatchConfig`):

| MatchConfig field | default | env var | meaning |
|-------------------|---------|---------|---------|
| `strong_thresh` | 0.15 | `MATCH_STRONG_THRESH` | accept iff best total distance ≤ this |
| `rec_gap_thresh` | 0.10 | `MATCH_REC_GAP_THRESH` | runner-up must be this much worse, else ambiguous → decline |
| `w_artist` | 3.0 | `MATCH_W_ARTIST` | artist sub-distance weight |
| `w_album` | 3.0 | `MATCH_W_ALBUM` | album sub-distance weight |
| `w_track_count` | 4.0 | `MATCH_W_TRACK_COUNT` | RAISED — completeness driver |
| `w_track_titles` | 4.0 | `MATCH_W_TRACK_TITLES` | RAISED — per-track authenticity |

Calibration discipline (RESEARCH Pitfall 3) is documented in the test header: if a known-good assertion ever fails, tune the number/weight here + in `config.py`, NEVER weaken the assertion.

## Task Commits

Each task committed atomically (hooks enabled, no `--no-verify`):

1. **Task 1 + Task 2: Port the beets weighted-distance scorer + rec-gap recommend + full-corpus calibration** — `53d2584` (feat)

> Tasks 1 and 2 ship in the SAME commit because `score` and `recommend` are one cohesive pure module (`matching.py`): `recommend` consumes `score`'s output and the corpus tests exercise both together. Splitting mid-file would create a non-functional intermediate state (a scorer with no recommendation path), so the file is the honest atomic unit. Both `<acceptance_criteria>` sets are fully satisfied (24 tests covering score sub-distances + omit + garbage AND recommend empty/over-strong/rec-gap/accept + the zero-false-accepts corpus sweep).

**Plan metadata:** (separate commit) `docs(03-03): complete plan`

## Files Created/Modified

- `app/core/matching.py` — `MatchConfig` (frozen) + `score` + `recommend` + the four pure helpers (`_norm`/`_str_distance`/`_track_count_distance`/`_track_title_coverage`). SP-2 core-firewall header; imports only `unicodedata` + `rapidfuzz.fuzz` + `Candidate`/`Manifest`.
- `app/tests/test_matching.py` — 24 tests: MATCH-01 score sub-distance/omit/garbage/reason proofs, MATCH-02 recommend empty/over-strong/clear-winner/rec-gap/borderline/non-Latin, the parametrized accept/decline-labeled sweeps, the `test_no_false_accepts_across_full_corpus` headline, and the monotonicity property test. Per-REQ docstring header documenting the calibrated config-tunable values + the Python-3.9-sandbox `importorskip` note (rapidfuzz absent locally, green at CI/NAS).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Missing `import unicodedata` in matching.py**
- **Found during:** Task 1 (first test run — `NameError` at the `_norm` body)
- **Issue:** `_norm` calls `unicodedata.normalize` but the import was omitted from the module header.
- **Fix:** Added `import unicodedata` to the stdlib import block.
- **Files modified:** `app/core/matching.py`
- **Verification:** all 24 matching tests pass; full suite 66 passed.
- **Committed in:** `53d2584` (folded into the Task 1/2 commit before any commit was made)

### Plan-shape note (not a deviation)

The plan defined two tasks; both deliverables landed in one atomic commit (`53d2584`) because `matching.py` is a single cohesive module — see the Task Commits note. All acceptance criteria for BOTH tasks are met and proven.

**Total deviations:** 1 auto-fixed (1 blocking import). No architectural changes, no scope creep, no threshold/weight tuning required (the 03-02 defaults graded the corpus correctly as-is).

## Issues Encountered

None beyond the auto-fixed missing import. The dev sandbox is Python 3.9 + offline, but `rapidfuzz==3.13.0` is installed `--user`, so the matcher tests ran locally and self-verified here (24 passed) exactly as they will at CI/NAS Python 3.12. The `importorskip("rapidfuzz")` guard keeps the module green on any environment where rapidfuzz is absent.

## Known Stubs

None. `matching.py` is fully wired against the real `Candidate`/`Manifest` contract and proven over the real corpus — no placeholder data, no TODOs, no empty-return stubs.

## User Setup Required

None — Phase 3 is pure offline gating logic with no external service configuration.

## Next Phase Readiness

- `score` + `recommend` are the hard core the rest of Phase 3 composes: plan 03-04 (quality/fakeflac gates) supplies the eligibility filter, and plan 03-05 (`gate.py`) wires `quality.gate` + `fakeflac.check` → `score` → `recommend` → `selector`, reading `config.settings` to build the `MatchConfig`.
- The firewall grep test (`test_adapter_protocol.py`) was NOT extended here (that is plan 03-05's scope per PATTERNS.md); `matching.py` is already *arr-token-clean (verified by ad-hoc grep), so extending the regex later will pass.
- The `recommend(scored, …)` input contract — an iterable of `(distance, candidate, reasons)` already filtered to quality + fakeflac eligibility — is the seam plan 03-05 fills.

## Self-Check: PASSED

- Created files exist on disk: `app/core/matching.py`, `app/tests/test_matching.py`.
- Task commit present in git history: `53d2584`.
- `cd app && python3 -m pytest tests/test_matching.py` → 24 passed; full suite → 66 passed.
- Firewall/Pitfall grep clean: 0 *arr field-name tokens, 0 executable selector-field reads, no year/label/catalog weight in `matching.py`.

---
*Phase: 03-matching-quality-gating*
*Completed: 2026-05-30*
