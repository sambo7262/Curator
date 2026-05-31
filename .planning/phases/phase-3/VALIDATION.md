---
phase: 3
phase_slug: matching-quality-gating
created: 2026-05-30
status: active
---

# Phase 3 Validation Strategy

> **Purpose:** Define HOW Phase 3 requirements will be validated before planning.
> Generated from RESEARCH.md "Validation Architecture" section.
> Consumed by gsd-planner (to add validation tasks) and gsd-verify-work (to check coverage).

## Validation Levels

| Level | Method | When to Use | Cost |
|-------|--------|-------------|------|
| **1. Static** | grep firewall test, type/contract checks | *arr field names must not leak into core/ | Free |
| **2. Unit** | Pure-function tests over labeled fixtures | Matcher, quality gate, fake-FLAC, release-name parser | Cheap |
| **3. Integration** | Adapter normalization tests | `get_quality_profile`/`get_manifest` return normalized shapes | Medium |
| **4. E2E** | N/A this phase | No slskd search/download/import until Phase 4 | — |
| **5. Manual** | N/A | No UX surface; matcher is headless | — |

**Phase 3 is gating logic only.** It operates on candidate *data structures* (the `Candidate`/`CandidateFile` dataclasses), never on live Soulseek. All validation is offline, deterministic, pure-function — mirroring the Phase-2 offline-fixture pattern. No network, no E2E, no manual.

## Requirement Validation Map

```
REQUIREMENT: MATCH-01 — score slskd candidates on authoritative identity (incl. track-count completeness)
  Level: 2 (Unit)
  Method: matcher.score(candidate, manifest) over the labeled corpus; assert best-candidate selection
  Success Signal: known_good_* scores below threshold (ACCEPT); incomplete_* penalized by track-count gap
  Automension: Automated

REQUIREMENT: MATCH-02 — reject candidates below a configurable confidence threshold (precision over recall)
  Level: 2 (Unit)
  Method: matcher.decide() over ambiguous_*, wrong_album_*, wrong_edition_* fixtures
  Success Signal: ZERO false-accepts across the corpus; ambiguous (just-below-strong) → DECLINE; rec-gap-to-runner-up enforced
  Automension: Automated

REQUIREMENT: QUAL-01 — read the item's *arr quality profile and cutoff via the adapter
  Level: 3 (Integration) + 1 (Static)
  Method: adapter returns a normalized Profile/cutoff; firewall grep proves no *arr JSON keys in core/
  Success Signal: Profile fields present in core as neutral types; grep finds 0 forbidden *arr tokens in app/core + app/state
  Automension: Automated

REQUIREMENT: QUAL-02 — filter candidates by format/bitrate BEFORE download, never below cutoff (no downgrades)
  Level: 2 (Unit)
  Method: quality gate over below_cutoff_* fixtures (e.g. MP3 when FLAC required)
  Success Signal: every below-cutoff candidate DECLINED before any download path is reached
  Automension: Automated

REQUIREMENT: QUAL-03 — heuristic fake/transcoded-FLAC checks before accepting a FLAC candidate
  Level: 2 (Unit)
  Method: fakeflac check over fake_flac_* (and genuine FLAC) fixtures
  Success Signal: fakes (bytes/sec floor, claimed-bitrate insanity, bad source tag) DECLINED; genuine FLAC passes; missing-attr FLAC NOT false-rejected
  Automension: Automated
```

## Nyquist Sampling Check

> The matcher is a pure decision function over a *distribution* of candidate qualities. Sampling one good + one bad is undersampling — the corpus must straddle the decision boundary (just-above and just-below threshold) so calibration is *proven*, not asserted.

Per component:

- **Happy path** — `known_good_*` (complete, correct, profile-OK) → ACCEPT; `borderline_accept_*` (just above strong) → ACCEPT.
- **Boundary** — `ambiguous_*` (just below strong) → DECLINE; `incomplete_*` (1 missing track vs many) → DECLINE; `below_cutoff_*` at the cutoff edge.
- **Error path** — `garbage_meta_*` → DECLINE and NEVER throw; missing-attribute FLAC → must NOT false-reject; `non_latin_*` → ACCEPT when correct (unicode/transliteration robustness).
- **Concurrent** — N/A (pure function, no shared state, no I/O).
- **Recovery** — N/A (no in-flight state in Phase 3).

## Coverage Targets

- **Critical paths (must-work):**
  - No false-accept anywhere in the corpus (precision-over-recall is the headline guarantee).
  - Every below-cutoff candidate declined before download (no-downgrade guarantee).
  - Fake-FLAC declined; genuine FLAC (incl. missing-attr) not false-rejected.
  - Firewall intact: 0 *arr field names in app/core + app/state.
- **Target coverage:** 100% of the labeled fixture classes exercised; 100% of the matcher/quality/fakeflac pure-function branches; every ROADMAP Phase-3 success criterion maps to ≥1 fixture with an asserted expected decision.
- **Explicitly NOT testing:** spectral/frequency FLAC analysis (QUAL-04, v2); live slskd search/download/import (Phase 4); selection heuristics beyond "matching ≠ selection" separation (uploader speed/slots is dumb/swappable, Phase 4); grace-window/Usenet-race gating (Phase 5).

## Validation Tasks

These become tasks in the implementation plan (Wave 0 corpus FIRST — test-first per owner directive):

- [ ] **Wave 0:** Build the labeled fixture corpus under `app/tests/fixtures/` — `known_good_*`, `incomplete_*`, `wrong_edition_*`, `wrong_album_*`, `fake_flac_*`, `below_cutoff_*`, `non_latin_*`, `garbage_meta_*`, `ambiguous_*`, `borderline_accept_*`, each tagged with expected decision. Define the `Candidate`/`CandidateFile`/`Manifest`/`Profile` dataclasses (the Phase 3→4 contract).
- [ ] Unit tests for `matcher.score()` / `decide()` over the full corpus — assert best-candidate selection (MATCH-01) and zero false-accepts + rec-gap (MATCH-02).
- [ ] Unit tests for the quality gate over `below_cutoff_*` — all declined pre-download (QUAL-02).
- [ ] Unit tests for the fake-FLAC check over `fake_flac_*` + genuine/missing-attr FLAC (QUAL-03).
- [ ] Integration test: adapter `get_quality_profile`/`get_manifest` return normalized `Profile`/`Manifest` (QUAL-01).
- [ ] Static: extend the Phase-2 comment-aware firewall grep to the new core modules — 0 *arr tokens in app/core + app/state (QUAL-01 firewall).
- [ ] Threshold/weight calibration against the corpus (not in the abstract) — document chosen `strong`/`rec_gap`/weights and that they are config-tunable.

---

*Generated from RESEARCH.md Validation Architecture. Update if requirements change.*
