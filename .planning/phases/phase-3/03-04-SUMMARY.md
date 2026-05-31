---
phase: 03-matching-quality-gating
plan: 04
subsystem: quality-gating
tags: [quality-gate, no-downgrade, fakeflac, firewall, dataclass, redos, corpus, qual-02, qual-03]

# Dependency graph
requires:
  - phase: 03-matching-quality-gating
    plan: 01
    provides: "Candidate/CandidateFile frozen contract + audio_files() + build_candidate factory + labeled fixture corpus (profiles lossless_only/mp3_320_cutoff, candidates below_cutoff_mp3/known_good_flac/known_good_mp3_320/fake_flac)"
  - phase: 03-matching-quality-gating
    plan: 02
    provides: "Settings.fakeflac_min_kbps (default 400) — the fakeflac.check floor the gate (03-05) passes in"
provides:
  - "core/quality.py: neutral frozen Profile(allowed, cutoff_rank) + integer QualityRank ladder + rank_for(ext, bitrate) + gate(candidate, profile)->(pass, reason) — the QUAL-01 firewall-clean side + the QUAL-02 no-downgrade pre-download gate"
  - "core/fakeflac.py: check(candidate, min_kbps=400)->(pass, reason) coarse non-spectral fake-FLAC heuristics (bytes/sec floor, claimed-lossy-bitrate, lossy source token) with data-absent->SKIP branches (QUAL-03)"
  - "_has_lossy_source_token: bounded ReDoS-safe folder-name lossy-marker detector"
affects: [03-05-gate, 04-acquisition]

# Tech tracking
tech-stack:
  added: []  # stdlib only (dataclasses, re, typing); no rapidfuzz import in either module
  patterns:
    - "Firewall-clean core gate (SP-2): inputs are only the neutral Candidate + plain ints/Profile; zero *arr vocabulary (verified by grep)"
    - "Neutral QualityRank ladder as integer constants the adapter normalizes *arr quality names onto (the profile fixtures encode the same ints)"
    - "Separation of concerns: the format gate trusts the extension for losslessness; detecting re-wrapped lossy is fakeflac's job, not the rank ladder's"
    - "Data-absent -> SKIP (Pitfall 4): every fakeflac sub-check guarded by `if f.length_seconds:` / `if f.bitrate_kbps` so missing attrs never false-reject genuine FLAC"
    - "Word-bounded ReDoS-safe alternation for the lossy-source-token check (no nested quantifiers; 'web-dl' not bare 'web')"

key-files:
  created:
    - app/core/quality.py
    - app/core/fakeflac.py
    - app/tests/test_quality.py
    - app/tests/test_fakeflac.py
  modified: []

key-decisions:
  - "Lossless extensions (flac/alac/wav/ape/m4a) all collapse to a single RANK_LOSSLESS (=RANK_FLAC=5) for GATING. The profile fixtures distinguish ALAC=4/FLAC=5, but any lossless file is at/above a lossless cutoff and inside an allowed {4,5}, so collapsing keeps the no-downgrade gate correct AND avoids false-rejecting ALAC (.m4a) against a FLAC-cutoff profile. rank_for never returns 4 — that rank exists only as an allowed-set member the gate compares against."
  - "rank_for('mp3', None) -> None (conservative): an unknown-bitrate lossy file cannot be assumed to meet a 320 cutoff (RESEARCH 350), so the gate rejects it as not-in-allowed rather than guessing."
  - "A claimed-low bitrate on a FLAC is NOT demoted in rank_for — that would conflate the format gate with fake-FLAC detection. fakeflac.check owns re-wrapped-lossy detection; rank_for trusts the extension for losslessness."
  - "The folder-level lossy-source-token check only fires when the candidate actually has FLAC files (a clean MP3 folder named '... 320' is not a fakeflac concern)."

patterns-established:
  - "Both gates are pure, offline, *arr-token-clean core modules consuming only the neutral contract — same firewall idiom as gap_detector.py / candidate.py"
  - "Corpus-calibrated assertions: both QUAL-02 directions + the fake/genuine FLAC split assert the INDEX.md labels, never invented outcomes (Pitfall 3 — tune the ladder/floor, never the test)"

requirements-completed: [QUAL-01, QUAL-02, QUAL-03]

# Metrics
duration: 12min
completed: 2026-05-30
---

# Phase 3 Plan 04: Quality Gate + Coarse Fake-FLAC Heuristics Summary

**A firewall-clean neutral Profile/QualityRank + a no-downgrade pre-download quality gate proven in BOTH QUAL-02 directions (rejects below-cutoff, permits profile-acceptable lossy) plus coarse non-spectral fake-FLAC heuristics that decline re-wrapped lossy without ever false-rejecting genuine or missing-attr FLAC.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-30
- **Completed:** 2026-05-30
- **Tasks:** 2 (both TDD: test -> impl)
- **Files modified:** 4 created (2 core modules, 2 test modules)

## Accomplishments
- `core/quality.py`: a frozen neutral `Profile(allowed: frozenset[int], cutoff_rank: int)` (SP-1), an integer `QualityRank` ladder (MP3-192=1 .. FLAC=5), `rank_for(ext, bitrate)` (lossless extensions -> lossless rank bitrate-agnostically; lossy by bitrate bucket; unknown/absent-bitrate-lossy -> None), and `gate(candidate, profile) -> (pass, reason)` verbatim per RESEARCH 442-451 — evaluates EVERY audio file, one below-cutoff/not-allowed file fails the whole candidate (QUAL-02 no-downgrade), reasons per RESEARCH 334. Zero *arr vocabulary (QUAL-01 firewall side).
- `core/fakeflac.py`: `check(candidate, min_kbps=400) -> (pass, reason)` with the three coarse heuristics (bytes/sec floor, claimed-lossy-bitrate bucket, lossy source token) and the critical data-absent -> SKIP branches, plus `_has_lossy_source_token` (bounded, ReDoS-safe). Default floor matches `Settings.fakeflac_min_kbps`.
- Corpus-driven tests: `test_quality.py` (12) proves both QUAL-02 directions + the rank ladder; `test_fakeflac.py` (10) proves fakes declined, genuine FLAC passes, each heuristic independently, and the HEADLINE missing-length FLAC passes (Pitfall 4).

## QUAL-02 both-directions proof
- **REJECT (no-downgrade, T-03-08):** `gate(below_cutoff_mp3, lossless_only)` -> `(False, "quality REJECT: ... .mp3 not in profile allowed set")`. MP3-192 (rank 1) is outside lossless_only's allowed `{4,5}` AND below its cutoff rank 5. Also rejected against `mp3_320_cutoff` (rank 1 < cutoff 3).
- **PERMIT (T-03-12 over-rejection DoS guard):** `gate(known_good_mp3_320, mp3_320_cutoff)` -> `(True, "quality OK: all audio files >= cutoff")`. MP3-320 (rank 3) is inside allowed `{3,4,5}` and AT the cutoff rank 3 — a profile-acceptable lossy candidate PASSES when the cutoff allows it. This makes the `mp3_320_cutoff` profile fixture load-bearing (consumed by an asserting test, not dead). Genuine FLAC and ALAC both pass `lossless_only`.

## Fake-FLAC results
- `check(fake_flac)` -> `(False, "fakeflac REJECT: ... effective 128 kbps < 400 floor")` — the bytes/sec floor (primary signal) fires on the ~128 kbps effective bitrate. (The fixture also carries a lossy source token, so two heuristics agree.)
- Each heuristic proven independently: a token-free low-bytes/sec FLAC fails on the floor; a high-bytes/sec FLAC claiming bitRate 320 fails on the claimed-bitrate check; a high-bytes/sec FLAC in a `(web-dl from spotify)` folder fails on the token check.
- **HEADLINE (Pitfall 4 / T-03-09):** a FLAC with `length_seconds=None` and a tiny size (which WOULD fail the floor if length were known) PASSES — the bytes/sec sub-check is skipped on absent data, never a rejection on a None input. A FLAC with `bitRate=None` likewise passes. `known_good_flac` (~800-900 kbps) passes; a clean MP3-320 candidate is not a fakeflac concern.

## Task Commits

Each task committed atomically (hooks enabled, no --no-verify):

1. **Task 1: Profile + QualityRank ladder + cutoff/allowed gate (quality.py)** - `d751391` (feat, TDD test+impl)
2. **Task 2: Coarse fake-FLAC heuristics (fakeflac.py) + tests for both gates** - `8eb8feb` (feat, TDD test+impl)

**Plan metadata:** (this commit) `docs(03-04): complete plan`

## Files Created/Modified
- `app/core/quality.py` - neutral Profile + QualityRank ladder + rank_for + no-downgrade gate (QUAL-01/02)
- `app/core/fakeflac.py` - coarse non-spectral fake-FLAC check + _has_lossy_source_token (QUAL-03)
- `app/tests/test_quality.py` - 12 tests (rank ladder + both QUAL-02 directions + every-file evaluation)
- `app/tests/test_fakeflac.py` - 10 tests (each heuristic + genuine/missing-attr FLAC pass + token unit)

## Decisions Made
- Lossless extensions collapse to one gating rank (RANK_LOSSLESS=5); ALAC=4 exists only as an allowed-set member, so ALAC (.m4a) is never false-rejected against a FLAC-cutoff profile that allows `{4,5}`. See key-decisions for the full rationale.
- `rank_for` does not demote a FLAC on a claimed-low bitrate — losslessness is the extension's domain; re-wrapped-lossy detection is fakeflac's. Keeping them separate avoids the format gate double-counting fake-FLAC signals.
- Unknown-bitrate lossy -> None (conservative reject), not a guessed bucket.

## Deviations from Plan
None - plan executed exactly as written. Both tasks followed TDD (failing test -> minimal impl -> green); no auto-fixes (Rules 1-3) were needed and no architectural decisions (Rule 4) arose. No authentication gates (Phase 3 is pure offline gating logic).

## Issues Encountered
None. The dev sandbox is Python 3.9 + offline; neither module imports rapidfuzz, so both test suites run locally and at CI/NAS identically. Baseline suite was 66 passing; after this plan the full suite is 88 passing (66 + 12 quality + 10 fakeflac).

## User Setup Required
None - no external service configuration required (Phase 3 is pre-download offline gating logic).

## Next Phase Readiness
- `quality.gate` and `fakeflac.check` are the two pure pre-download defenses plan 03-05's composing `gate.py` orchestrates (after matching + selection). 03-05 owns: the adapter `get_quality_profile` normalization (*arr profile JSON -> neutral Profile), wiring `settings.fakeflac_min_kbps` into `check`, and extending the firewall grep test (`test_adapter_protocol.py`) to cover `core/quality.py` + `core/fakeflac.py` — both are already *arr-token-clean (verified by the plan's verification grep), so extending the regex will pass.
- The neutral QualityRank ints (1..5) are the contract the adapter maps Lidarr quality names onto; the gate compares against `profile.allowed`/`profile.cutoff_rank` only.

## Self-Check: PASSED

- All created files exist on disk (core/quality.py, core/fakeflac.py, tests/test_quality.py, tests/test_fakeflac.py).
- Both task commits present in git history (d751391, 8eb8feb).
- Full suite 88 passed; firewall grep clean on both new core modules; both QUAL-02 directions and the missing-attr FLAC guarantee asserted against the labeled corpus.

---
*Phase: 03-matching-quality-gating*
*Completed: 2026-05-30*
