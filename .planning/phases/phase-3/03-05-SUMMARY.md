---
phase: 03-matching-quality-gating
plan: 05
subsystem: gating-integration
tags: [gate, selector, composition, adapter-normalization, firewall, qual-01, match-01, match-02, qual-02, qual-03, end-to-end, corpus]

# Dependency graph
requires:
  - phase: 03-matching-quality-gating
    plan: 01
    provides: "Candidate/CandidateFile frozen contract + audio_files() + build_candidate factory; Manifest neutral type; labeled fixture corpus (profiles lossless_only/mp3_320_cutoff, manifests standard_12track/no_titles/non_latin, 14 candidates + INDEX.md labels)"
  - phase: 03-matching-quality-gating
    plan: 02
    provides: "Settings.match_* + Settings.fakeflac_min_kbps tunables (gate builds MatchConfig + passes the fakeflac floor from these)"
  - phase: 03-matching-quality-gating
    plan: 03
    provides: "matching.score(candidate, manifest, cfg)->(dist, reasons) + recommend(scored, cfg)->(decision, chosen, dist, reasons) + MatchConfig"
  - phase: 03-matching-quality-gating
    plan: 04
    provides: "quality.Profile + gate(candidate, profile)->(ok, reason) + rank_for; fakeflac.check(candidate, min_kbps)->(ok, reason)"
provides:
  - "core/gate.py: frozen GateResult(decision, chosen, distance, reasons) + evaluate(candidates, manifest, profile, cfg=None, min_kbps=None) — the single Phase-3 composition point (eligibility-before-acceptance: quality.gate + fakeflac.check -> matching.score -> recommend -> selector)"
  - "core/selector.py: select(accepted)->Candidate|None — the dumb/swappable best-pick and the ONLY reader of upload_speed/free_upload_slots/username (matching != selection)"
  - "adapters get_quality_profile(id)->Profile + get_manifest(foreign_id)->Manifest on lidarr (primary, raise_for_status) + readarr (best-effort, degrade-to-safe-default); ArrAdapter Protocol updated to the neutral return types"
  - "extended firewall grep (ARR_FIELD_NAMES) covering all 8 core modules with 0 forbidden *arr tokens"
affects: [04-acquisition]

# Tech tracking
tech-stack:
  added: []  # stdlib + the already-present rapidfuzz (transitively via matching); no new deps
  patterns:
    - "Single-composition-point in core (SP-2): gate.evaluate mirrors gap_detector.detect_gaps — one function wires the pure sub-gates into a verdict"
    - "Eligibility-before-acceptance: quality + fakeflac filter BEFORE matching scores, so a below-cutoff / fake-FLAC candidate is structurally unable to slip past as a good match"
    - "matching != selection (Pitfall 5): selector is the lone uploader-field reader, called ONLY after recommend() accepts; proven by an attribute-access source grep in test_gate.py"
    - "Normalize-behind-firewall (SP-3): adapters map *arr profile/manifest JSON -> neutral Profile/Manifest with all *arr names + a local quality-name->rank map confined to the adapter"
    - "Best-effort degrade (ARR-02): readarr profile/manifest faults swallow to an empty-allowed Profile / stub-safe book Manifest, never raising into the loop, never gating music"

key-files:
  created:
    - app/core/gate.py
    - app/core/selector.py
    - app/tests/test_gate.py
    - app/tests/test_selector.py
  modified:
    - app/core/matching.py        # reword one docstring line to drop .username dot-notation (firewall-grep clean)
    - app/adapters/base.py        # import neutral Profile/Manifest; Protocol get_quality_profile->Profile + add get_manifest->Manifest
    - app/adapters/lidarr.py      # get_quality_profile + get_manifest impls + local *arr-quality-name->rank map
    - app/adapters/readarr.py     # best-effort get_quality_profile + get_manifest over a book-format ladder
    - app/tests/test_lidarr_adapter.py    # neutral-type + allowed/cutoff mapping + manifest tests
    - app/tests/test_readarr_adapter.py   # book-format profile + degrade-on-fault + manifest tests
    - app/tests/test_adapter_protocol.py  # extend ARR_FIELD_NAMES + assert new callable methods

key-decisions:
  - "gate.evaluate snapshots the accept set at the BEST distance (best + 1e-9 epsilon) and hands it to selector. recommend() already declines genuine rec-gap near-ties, so on accept there is a single sub-strong unambiguous winner; selector ratifies it (and would tie-break a true multi-accept set). This keeps matching!=selection clean — selector never re-judges the match, only picks the copy."
  - "Two near-identical copies of the SAME correct album are a genuine rec-gap AMBIGUOUS decline (not an accept). The original plan's test_selector_picks_best_among_multiple_accepts wrongly assumed they'd accept; replaced with test_clear_winner_accepts_over_a_worse_eligible (clear winner + a worse-but-eligible runner-up accepts the winner) + a dedicated test_selector.py unit-testing the tie-break ladder directly. This is faithful to the corpus reality, not a weakened assertion."
  - "The 'cutoff' firewall token is matched ONLY as a quoted JSON key (\"cutoff\"), not the bare English word. core/quality.py's reason strings ('below cutoff', 'cutoff met') and the neutral Profile.cutoff_rank field are legitimate prose/identifiers; requiring the quotes targets the *arr JSON-key access (body.get(\"cutoff\")) and avoids false-positives. Likewise \"allowed\"/\"items\" are quoted-key forms (the neutral Profile.allowed attr is unquoted)."
  - "lidarr.get_quality_profile resolves cutoff_rank from the cutoff quality id via a local id->rank map, falling back to min(allowed) when the cutoff id is unresolvable — so a partial/A4-unconfirmed profile shape still yields a usable floor instead of crashing."
  - "Lidarr quality names map through a LOCAL _LIDARR_QUALITY_RANKS dict (mp3-320/v0->RANK_MP3_320, flac/wav/ape->RANK_FLAC, alac->RANK_ALAC, ...); an unrecognized name -> None and is omitted from the allowed set (conservative). The *arr quality-name vocabulary lives only in lidarr.py."

# Metrics
metrics:
  duration: ~40m
  tasks: 3
  tests_added: 40   # suite 88 -> 128
  commits: 3
  completed: 2026-05-30
---

# Phase 3 Plan 05: Gate Composition, Dumb Selector & Adapter Normalization Summary

Closed Phase 3 by wiring the four pure gates into a single `gate.evaluate(candidates, manifest, profile) -> GateResult` (eligibility-before-acceptance: quality + fakeflac filter, then matching scores/recommends, then the dumb selector picks the copy), adding the swappable `selector.select` as the lone uploader-field reader (matching != selection), implementing the adapter `get_quality_profile`/`get_manifest` normalization that surfaces *arr profile + manifest as neutral `Profile`/`Manifest` (QUAL-01), and extending the locked firewall grep over all 8 new core modules. The full labeled corpus grades end-to-end exactly to its INDEX.md labels, with QUAL-02 proven in BOTH directions.

## What was built (per task)

**Task 1 — selector.py + gate.py + end-to-end test_gate.py (commit `aab4404`):**
- `core/selector.py`: `select(accepted) -> Candidate | None`, a ~20-line deterministic tie-break ladder — distance asc (primary, never overridden) -> format preference (lossless before mp3-320) -> free_upload_slots desc -> upload_speed desc. The ONLY module reading `candidate.upload_speed/free_upload_slots/username` (Pitfall 5). None attrs sort last (treated as 0), never crash.
- `core/gate.py`: frozen `GateResult(decision, chosen, distance, reasons)` + `evaluate(...)` mirroring `detect_gaps`' single-composition shape. For each candidate: `quality.gate` then `fakeflac.check` (eligible iff BOTH pass; ineligibles recorded with reason + excluded from scoring), then `matching.score` the eligibles, `matching.recommend` for the decision, and on accept `selector.select` over the best-distance accept set. Reads `config.settings` to build `MatchConfig` + the fakeflac floor (SP-4, env-tunable, no rebuild).
- `tests/test_gate.py`: drives `evaluate` over every corpus fixture using its INDEX-paired manifest+profile; asserts the LABELED decision, chosen-iff-accept, non-empty reasons always; plus targeted stage proofs and the matching!=selection source grep.
- `matching.py`: reworded one docstring line to drop `.username` dot-notation so the source grep is clean.
- `tests/test_selector.py`: unit-tests the tie-break ladder (distance primary, format, slots, speed, None-safety).

**Task 2 — adapter normalization (commit `efad5b1`):**
- `base.py`: imports the neutral `Profile`/`Manifest`; promotes `get_quality_profile` to `-> Profile` and adds `get_manifest -> Manifest` on the `ArrAdapter` Protocol.
- `lidarr.py`: `get_quality_profile` GETs `qualityprofile/{id}`, maps `allowed` items through a LOCAL `_LIDARR_QUALITY_RANKS` name->rank dict to `Profile(allowed, cutoff_rank)` (cutoff resolved from the cutoff quality id, `.get()`-defensive, raise_for_status primary); `get_manifest` maps the album record (artist/title/track list) to `Manifest`, with `track_titles=None` graceful when no track list is present. All *arr/MB keys stay local.
- `readarr.py`: best-effort impls over a book-format ladder (PDF<MOBI<AZW3<EPUB); ANY HTTP/JSON/shape fault degrades to an empty-allowed `Profile` / stub-safe book `Manifest` (never raises, never gates music — ARR-02).
- adapter tests assert the neutral return TYPES + allowed-set/cutoff mapping + Readarr degrade-on-fault.

**Task 3 — extend the locked firewall (commit `89b4b18`):**
- `ARR_FIELD_NAMES` extended with the Pitfall-2 quality-profile-JSON leaks: `qualityProfileId`, `items[`/`"items"`, `"allowed"`, `"cutoff"` (quoted-key forms targeting *arr JSON-key access, admitting the neutral `cutoff_rank`/`allowed` identifiers + the gate's English reason prose).
- `test_both_satisfy_protocol` now asserts both concrete adapters expose callable `get_quality_profile` + `get_manifest`.

## QUAL-02 both-directions end-to-end proof

Proven through the COMPOSED gate (not just the quality module in isolation):
- **REJECT direction** — `evaluate([below_cutoff_mp3], standard_12track, lossless_only)` -> `decline`, and the reason trail shows the candidate was `excluded` at the QUALITY stage with `quality REJECT` (eligibility-before-acceptance: it was never even scored). `test_declines_below_cutoff`.
- **PERMIT direction** — `evaluate([known_good_mp3_320], standard_12track, mp3_320_cutoff)` -> `accept` with `chosen` set; the candidate passed the quality stage (`eligible` in the reason trail) and matched end-to-end. `known_good_mp3_320` differs from `known_good_flac` ONLY in format, so this proves the cutoff/rank ladder does not over-reject a profile-acceptable lossy candidate (T-03-12 DoS guard). `test_accepts_mp3_320_when_cutoff_allows`.

Full corpus headline (`test_no_false_accepts_across_full_corpus`): all 12 evaluated fixtures grade to their INDEX label end-to-end — accepts (known_good_flac/alac/mp3_320, borderline_accept, non_latin) with chosen set; declines split across the quality stage (below_cutoff_mp3), fakeflac stage (fake_flac), and match stage (incomplete_tracks/wrong_album/wrong_edition/garbage_metadata/no_audio_files); ambiguous twins decline via rec-gap. Zero false-accepts.

## Firewall-grep result

`test_core_state_have_no_arr_field_names` passes over all 8 core modules (gate, selector, matching, quality, fakeflac, candidate, manifest, release_parse) + state: **0 forbidden *arr tokens**. Negative control confirmed both ways — a deliberate `qualityProfileId` inserted into `core/selector.py` failed the grep (the regex matches the new token), then reverted; and the matching!=selection source grep (`test_selector_only_reads_uploader_fields`) confirms the uploader fields are read (attribute access) ONLY in `core/selector.py`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Corrected an unrealistic multi-accept selector test premise**
- **Found during:** Task 1 (test_gate.py first run)
- **Issue:** The plan's `test_selector_picks_best_among_multiple_accepts` assumed two near-identical copies of the same correct album (known_good_flac + known_good_alac) would ACCEPT and let selector pick between them. In reality `recommend()` correctly DECLINES that pair as a rec-gap near-tie (two equally-good matches IS ambiguous) — so the gate returns `decline`, not `accept`. The premise contradicted the matcher's MATCH-02 contract.
- **Fix:** Replaced it with `test_clear_winner_accepts_over_a_worse_eligible` (a clear winner + a worse-but-quality-eligible runner-up accepts the winner and runs selector once) and added a dedicated `tests/test_selector.py` that unit-tests the tie-break ladder directly (distance primary, format, slots, speed, None-safety). This proves the accept->select path AND the selector ladder faithfully, without weakening any corpus assertion.
- **Files modified:** app/tests/test_gate.py, app/tests/test_selector.py (new)
- **Commit:** `aab4404`

**2. [Rule 1 - Bug] Removed a uploader-field read from gate.py's candidate label**
- **Found during:** Task 1 (the matching!=selection source-grep test caught it)
- **Issue:** `gate.evaluate` initially built a candidate label as `cand.folder or cand.username or "<candidate>"`, which READS `candidate.username` in core/gate.py — a matching!=selection (Pitfall 5 / T-03-06) violation that the source grep correctly flagged.
- **Fix:** Changed the label to `cand.folder or "<candidate>"` so the only uploader-field reads in core remain in selector.py.
- **Files modified:** app/core/gate.py
- **Commit:** `aab4404`

**3. [Rule 3 - Blocking] Reworded a matching.py docstring to keep the source grep clean**
- **Found during:** Task 1
- **Issue:** matching.py's docstring used `candidate.username / upload_speed / free_upload_slots` dot-notation, which the attribute-access source grep would have flagged as a uploader-field read outside selector.py.
- **Fix:** Reworded to "the uploader identity/slots/speed fields (Pitfall 5 — selector-only)" — same meaning, no dot-notation. (The test also comment-strips, but removing the dot-notation entirely is the cleaner fix.)
- **Files modified:** app/core/matching.py
- **Commit:** `aab4404`

### Firewall-token scoping (Task 3, within plan guidance)
The plan flagged that the `cutoff` token must not false-positive on the neutral `cutoff_rank`. The corpus reality is stronger: `core/quality.py` ALSO emits user-facing reason strings ("below cutoff", "cutoff met"). So the token was scoped to the quoted JSON-key form `"cutoff"` (and likewise `"allowed"`/`"items"`) rather than `\bcutoff\b(?!_rank)` — this targets the *arr JSON-key access (`body.get("cutoff")`, `profile["items"][i]["allowed"]`) while admitting both the neutral identifier and the English prose. Verified by an explicit negative-control suite.

## Self-Check: PASSED

- Created files all exist: app/core/gate.py, app/core/selector.py, app/tests/test_gate.py, app/tests/test_selector.py, .planning/phases/phase-3/03-05-SUMMARY.md.
- All three task commits exist on the branch: `aab4404` (gate+selector), `efad5b1` (adapter normalization), `89b4b18` (firewall extension).
- Full suite: 128 passed.

## Verification

- `cd app && python3 -m pytest tests/test_gate.py tests/test_selector.py tests/test_adapter_protocol.py tests/test_lidarr_adapter.py tests/test_readarr_adapter.py` -> 55 passed.
- `cd app && python3 -m pytest` -> **128 passed** (was 88 at plan start; +40 new tests).
- QUAL-02 both directions: `test_declines_below_cutoff` + `test_accepts_mp3_320_when_cutoff_allows` pass.
- Firewall: `test_core_state_have_no_arr_field_names` = 0 forbidden tokens across all 8 new core modules; negative control confirmed.
- `grep -rn '\.upload_speed\|\.free_upload_slots\|\.username' app/core/` shows attribute reads ONLY in selector.py (other hits are comments).
