# Phase 3: Matching & Quality Gating - Research

**Researched:** 2026-05-30
**Domain:** Fuzzy entity matching (ported beets distance model) + quality-profile/cutoff gating + coarse fake-FLAC heuristics, scoring noisy slskd candidate folders against an authoritative *arr/MusicBrainz manifest
**Confidence:** HIGH (architecture, beets model, library choices, firewall fit) / MEDIUM (exact slskd candidate JSON field names — confirmable against live slskd in Phase 4; Phase 3 owns the dataclass contract, so this is non-blocking) / LOW-by-design (exact threshold/weight/fake-FLAC numbers — intentionally calibrated against the labeled corpus)

## Summary

Phase 3 builds the **gating brain**: a deterministic, offline-testable, pure function `(candidate, manifest, profile) → (decision, score, reasons)`. It does **no** searching, downloading, or importing — that is Phase 4. Phase 3's job is to (1) define the candidate data shape Phase 4 will populate, (2) port the beets album-distance model into a self-contained pure scorer, (3) layer a quality-profile/cutoff gate and coarse fake-FLAC heuristics on top, and (4) emit explainable reason strings so the conservative ~1σ threshold can be hand-tuned.

The seed's directives are sound and confirmed by research: **port** the beets distance model rather than depend on beets. beets pulls a large transitive tree (mediafile, confuse, musicbrainzngs, jellyfish, unidecode, munkres, pyyaml, reflink…) and its `Distance` class is tightly coupled to beets' `Item`/`AlbumInfo`/`TrackInfo` objects and the global `beets.config`, making it unusable as a drop-in library against slskd folder listings. The model itself is simple, public (beets is MIT), and documented: a set of named sub-distances each in `[0,1]`, combined as a **weighted average** (`Σ(wᵢ·dᵢ)/Σ(wᵢ)`), with `strong`/`medium`/`none` recommendation tiers gated additionally by a "distance gap to the runner-up" check. Curator adapts the weights for Soulseek's reality: candidate metadata is free-text folder/file names, often incomplete, sometimes non-Latin — so **track-count completeness and per-track title coverage carry more weight, and the candidate's self-described year/label/catalog number are untrusted** (anchor only on the manifest, never the candidate's claims). Curator collapses beets' three tiers into **accept / decline** (no human-adjudicated "medium" — there is no human in this loop).

**Primary recommendation:** Build pure modules under `app/core/` — `candidate.py` (the `Candidate`/`CandidateFile` dataclasses; the Phase 3→4 contract), `manifest.py` (normalized target identity), `matching.py` (ported beets-style scorer → score + reasons), `quality.py` (profile/cutoff format-bitrate gate), `fakeflac.py` (coarse FLAC sanity), `selector.py` (dumb, swappable best-pick), and `gate.py` (composes them). Matching (hard, pure) stays strictly separate from selection (uploader speed/slots/format tie-break — dumb, swappable). Implement the already-declared `get_quality_profile` on the adapters (QUAL-01), returning a **normalized** `Profile` so no *arr field names cross the Phase-2 firewall. Prove everything against a labeled fixture corpus of slskd-shaped JSON with zero network, mirroring the Phase-2 offline-fixture pattern.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Score candidate identity vs manifest | `app/core/matching.py` (pure) | — | Hard, deterministic, offline-testable; the seed's core directive |
| Define candidate data shape | `app/core/candidate.py` (pure dataclass) | adapters (Phase 4 populates) | Phase 3 owns the contract; Phase 4 fills it from real slskd results |
| Resolve quality profile/cutoff | `app/adapters/` (`get_quality_profile`) | `app/core/quality.py` consumes a normalized `Profile` | *arr field names stay behind the firewall (locked Phase 2 rule) |
| Filter candidate by format/bitrate vs cutoff | `app/core/quality.py` (pure) | — | Pure logic over a normalized profile + candidate file attrs |
| Fake/transcoded-FLAC heuristics | `app/core/fakeflac.py` (pure) | — | Coarse math over file size/duration/claimed-bitrate |
| Select best surviving candidate | `app/core/selector.py` (dumb, swappable) | — | Separate from matching per seed; uploader speed/slots/format tie-break |
| Provide the manifest (track list) for a gap | `app/adapters/` + ledger `foreign_id` | `app/core` consumes a normalized `Manifest` | MBID/track-count live behind adapter; core sees a neutral struct |
| Config-tunable thresholds/weights | `app/config.py` (`Settings`) | `gate.py`/`MatchConfig` defaults | Owner tunes thresholds up over time without a rebuild |

## User Constraints

> No CONTEXT.md exists yet for Phase 3 (discuss-phase has not run). The binding constraints below come from RESEARCH-SEED.md (owner pre-planning directives, 2026-05-30), CLAUDE.md, and the locked Phase-2 firewall. Treat all of these as locked — research serves them, does not relitigate them.

### Locked Decisions (from RESEARCH-SEED.md, owner)
- **Port the beets autotagger album-distance model** into Curator's own pure-function matcher — do not invent a scoring model from scratch, and do not relitigate the model choice.
- **Anchor on the canonical *arr/MusicBrainz manifest, never the candidate's self-description.** Matching answers "does this folder correspond to this known manifest?", not open-ended identification.
- **Explainable weighted scoring, no ML/black box** — every sub-score emits a reason string.
- **Precision over recall is a feature.** Set the confidence threshold conservatively HIGH. Target the **~1σ center** (easy, obvious matches); intentionally sacrifice the 2–3σ long tail (deluxe editions, compilations, ambiguous track counts, borderline fakes). Ambiguous → decline → item retries / Usenet gets it. **A human is never asked to adjudicate.**
- **Build test-first against a labeled fixture corpus** of slskd-shaped JSON (known-good + incomplete + wrong-edition + fake-FLAC + non-Latin) as a pure function `(candidate, manifest, profile) → (decision, score, reasons)` — deterministic, no live Soulseek.
- **Separate matching (hard, pure) from selection (uploader speed/slots/format preference — dumb, swappable).**
- **Fake-FLAC = coarse heuristics only** (size-per-duration, claimed-bitrate sanity, source-tag sanity). Spectral/frequency analysis (QUAL-04) is explicitly OUT of v1 scope.
- **Reuse libraries for sub-pieces:** RapidFuzz (`token_set_ratio`) for fuzzy string match; a guessit-style parser for release-name tokenization.
- **Phase 3 does NOT search/download/import** — that is Phase 4. Phase 3 is gating logic only, operating on candidate data structures.

### Claude's Discretion (research recommends)
- Exact starting threshold numbers (recommended below, must be config-tunable).
- Exact per-sub-distance weights (recommended below, must be config-tunable).
- Whether the release-name tokenizer is `guessit` proper, a thin custom regex parser, or a hybrid (recommendation: custom regex primary, guessit fallback).
- Module file layout within the `app/core/` firewall (proposed below).

### Deferred Ideas (OUT OF SCOPE)
- QUAL-04 spectral/frequency-cutoff FLAC analysis (v2).
- Books gating logic beyond a stubbed/abstracted seam — **music must work end-to-end first** (RESEARCH-SEED + CLAUDE.md). Books ride the same matcher abstraction best-effort, never gate music.
- Any live slskd interaction, search triggering, or download (Phase 4).
- Chasing the ambiguous long tail (multi-disc edge cases, compilations, deluxe-edition disambiguation) — the correct behavior is **decline**, not heroics.

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MATCH-01 | Score slskd candidates against authoritative identity (artist/album, track-count completeness, edition/year, format; author/title+format for books) | Ported beets distance model (`matching.py`): per-sub-distance scorer with track-count, per-track-title, artist, album sub-distances → weighted average. §1, §2 |
| MATCH-02 | Reject candidates below a configurable confidence threshold (precision over recall) | Conservative `strong` accept threshold + rec-gap tie-break; `decline` on ambiguity. Config-tunable. §3 |
| QUAL-01 | Read the item's *arr quality profile and cutoff via the adapter | Implement the already-declared `get_quality_profile(profile_id)` behind the firewall, returning a normalized `Profile`; `quality_profile_id` already on `GapItem`/ledger. §5 |
| QUAL-02 | Filter candidates by format/bitrate BEFORE download; never grab below cutoff (no downgrades) | Pure `quality.py` gate maps candidate file ext/bitrate to a profile-rank and rejects below-cutoff/not-allowed. §5 |
| QUAL-03 | Heuristic fake/transcoded-FLAC checks (bitrate/size/source-tag sanity) before accepting FLAC | Pure `fakeflac.py`: bytes-per-second floor, claimed-bitrate-vs-size consistency, lossy-source-token sanity. §6 |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `rapidfuzz` | `3.x` (latest line 3.13.x, 2025) [VERIFIED: PyPI — cp312 manylinux x86_64 wheels present] | Fuzzy string distance: `fuzz.token_set_ratio`, `fuzz.token_sort_ratio`, `fuzz.ratio` for artist/album/track-title sub-distances | C++/Cython core, MIT, ships manylinux + cp312 wheels (no build toolchain needed on `linux/amd64`), far faster than pure-Python `fuzzywuzzy`/`difflib`, actively maintained. Named explicitly in the seed AND already pre-selected in `.planning/research/STACK.md`. |
| Python stdlib `re`, `unicodedata` | 3.12 | Tokenization helpers, NFKD normalization for non-Latin/diacritic folding before fuzzing; the custom release-name tokenizer | Zero-dependency; `unicodedata.normalize('NFKD', s)` + strip-combining covers the ~1σ non-Latin center without another C dep (beets uses `unidecode`; we avoid that transitive dep) |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `guessit` | `3.x` (latest 3.8.x line) [ASSUMED: PyPI — pure-Python; deps `rebulk`, `babelfish`, `python-dateutil`] | Release-name tokenization (year/format/edition/source extraction from slskd folder/file names) | **Fallback only — see §4 recommendation.** Pure-Python, but tuned for *video* release names; music-folder benefit is partial. Prefer the ~30-line custom regex tokenizer; adopt guessit only if the corpus reveals naming chaos the regex can't tame. |
| `pydantic-settings` | `2.x` [ASSUMED: PyPI; also listed in STACK.md] | Config-tunable thresholds/weights via env | **Conditional.** `app/config.py` currently uses a **hand-rolled frozen `@dataclass` `Settings.from_env()`** (NOT pydantic-settings). Recommendation: extend that existing mechanism for the new tunables rather than introduce pydantic-settings now — keeps one config pattern. (STACK.md lists pydantic-settings for the broader project; deferring it here avoids a parallel config path.) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Port beets distance model | `import beets.autotag.match` (depend on beets) | **Rejected.** beets `Distance` is coupled to `beets.config` (global confuse), `AlbumInfo`/`TrackInfo`/`Item` objects, and plugin hooks; large transitive tree (mediafile, confuse, musicbrainzngs, jellyfish, unidecode, munkres, reflink…); designed for clean local tags vs MB, not noisy slskd listings. License is MIT so *porting the design* is clean; depending on the package is heavyweight and a poor fit on a `linux/amd64` Synology image. |
| `rapidfuzz` | `thefuzz`/`fuzzywuzzy` | Slower (pure-Python or python-Levenshtein C dep), less maintained. RapidFuzz is the modern standard. |
| custom regex tokenizer | `guessit` | guessit is tuned for video; for music folders we need only a handful of tokens (year, format, source, edition). Custom regex is ~30 lines, zero transitive deps, trivially pure-testable. **Custom recommended primary; guessit fallback.** |
| Extend `app/config.py` dataclass | `pydantic-settings` | pydantic-settings is nicer but introduces a second config pattern mid-project; the existing `Settings.from_env()` already proves the env-snapshot approach (WR-01 fix). Defer the migration. |

**Installation:**
```bash
# Add to app/requirements.txt (runtime), pinned at plan time:
rapidfuzz==3.13.x        # confirm exact patch via `pip index versions rapidfuzz` in CI at plan time
# guessit ONLY if the custom tokenizer proves insufficient against the corpus:
# guessit==3.8.x
```
`pytest` + `respx` are already in `requirements-dev.txt` (Phase 2). No new dev deps required unless adding `hypothesis` for optional property-based threshold tests.

**Version verification:** `rapidfuzz` confirmed on PyPI (latest 3.13.x line, 2025; ships `cp312` `manylinux_2_17_x86_64`/`manylinux2014` wheels — installs without a compiler on the Synology `linux/amd64` image) `[VERIFIED: PyPI]`. `guessit` 3.8.x, pure-Python `py3-none-any`, deps `rebulk`/`babelfish`/`python-dateutil` `[ASSUMED — web fetch of pypi json was rate-limited this pass; re-confirm at plan time, non-blocking since guessit is fallback-only]`. `pydantic-settings` 2.x current. Because the dev sandbox is Python 3.9 + offline, the **authoritative pin happens in CI/NAS** at plan time (re-run `pip index versions <pkg>`), exactly as Phase 2 pinned httpx/respx.

## Package Legitimacy Audit

> slopcheck could not be run in this offline dev sandbox (Python 3.9, no network for pip; the `pip`/`head` bash pipeline was also denied this session). Per protocol, packages are tagged `[ASSUMED]` for legitimacy and the planner MUST gate each new install behind a `checkpoint:human-verify` task before adding it to `requirements.txt` — mirroring exactly how `httpx`/`respx` were human-approved at the Phase-2 package-legitimacy checkpoint (see the comments in `requirements.txt`/`requirements-dev.txt`).

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| `rapidfuzz` | PyPI | ~5 yrs (since 2020) | very high (~10M+/mo class) | github.com/rapidfuzz/RapidFuzz | n/a (offline) | `[ASSUMED]` — planner adds human-verify checkpoint. Well-known, MIT, C-extension with first-party manylinux wheels. |
| `guessit` | PyPI | ~12 yrs | high | github.com/guessit-io/guessit | n/a (offline) | `[ASSUMED]` + only if adopted. Long-established GuessIt project. |
| `pydantic-settings` | PyPI | ~2 yrs (split from pydantic v2) | very high | github.com/pydantic/pydantic-settings | n/a (offline) | `[ASSUMED]` — first-party pydantic org. Deferred per Stack note. |

**Packages removed due to slopcheck [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none
**Planner action:** add a `checkpoint:human-verify` task confirming each package name + pin against PyPI before it lands in `requirements.txt` (Phase 2 precedent). The only package likely needed this phase is `rapidfuzz`.

## Architecture Patterns

### System Architecture Diagram

```
                          PHASE 3 SCOPE (pure gating logic — NO network)
                          ───────────────────────────────────────────────
  (Phase 4 will populate)                                   ledger (state/repo.py, items table)
  raw slskd search JSON ──┐                                  foreign_id (MBID), quality_profile_id
                          │                                            │
                          ▼                                            ▼
                 candidate.py                            adapter.get_manifest(foreign_id)  ── Phase 3/4 seam
                 Candidate / CandidateFile  <─ normalize ─  adapter.get_quality_profile(id)  ── behind firewall
                 (folder name, file list,                            │  (return NORMALIZED
                  per-file ext/bitrate/                              │   Manifest + Profile —
                  size/duration/sample-rate)                        │   NO *arr field names
                          │                                          │   cross the firewall)
                          │                                          ▼
                          │                                Manifest(artist, album, year,
                          │                                 track_count, [track_titles…], kind)
                          │                                Profile(allowed_ranks, cutoff_rank)
                          ▼                                          │
        ┌───────────────────────────── gate.py (compose) ──────────────────────────────┐
        │                                                                               │
        │   1. matching.score(candidate, manifest)  ──► (distance, reasons[])           │
        │        ├─ artist sub-distance      (rapidfuzz token_set_ratio)                │
        │        ├─ album  sub-distance      (rapidfuzz token_set_ratio)                │
        │        ├─ track_count sub-distance (|cand_audio_files − manifest_tracks|)     │
        │        ├─ track_title coverage     (greedy per-track best-match, if titles)   │
        │        └─ weighted average → distance ∈ [0,1]  (lower = better, beets-style)  │
        │                                                                               │
        │   2. quality.gate(candidate, profile) ──► pass/fail + reason                  │
        │        (every wanted audio file >= cutoff rank AND in allowed set)            │
        │                                                                               │
        │   3. fakeflac.check(candidate)        ──► pass/fail + reason                  │
        │        (only if FLAC: bytes/sec floor, claimed-bitrate sanity, source tag)    │
        │                                                                               │
        │   Decision per candidate: eligible iff quality pass AND fakeflac pass         │
        │   ACCEPT iff best eligible distance <= strong_thresh AND rec-gap clear; else  │
        │   DECLINE.                                                                     │
        └───────────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
        selector.py (dumb, swappable) — among ACCEPT-eligible candidates,
        pick best by (distance, then format preference, then uploader
        free-slots / speed). Returns chosen Candidate or None (decline).
                          │
                          ▼
        GateResult(decision='accept'|'decline', chosen, distance, reasons[])
        (Phase 4 consumes this to actually trigger a download)
```

### Recommended Project Structure
```
app/core/
├── gap_detector.py    # EXISTING (Phase 2) — unchanged
├── candidate.py       # NEW: Candidate / CandidateFile dataclasses (the Phase 3→4 contract)
├── manifest.py        # NEW: Manifest dataclass (normalized target identity, no *arr field names)
├── matching.py        # NEW: pure beets-style distance scorer → (distance, reasons)
├── quality.py         # NEW: pure profile/cutoff format-bitrate gate → (pass, reason); QualityRank ladder
├── fakeflac.py        # NEW: pure coarse FLAC heuristics → (pass, reason)
├── release_parse.py   # NEW: pure release-name tokenizer (year/format/source/edition + clean name)
├── selector.py        # NEW: dumb, swappable best-candidate selection
└── gate.py            # NEW: composes matching+quality+fakeflac+selector → GateResult

app/adapters/
├── base.py            # EDIT: add Profile/Manifest neutral types; implement get_quality_profile in Protocol;
│                      #       add get_manifest to the Protocol (impl now or Phase-4-wired, see Q2)
├── lidarr.py          # EDIT: implement get_quality_profile (normalize) + get_manifest (MB track list)
└── readarr.py         # EDIT: best-effort get_quality_profile/get_manifest (book format ladder) — stub-safe

app/tests/
├── fixtures/
│   └── candidates/    # NEW: labeled slskd-shaped JSON corpus (see Validation Architecture)
│       ├── known_good_flac.json ... fake_flac.json ... non_latin.json
│       └── manifests/ + profiles/   # the targets each candidate is scored against
├── test_matching.py   # NEW
├── test_quality.py    # NEW
├── test_fakeflac.py   # NEW
├── test_release_parse.py  # NEW
└── test_gate.py       # NEW (end-to-end pure gating over the corpus)
```

**Firewall rule (LOCKED from Phase 2 — MUST preserve):** `app/core/*` and `app/state/*` import only neutral types (`GapItem`, the new `Candidate`/`Manifest`/`Profile`), the `ArrAdapter` Protocol, and `state.repo`. *arr-specific field names (`foreignAlbumId`, `profileId`, `qualityProfileId`, `records[]`, profile JSON `items[]`/`allowed`/`cutoff`, MB internals) live ONLY in `app/adapters/`. The new `get_quality_profile`/`get_manifest` adapter methods must return **already-normalized** `Profile`/`Manifest` objects so no *arr vocabulary leaks into `core/`. The existing comment-aware grep test over `app/core` + `app/state` MUST be extended to cover the new modules. (Phase-2 precedent: the grep test currently asserts 0 forbidden tokens.)

### Pattern 1: Beets-style weighted distance (PORTED, not imported)
**What:** Each identity facet produces a sub-distance in `[0,1]` (0 = perfect, 1 = worst). The album distance is the **weighted average** of the active sub-distances: `Σ(wᵢ·dᵢ)/Σ(wᵢ)`. Lower total = better. This is exactly beets' `Distance` semantics (a `_penalties` dict of name → list-of-floats, each weighted, normalized by total weight).
**When to use:** The core of `matching.py`.
**Example (the model to port — Curator's own clean pure implementation):**
```python
# app/core/matching.py  (ported design; beets is MIT, this is a clean reimplementation)
from dataclasses import dataclass
import unicodedata
from rapidfuzz import fuzz

@dataclass(frozen=True)
class MatchConfig:
    # Weights adapted for Soulseek (see §2). Config-tunable via app/config.Settings at the gate layer.
    w_artist: float = 3.0
    w_album: float = 3.0
    w_track_count: float = 4.0      # RAISED vs beets — completeness is paramount for slskd
    w_track_titles: float = 4.0     # RAISED — per-track coverage is the strongest authenticity signal
    strong_thresh: float = 0.15     # ACCEPT only if total distance <= this (conservative; ~1σ)
    rec_gap_thresh: float = 0.10    # runner-up must be at least this much worse, else ambiguous→decline

def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()

def _str_distance(a: str | None, b: str | None) -> float:
    # token_set_ratio is order/duplication tolerant — ideal for "Artist - Album (2007) [FLAC]" noise.
    return 1.0 - (fuzz.token_set_ratio(_norm(a), _norm(b)) / 100.0)

def _track_count_distance(cand_audio_files: int, manifest_tracks: int) -> float:
    if manifest_tracks <= 0:
        return 1.0
    return min(1.0, abs(cand_audio_files - manifest_tracks) / manifest_tracks)

def score(candidate, manifest, cfg: MatchConfig = MatchConfig()):
    penalties, weights, reasons = [], [], []
    da = _str_distance(candidate.parsed_artist, manifest.artist)
    penalties.append(da); weights.append(cfg.w_artist)
    reasons.append(f"artist '{candidate.parsed_artist}' vs '{manifest.artist}' dist={da:.2f}")
    dl = _str_distance(candidate.parsed_album, manifest.album)
    penalties.append(dl); weights.append(cfg.w_album)
    reasons.append(f"album '{candidate.parsed_album}' vs '{manifest.album}' dist={dl:.2f}")
    dc = _track_count_distance(candidate.audio_file_count, manifest.track_count)
    penalties.append(dc); weights.append(cfg.w_track_count)
    reasons.append(f"track-count {candidate.audio_file_count}/{manifest.track_count} dist={dc:.2f}")
    if manifest.track_titles:   # only when MB gave us a title list
        dt = _track_title_coverage(candidate.file_titles, manifest.track_titles)
        penalties.append(dt); weights.append(cfg.w_track_titles)
        reasons.append(f"track-title coverage dist={dt:.2f}")
    total = sum(p*w for p, w in zip(penalties, weights)) / sum(weights)
    return total, reasons
```
**Source:** beets `Distance` semantics — `[CITED: beets.readthedocs.io/en/stable/reference/config.html#match]` (distance_weights are a weighted average of per-facet penalties); reimplemented as a Curator-owned pure function.

### Pattern 2: Recommendation tiers + rec-gap (precision over recall)
**What:** beets does NOT accept on absolute distance alone — it also requires the best candidate to be clearly better than the runner-up. Curator adopts both: **ACCEPT only if** `best_distance <= strong_thresh` AND `(second_best_distance - best_distance) >= rec_gap_thresh`; otherwise DECLINE (ambiguous). This is the structural expression of "target the 1σ center, decline the tail." Curator collapses beets' strong/medium/none into accept/decline (no human-adjudicated medium tier).
**Example:**
```python
def recommend(scored, cfg):  # scored: list[(distance, candidate, reasons)] ascending, ALREADY quality+fakeflac-eligible
    if not scored:
        return ("decline", None, 1.0, ["no eligible candidates"])
    best_d, best_c, best_r = scored[0]
    if best_d > cfg.strong_thresh:
        return ("decline", None, best_d, best_r + [f"DECLINE best dist {best_d:.2f} > strong {cfg.strong_thresh}"])
    if len(scored) > 1 and (scored[1][0] - best_d) < cfg.rec_gap_thresh:
        return ("decline", None, best_d, best_r + ["DECLINE ambiguous: runner-up within rec_gap"])
    return ("accept", best_c, best_d, best_r + [f"ACCEPT total={best_d:.2f} <= strong={cfg.strong_thresh}"])
```

### Anti-Patterns to Avoid
- **Trusting the candidate's self-described metadata over the manifest.** Never score year/label/catalog from the folder name as ground truth — anchor on the manifest, use candidate tokens only to *match against* it.
- **Letting *arr field names leak into `core/`.** The profile/manifest must arrive pre-normalized from the adapter. (Breaks the locked firewall + its grep test.)
- **Coupling matching to selection.** Uploader speed/slots must never influence the match score — selection is a separate, dumb tie-break over already-accepted candidates.
- **Chasing the long tail.** No deluxe-edition/compilation/multi-disc disambiguation logic — DECLINE on ambiguity (seed directive).
- **Spectral analysis.** QUAL-04 is out of scope; fake-FLAC is coarse-only.
- **Rejecting on missing data.** A heuristic whose inputs are absent should be **skipped**, not turned into a rejection.
- **Live network in tests.** The entire phase is provable offline against the corpus.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fuzzy string similarity | Custom Levenshtein/difflib loops | `rapidfuzz.fuzz.token_set_ratio` | C-speed, handles token reordering/duplication, battle-tested; hand-rolled is slow and subtly wrong on tokenized names |
| The whole scoring model | A bespoke ad-hoc heuristic scorer | Ported **beets distance model** (weighted-average penalties + rec-gap) | Mature prior art for *this exact problem*; the seed mandates it; avoids reinventing recommendation tiers |
| Unicode/diacritic folding | Manual char maps | `unicodedata.normalize('NFKD', s)` + strip combining marks | Correct non-Latin handling for the corpus's non-Latin case; avoids the `unidecode` C dep |
| Config-tunable thresholds | Hard-coded constants edited in source | Extend `app/config.py` `Settings.from_env()` env vars | Owner must tune thresholds up over time without a rebuild; seed requires tunability; reuse the existing config pattern |

**Key insight:** The scoring *model* is the hard, mature part — port it, don't invent. The *plumbing* (fuzzy ratio, unicode fold, config) is solved by stdlib + rapidfuzz. Curator's only genuinely novel work is **adapting the weights for Soulseek noise** (§2) and the **coarse fake-FLAC heuristics** (§6).

## Detailed Research Answers

### §1 — beets distance model: port vs depend, and what to port
- **License:** beets is **MIT** `[CITED: github.com/beetbox/beets LICENSE]` — porting the design into Curator is fully permitted.
- **Depend? No.** `beets.autotag.match.distance()` operates on `AlbumInfo`/`TrackInfo`/`Item` objects, reads weights from the global `beets.config` (confuse), and invokes plugin hooks. Pulling beets drags `mediafile`, `confuse`, `musicbrainzngs`, `jellyfish`, `unidecode`, `munkres`, `pyyaml`, `reflink` — a large, ill-fitting tree for a `linux/amd64` Synology image. The model is simple; **port it.**
- **What the model is (verified against official docs):** distance = **weighted average of named penalties**, each penalty in `[0,1]`, combined as `Σ(wᵢ·dᵢ)/Σ(wᵢ)`. Recommendation tiers `strong`/`medium`/`none` derive from the total distance plus the **gap to the next-best candidate**.
- **Default `distance_weights` (the canonical reference set) [CITED: beets.readthedocs.io/en/stable/reference/config.html#match]:**
  | beets weight | default | Curator decision |
  |---|---|---|
  | `source` | 2.0 | drop (N/A — single source) |
  | `artist` | 3.0 | **keep** |
  | `album` | 3.0 | **keep** |
  | `media` | 1.0 | drop (don't trust candidate-claimed media) |
  | `mediums` | 1.0 | drop / fold into track_count |
  | `tracks` | 2.0 | **keep, raise** (per-track aggregate) |
  | `missing_tracks` | 0.9 | **keep, raise** (completeness driver) |
  | `unmatched_tracks` | 0.6 | **keep** (extra files) |
  | `year` | 1.0 | **drop/zero** (don't trust candidate year — anchoring rule) |
  | `country`/`label`/`catalognum`/`albumdisambig` | ~0.5 each | **drop** (long-tail edition disambig → decline instead) |
  | `album_id` | 5.0 | N/A (no candidate MBID) |
  | `track_title` | 3.0 | **keep, raise** |
  | `track_index`/`track_length` | 1.0 / 2.0 | drop (slskd files rarely carry reliable index/length pre-download) |
  | `track_id` | 5.0 | N/A |
- **Recommendation thresholds (beets defaults):** `strong_rec_thresh: 0.04`, `medium_rec_thresh: 0.25`, `rec_gap_thresh: 0.25` `[CITED: same docs]`. Curator collapses to two outcomes (accept/decline) and recalibrates the numbers to its reweighted distance scale — see §3.
- **Track-count penalty (the completeness driver):** beets penalizes `missing_tracks` and `unmatched_tracks` separately. Curator's simpler `track_count_distance = |cand_audio_files − manifest_tracks| / manifest_tracks` captures the ~1σ completeness signal cheaply; optionally split into missing-vs-extra if the corpus shows asymmetry (missing should hurt more than extra bonus tracks). `[ASSUMED]` the simple version suffices for the center — validate on corpus (A1).

### §2 — Adapting weights for Soulseek
beets matches **clean local tags** against MB; Curator matches **noisy free-text folder/file names** (`Artist - Album (Year) [FLAC]/01 - Track.flac`, often incomplete, sometimes non-Latin) against the authoritative manifest. Changes:
- **Raise track-count + track-title weights** (the strongest authenticity signals when tags are absent): `track_count` 4.0, `track_titles` 4.0 vs `artist`/`album` 3.0. A folder with the right album name but 4 files for a 12-track album is the classic wrong/incomplete grab the seed wants declined.
- **Zero/drop candidate-self-described facets:** `year`, `country`, `label`, `catalognum`, `media`, `track_index`, `track_length`, `source`. Candidate claims are untrusted (anchoring rule), and we often lack reliable per-file length pre-download anyway.
- **Tokenize, then `token_set_ratio`:** strip bracket/paren noise (`[FLAC]`, `(2007)`, `{WEB}`) via `release_parse.py` before fuzzing artist/album so format/year tokens don't dilute the name match.
- **Graceful missing/garbage metadata:** if the candidate's parsed artist or album is empty/None → that sub-distance = **1.0** (max penalty) with a reason, not a crash. If the manifest lacks `track_titles` → **omit** that sub-distance (don't penalize) and lean on track-count. A candidate with zero audio files → immediate decline.
- **Non-Latin:** NFKD-normalize + strip combining marks both sides before fuzzing; `token_set_ratio` on normalized strings handles diacritic/transliteration-free cases. Genuinely different scripts that don't match → high distance → decline (acceptable per "decline the tail").

### §3 — The 1σ / precision-over-recall threshold
- **Two outcomes only:** Curator has no human adjudicator, so collapse beets' strong/medium/none into **accept / decline**. No "medium → ask a human" tier.
- **Conservative starting thresholds (config-tunable, recommended):**
  - `MATCH_STRONG_THRESH = 0.15` — accept only if total weighted distance ≤ 0.15 (≈ ≥85% aggregate identity confidence). Start here; tune **up** (looser) only if the corpus + live data show over-declining; never start loose.
  - `MATCH_REC_GAP_THRESH = 0.10` — the best candidate must beat the runner-up by ≥ 0.10 distance, else **decline** (ambiguous twins are exactly the tail to skip).
  - These differ from beets' `0.04` strong rec because Curator's distance scale differs (fewer, reweighted facets, no album_id/track_id). `0.15` is the recommended *starting* center, not a port of beets' number. `[ASSUMED — calibrate against the labeled corpus]` (A2): known-good cases must land ≤ 0.15 and all wrong/incomplete/fake cases must land > 0.15; if not, adjust the number, not the test.
- **Tunability:** expose `MATCH_STRONG_THRESH`, `MATCH_REC_GAP_THRESH`, each weight, and the fake-FLAC floor via `app/config.py` `Settings.from_env()` (the existing env-snapshot pattern). Defaults baked in `MatchConfig`; env overrides for live tuning without rebuild.
- **Explicit reason strings each sub-score MUST emit** (Soularr's opacity was the prior pain point — see PITFALLS Pitfall 2/4):
  - artist: `artist '<cand>' vs '<manifest>' dist=0.NN`
  - album: `album '<cand>' vs '<manifest>' dist=0.NN`
  - track-count: `track-count <cand_files>/<manifest_tracks> dist=0.NN`
  - track-titles: `track-title coverage <matched>/<total> dist=0.NN`
  - decision: `ACCEPT total=0.NN <= strong=0.15` / `DECLINE total=0.NN > strong=0.15` / `DECLINE ambiguous: runner-up within rec_gap`
  - quality: `quality REJECT: file .mp3@192 below cutoff rank` / `quality OK: all files >= cutoff`
  - fake-FLAC: `fakeflac REJECT: 612 kbps effective < floor` etc.

### §4 — Sub-piece libraries (RapidFuzz + release-name tokenizer)
- **RapidFuzz** `3.13.x` `[VERIFIED: PyPI existence + cp312 manylinux wheels]`. Minimal usage: `fuzz.token_set_ratio(a, b)` (0–100; `1 - x/100` = distance). `token_set_ratio` chosen over `ratio`/`partial_ratio` because slskd names reorder/duplicate tokens. Pure-Python fallback: RapidFuzz ships cp312/`linux/amd64` wheels, so no fallback needed; if ever unavailable, `difflib.SequenceMatcher().ratio()` is the stdlib floor (slower, no token-set semantics — degraded mode only).
- **Release-name tokenizer — RECOMMENDATION: thin custom regex parser (`release_parse.py`), not guessit (primary).** guessit (`3.8.x`, pure-Python, deps `rebulk`+`babelfish`+`python-dateutil`) is tuned for **video** release names; for music folders we need only:
  - year: `\b(19|20)\d{2}\b`
  - format/quality: `FLAC|ALAC|WAV|APE|MP3|AAC|OGG|320|256|192|V0|V2|V8|24bit|16bit|24-?44|Hi-?Res`
  - source: `\b(WEB|CD|Vinyl|LP|SACD|Cassette|Tape)\b`
  - edition: `Deluxe|Remaster(ed)?|Anniversary|Expanded|Bonus`
  - then strip matched tokens + bracket/paren groups to leave clean artist/album text for fuzzing.
  …gives full control, zero transitive deps, and is a trivially-testable pure function. **Keep guessit as a documented fallback** only if the corpus reveals folder-naming chaos the regex can't tame. Either way the tokenizer is pure and offline-testable. Keep regexes anchored/bounded to avoid ReDoS on hostile folder names. `[ASSUMED]` (A5) the custom regex covers the ~1σ center — the corpus's `non_latin`/odd-name cases are the test; if they fail, adopt guessit (gated by the legitimacy checkpoint).

### §5 — Quality profile + cutoff gating (QUAL-01/02)
- **Surfacing the profile through the firewall:** implement the already-declared `get_quality_profile(profile_id)` on the adapters (currently stubbed in `base.py`). It must fetch the *arr profile JSON **and normalize it inside the adapter** into a neutral `Profile` dataclass — e.g. `Profile(allowed: frozenset[QualityRank], cutoff_rank: int)` — so `core/quality.py` never sees *arr JSON. `GapItem.quality_profile_id` (mapped from `AlbumResource.profileId` in Phase 2 — VERIFIED in lidarr.py comments) is the input.
  - Lidarr quality-profile API: `GET /api/v1/qualityprofile/{id}` returns an ordered `items[]` (each `allowed:bool`, nested quality `{id,name}` like `FLAC`,`MP3-320`,`MP3-V0`…) and a `cutoff` quality id `[CITED: STACK.md *arr API table + lidarr.audio/docs/api]`. `[ASSUMED — confirm exact field shapes against live Lidarr at plan time; keep all of it inside lidarr.py]` (A4). The adapter converts the ordered allowed-list + cutoff into `Profile(allowed_ranks, cutoff_rank)`.
- **Where candidate format/bitrate comes from:** the slskd search result's per-file attributes. A slskd file carries `filename` (→ extension), `size` (bytes), and **optional** audio attributes from Soulseek file-attribute codes: `bitRate`, `bitDepth`, `sampleRate`, `length` (seconds), `isVariableBitRate`. `[ASSUMED — exact JSON keys confirmed against live slskd / its swagger in Phase 4; Phase 3 fixes the `CandidateFile` dataclass shape so Phase 4 maps into it]` (A3). Format is derived primarily from the **file extension** (`.flac/.mp3/.m4a/.ape/.wav/.ogg`); bitrate from the `bitRate` attr when present, else an inferred bucket from extension (`.flac` → lossless rank; `.mp3` with no bitrate → unknown, treated conservatively).
- **The gate (pure, `core/quality.py`):** map each candidate wanted audio file → a `QualityRank` (by extension + bitrate), then **reject the candidate if ANY wanted audio file ranks below the profile's `cutoff_rank`** (no downgrades — QUAL-02) or if its rank is not in the profile's `allowed` set. Emit reasons. Runs **before** any download by construction (Phase 3 is pre-download). This is the structural defense against PITFALLS Pitfall 3 (quality downgrades / fake FLAC at "cutoff met").
- **Books:** Readarr profiles are format-based (`EPUB`/`MOBI`/`PDF`/`AZW3`); the same `Profile(allowed, cutoff_rank)` abstraction holds with a book-format rank ladder. Best-effort, behind the seam.

### §6 — Fake/transcoded-FLAC heuristics (QUAL-03), COARSE only
Pure `core/fakeflac.py`, runs only when a candidate file is FLAC. Three coarse checks, each a formula a planner can implement directly (all `[ASSUMED]` starting thresholds (A6) — calibrate on the `fake_flac` corpus, tune via config):
1. **Bytes-per-second floor (the primary signal).** `effective_kbps = (size_bytes * 8) / length_seconds / 1000`. A genuine 16-bit/44.1kHz stereo FLAC is typically ~**700–1000 kbps** after compression (raw PCM ~1411 kbps; FLAC ~40–60% of that). A FLAC whose effective bitrate is **< ~400 kbps** is almost certainly a low-bitrate lossy source re-wrapped/upscaled → **REJECT**. Recommended config floor `FAKEFLAC_MIN_KBPS = 400` (conservative; real quiet/classical tracks can dip, hence not 700). Requires `length` — **if absent, skip this check** (don't reject on missing data; lean on checks 2–3).
2. **Claimed-bitrate sanity.** If the file's `bitRate` attribute is present and claims a suspiciously low value for FLAC (e.g. `< 400`) or exactly a classic lossy bucket (128/192/256/320) → suspicious → REJECT. `isVariableBitRate=false` with a round lossy number is a flag.
3. **Source-tag / filename sanity.** If the folder/filename contains a lossy marker while claiming `.flac` (`mp3`, `320`, `v0`, `web-dl from spotify`, `youtube`, `cbr`) → REJECT (`reason: lossy source token in FLAC candidate`). Cheap string check on already-parsed tokens (§4).
- **Out of scope (QUAL-04):** spectral/frequency-cutoff detection — explicitly v2.

### §7 — Multi-disc / track grouping / edition disambiguation
- **beets/Picard** handle these via `mediums`/`media`/disc-tagging and MB release disambiguation. Curator deliberately does **not** chase this (seed: "the ambiguous long tail is out of scope").
- **Cheap correct behavior = decline.** For multi-disc albums, the manifest's `track_count` is the sum across discs; a single-disc folder matching one disc fails the track-count completeness penalty → distance > threshold → **decline** (correct: don't grab half an album). Multiple candidate folders for the same gap that are near-ties (different editions) trip the **rec-gap** check → **decline**. No special edition logic is needed or wanted; the model's existing penalties + rec-gap produce the right "when unsure, skip" behavior for free.

### §8 — Module/architecture fit
Covered in **Recommended Project Structure** above. Key contracts:
- **`Candidate` / `CandidateFile` dataclasses (`core/candidate.py`) are the Phase 3→4 contract.** Phase 3 defines and tests them with fixture JSON; Phase 4 populates them from real slskd search results. Proposed shape:
  ```python
  @dataclass(frozen=True)
  class CandidateFile:
      filename: str
      size_bytes: int
      extension: str                 # normalized lower, no dot: 'flac','mp3',...
      bitrate_kbps: int | None       # from slskd bitRate attr if present
      length_seconds: int | None     # from slskd length attr if present
      sample_rate: int | None
      bit_depth: int | None
      is_vbr: bool | None

  @dataclass(frozen=True)
  class Candidate:
      username: str                  # uploader (selector ONLY, never matching)
      folder: str                    # raw folder/dir name from slskd
      files: tuple[CandidateFile, ...]
      free_upload_slots: int | None  # selector-only
      upload_speed: int | None       # selector-only
      # derived by release_parse (populated at construction):
      parsed_artist: str | None
      parsed_album: str | None
      parsed_year: int | None
      parsed_format: str | None
      # helpers: audio_file_count, file_titles, audio_files()
  ```
- **`Manifest` (`core/manifest.py`):** `Manifest(artist, album, track_count, track_titles: tuple[str,...] | None, kind, year=None)` — the normalized authoritative target, built by the adapter from the gap's `foreign_id` (MBID). For Phase 3, fixtures provide `Manifest` JSON directly; the adapter's `get_manifest` impl can be a thin Phase-3 addition or deferred to Phase 4 wiring as long as the dataclass + matcher are proven now (Q2).
- **`Profile` (`core/manifest.py` or `quality.py`):** `Profile(allowed: frozenset[int], cutoff_rank: int)` over a neutral `QualityRank` ladder.
- **Matching is pure and deterministic** (no I/O, no clock, no network); **selection is dumb and swappable** (sorts accepted candidates by format preference then uploader free-slots/speed). Never mix them.
- **`get_quality_profile` is implemented this phase** (QUAL-01); `get_manifest` may be implemented now or its impl deferred to Phase 4 — but the `Manifest` shape and the matcher that consumes it MUST be complete and tested this phase.

### §9 — Books (Readarr) best-effort
- The **same matcher abstraction** handles books: `Manifest(author, title, …)` instead of `(artist, album)`; sub-distances become author-distance + title-distance + format gate. Track-count/track-title sub-distances don't apply (a book is one file) — the weighted average omits them, exactly like the "manifest has no track_titles" path in §2.
- **Music first.** Per CLAUDE.md + seed + ROADMAP, the music path must be complete and green end-to-end before the books branch is exercised. Books ride the same `gate.py`/`matching.py` via the neutral `Manifest`/`Candidate` types; the Readarr adapter supplies a book `Manifest` + book `Profile` (format ladder). Books degrade gracefully and **never gate music** (Phase 2's breaker policy continues upstream).

## Runtime State Inventory

> Phase 3 is **greenfield gating logic** — new pure modules + new adapter methods. It is not a rename/refactor/migration. No stored data, live-service config, OS-registered state, secrets, or build artifacts are renamed or migrated by this phase.
- **Stored data:** None new requiring migration. The `items` ledger already carries `foreign_id` + `quality_profile_id` from Phase 2 (**verified by reading schema.sql**) — Phase 3 *reads* them; no schema change required for the matcher. (A future `attempts`/decisions table is Phase 4-6, explicitly not added here per schema.sql's Phase-2-scope comment.)
- **Live service config:** None.
- **OS-registered state:** None.
- **Secrets/env vars:** New **config keys** added (thresholds/weights/fake-FLAC floors via `Settings.from_env()`), but these are new tunables, not renames of existing secrets. No existing env var changes.
- **Build artifacts:** New runtime dep (`rapidfuzz`, optionally `guessit`) added to `requirements.txt` → the CI image rebuilds normally; no stale artifact to purge.

## Common Pitfalls

### Pitfall 1: Trusting candidate self-description over the manifest
**What goes wrong:** Scoring the candidate's claimed year/edition/format as truth, accepting a confidently-mislabeled folder. (PITFALLS Pitfall 2 — incorrect matches.)
**Why it happens:** Folder names look authoritative; it's tempting to match year-to-year.
**How to avoid:** Anchor every sub-distance on the manifest; use candidate tokens only as the *thing being matched*. Zero-weight candidate-only facets (year/label/catalog).
**Warning signs:** A weight named `year`/`catalognum` with nonzero value in `MatchConfig`.

### Pitfall 2: *arr field names leaking into core/ (firewall break)
**What goes wrong:** `core/quality.py` reads `profile["items"][i]["allowed"]` → couples core to Lidarr JSON, breaks the Phase-2 firewall and its grep test.
**How to avoid:** Adapter returns a normalized `Profile`/`Manifest`; extend the firewall grep test to the new core modules.
**Warning signs:** `profileId`, `foreignAlbumId`, `qualityProfileId`, `records`, `items[`…`]["allowed"]`, `cutoff` appearing under `app/core/`.

### Pitfall 3: Over-declining the entire corpus by setting thresholds blind
**What goes wrong:** A too-strict `strong_thresh` declines even known-good matches → Curator acquires nothing.
**How to avoid:** Calibrate on the labeled corpus: known-good MUST land ≤ threshold, all bad MUST land >. Adjust the number, never the test. Start conservative, document the calibration in the plan.
**Warning signs:** `test_known_good_accepts` failing → tune the threshold/weights, not the assertion.

### Pitfall 4: Rejecting valid FLAC on missing attribute data
**What goes wrong:** A real FLAC with no `length` attribute fails the bytes/sec check and is rejected.
**How to avoid:** **Skip** any heuristic whose inputs are absent; only reject on present-and-bad data. Document each check's "data absent → skip" branch.
**Warning signs:** Rejections whose reason references a `None` input.

### Pitfall 5: Coupling matching to selection
**What goes wrong:** A faster uploader's worse-matching folder gets picked because speed bled into the score.
**How to avoid:** `matching.score()` takes no uploader fields; `selector` sorts only already-accepted candidates. `Candidate.username/upload_speed/free_upload_slots` are read **only** in `selector.py`.

## Code Examples

### Quality-rank gate (pure)
```python
# app/core/quality.py  — Profile arrives ALREADY normalized from the adapter (no *arr JSON here)
def gate(candidate, profile) -> tuple[bool, str]:
    for f in candidate.audio_files():
        rank = rank_for(f.extension, f.bitrate_kbps)   # extension+bitrate -> neutral QualityRank int
        if rank is None or rank not in profile.allowed:
            return False, f"quality REJECT: {f.filename} not in profile allowed set"
        if rank < profile.cutoff_rank:
            return False, f"quality REJECT: {f.filename} rank {rank} below cutoff {profile.cutoff_rank} (no downgrade)"
    return True, "quality OK: all audio files >= cutoff"
```

### Coarse fake-FLAC check (pure)
```python
# app/core/fakeflac.py
def check(candidate, min_kbps: int = 400) -> tuple[bool, str]:
    for f in candidate.audio_files():
        if f.extension != "flac":
            continue
        if f.length_seconds:   # data absent -> skip this sub-check (Pitfall 4)
            eff = (f.size_bytes * 8) / f.length_seconds / 1000
            if eff < min_kbps:
                return False, f"fakeflac REJECT: {f.filename} effective {eff:.0f} kbps < {min_kbps} floor"
        if f.bitrate_kbps and f.bitrate_kbps in (128, 192, 256, 320):
            return False, f"fakeflac REJECT: {f.filename} claims lossy bitrate {f.bitrate_kbps} as FLAC"
    if _has_lossy_source_token(candidate.folder):
        return False, "fakeflac REJECT: lossy source token in FLAC candidate folder name"
    return True, "fakeflac OK"
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Soularr opaque filename-ratio matching (≥0.8) | Explainable ported beets distance + reason strings + rec-gap | this phase | Hand-tunable thresholds; the prior pain point removed |
| `fuzzywuzzy`/`thefuzz` | `rapidfuzz` 3.x | ~2021+ | C-speed, maintained, manylinux wheels |
| `pydantic` v1 `BaseSettings` | `pydantic-settings` 2.x (separate package) | pydantic v2 (2023) | Project may adopt later; Phase 3 reuses the existing hand-rolled `Settings.from_env()` to avoid a parallel config path |

**Deprecated/outdated:** `fuzzywuzzy` (use `rapidfuzz`); depending on `beets` as a library for its matcher (port the design instead).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Simple `track_count_distance` (vs beets' split missing/unmatched) suffices for the 1σ center | §1/§2 | If incomplete grabs slip through, split into missing(heavier)/extra(lighter) penalties — corpus catches it |
| A2 | Starting thresholds `strong=0.15`, `rec_gap=0.10` separate good from bad on the corpus | §3 | Mis-calibration → over/under-declining; MUST be calibrated against the labeled corpus, tune the number not the test |
| A3 | slskd per-file attrs expose bitRate/length/sampleRate/bitDepth and result-level username/uploadSpeed/free-slots | §5/§8 | Phase 3 only fixes the `CandidateFile` shape; exact JSON keys confirmed against live slskd swagger in Phase 4 — non-blocking |
| A4 | Lidarr `GET /api/v1/qualityprofile/{id}` returns ordered `items[]` (allowed bool + nested quality) + `cutoff` id | §5 | Confirm field shapes against live Lidarr at plan time; stays inside lidarr.py so a wrong guess is a localized fix |
| A5 | Custom regex tokenizer covers the ~1σ naming center; guessit is fallback-only | §4 | If folder naming is chaotic (non_latin/odd cases fail), adopt guessit (behind legitimacy checkpoint) |
| A6 | Fake-FLAC floor `400 kbps` effective separates real FLAC from re-wrapped lossy without false-rejecting quiet/classical | §6 | Calibrate on `fake_flac` corpus; tune config floor |
| A7 | `rapidfuzz`/`guessit`/`pydantic-settings` legitimacy (slopcheck couldn't run offline) | Stack/Audit | Planner gates each install behind human-verify (Phase 2 precedent) |
| A8 | guessit 3.8.x exact version/deps (pypi json fetch rate-limited this pass) | Stack | guessit is fallback-only; re-confirm at plan time if adopted — non-blocking |

## Open Questions

1. **Config mechanism for thresholds.**
   - What we know: `app/config.py` uses a hand-rolled frozen `@dataclass Settings` with `from_env()` (NOT pydantic-settings, despite STACK.md listing it). It snapshots env at construction (WR-01 fix) and tests rebuild via `Settings.from_env()`.
   - Recommendation: extend that existing `Settings` with the new threshold/weight/fake-FLAC fields rather than introduce pydantic-settings now — one config pattern, consistent with Phase 2.
2. **Implement `get_manifest` this phase or defer the impl to Phase 4?**
   - What we know: the `Manifest` shape + matcher MUST be complete/tested now; the matcher is provable against fixture `Manifest` JSON without a live MB call.
   - Recommendation: define + test the `Manifest` dataclass and matcher now; implement the adapter `get_manifest` impl now if MB track-list retrieval is cheap, else stub it and wire in Phase 4. Either keeps Phase 3 offline-provable.
3. **Does MB/Lidarr reliably return a per-track title list for the manifest?**
   - What we know: matcher omits the track-title sub-distance gracefully when absent (§2).
   - Recommendation: design for both; include a manifest-without-titles corpus case to prove the omission path.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `rapidfuzz` | matching fuzzy distance | ✗ (offline sandbox; installs in CI/NAS) | 3.13.x target | `difflib.SequenceMatcher` (degraded, no token-set) |
| `guessit` | release-name tokenize (optional) | ✗ | 3.8.x target | custom regex tokenizer (recommended primary anyway) |
| Python 3.12 | runtime (CI/NAS) | ✗ in sandbox (3.9) | 3.12 | tests run in CI/NAS, not sandbox (Phase 2 precedent) |
| Live slskd / Lidarr | NOT needed Phase 3 | — | — | entire phase is offline against fixtures |

**Missing dependencies with no fallback:** none (matcher provable offline; CI/NAS runs the real install + tests, exactly as Phase 2).
**Missing dependencies with fallback:** `rapidfuzz`→`difflib` (degraded), `guessit`→custom regex (preferred).

## Validation Architecture

> Nyquist validation is ENABLED (config.json `workflow.nyquist_validation: true`). Phase 3 is the *ideal* phase for it: a pure function `(candidate, manifest, profile) → (decision, score, reasons)` proven against a labeled corpus, zero network.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` (already in `app/requirements-dev.txt`, Phase 2) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]` with `pythonpath=["app"]` — Phase 2 used flat imports; confirm/keep) |
| Quick run command | `cd app && python -m pytest tests/test_matching.py tests/test_quality.py tests/test_fakeflac.py tests/test_release_parse.py -x -q` |
| Full suite command | `cd app && python -m pytest -q` |

### The labeled fixture corpus (the spine of validation — Wave 0 deliverable)
Mirror the Phase-2 offline-fixture pattern (`tests/fixtures/*.json` + the `load_fixture` conftest fixture). Create `tests/fixtures/candidates/` with slskd-shaped JSON, each paired with the `Manifest` + `Profile` it's scored against, every fixture labeled with its expected decision:

| Fixture | Expected decision | Proves |
|---------|-------------------|--------|
| `known_good_flac` | ACCEPT | correct full FLAC album, profile-safe → strong match (criterion 1) |
| `known_good_mp3_320` | ACCEPT | profile-acceptable lossy passes when cutoff allows (QUAL-02) |
| `incomplete_tracks` | DECLINE | 4/12 files → track-count penalty > threshold (criterion 2, completeness) |
| `wrong_album` | DECLINE | high artist/album distance (MATCH-02) |
| `wrong_edition_ambiguous` | DECLINE | two near-tie candidates → rec-gap fails (precision over recall) |
| `below_cutoff` | DECLINE | MP3-128 vs FLAC cutoff → quality gate (QUAL-02, criterion 3) |
| `fake_flac` | DECLINE | FLAC with ~300 kbps effective → fake-FLAC heuristic (QUAL-03, criterion 4) |
| `fake_flac_source_token` | DECLINE | `.flac` in a `(MP3 320)` folder → source-tag sanity |
| `non_latin` | ACCEPT (or DECLINE if genuinely different script) | NFKD fold + token_set_ratio path |
| `manifest_without_track_titles` | ACCEPT | track-title sub-distance omitted gracefully (§2) |
| `garbage_metadata` | DECLINE (no crash) | unparseable artist/album → max penalty, graceful |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MATCH-01 | scores identity incl. track-count, selects best | unit | `pytest tests/test_matching.py -x` | ❌ Wave 0 |
| MATCH-02 | declines below confidence threshold | unit | `pytest tests/test_gate.py::test_declines_below_threshold -x` | ❌ Wave 0 |
| MATCH-02 | declines ambiguous twins (rec-gap) | unit | `pytest tests/test_gate.py::test_declines_ambiguous -x` | ❌ Wave 0 |
| QUAL-01 | reads profile/cutoff via adapter (normalized) | unit | `pytest tests/test_quality.py::test_profile_normalized -x` | ❌ Wave 0 |
| QUAL-02 | rejects below-cutoff format/bitrate (no downgrade) | unit | `pytest tests/test_quality.py -x` | ❌ Wave 0 |
| QUAL-03 | rejects fake/transcoded FLAC (coarse) | unit | `pytest tests/test_fakeflac.py -x` | ❌ Wave 0 |
| firewall | no *arr field names in core/ | unit | extend Phase-2 firewall grep test to new modules | ⚠️ extend existing |

### Sampling Rate
- **Per task commit:** the quick run command (matching+quality+fakeflac+release_parse) — < 5 s, no network.
- **Per wave merge:** full suite (`pytest -q`) green including the extended firewall grep test.
- **Phase gate:** full suite green + every corpus fixture's labeled decision matches before `/gsd:verify-work`. The four ROADMAP success criteria each map to a corpus fixture above.

### Wave 0 Gaps
- [ ] `tests/fixtures/candidates/*.json` + paired `manifests/` + `profiles/` — the labeled corpus (the critical Wave 0 artifact).
- [ ] `app/core/candidate.py` + `manifest.py` — dataclasses the corpus deserializes into (the Phase 3→4 contract).
- [ ] `tests/test_matching.py`, `test_quality.py`, `test_fakeflac.py`, `test_release_parse.py`, `test_gate.py` — covering MATCH-01/02, QUAL-01/02/03.
- [ ] Extend the existing Phase-2 firewall grep test to include the new `app/core/` modules.
- [ ] Confirm `[tool.pytest.ini_options]` `pythonpath=["app"]` in `pyproject.toml` (Phase 2 ran pytest with flat imports — likely already present).
- [ ] `rapidfuzz` install (CI/NAS): `pip install rapidfuzz==3.13.x` — gated by package-legitimacy checkpoint.
- [ ] (Optional) `hypothesis` for property-based monotonicity tests (e.g. "more matching tracks never increases distance").

## Security Domain

> `security_enforcement` was not present as a key in `.planning/config.json` this pass; Phase 3 is pure offline computation with no new network surface, no auth, no untrusted deserialization of remote data at runtime (fixtures are local, trusted test data). Controls are minimal but real.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Phase 3 makes no auth decisions (adapter `X-Api-Key` is Phase 2, unchanged) |
| V3 Session Management | no | — |
| V4 Access Control | no | — |
| V5 Input Validation | yes | Candidate/Manifest/Profile parsing must defend against malformed/missing fields → graceful max-penalty/decline, never crash (mirrors Phase 2 defensive `_map`). slskd-supplied strings are untrusted free text — used only for fuzzing/decisions, never `eval`/SQL/path ops in this phase. |
| V6 Cryptography | no | — |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/garbage candidate metadata crashes the gate | Denial of Service | Defensive parsing → decline+log, never raise into the loop (Phase 2 pattern) |
| Hostile folder/file name (path traversal `../`, control chars) used downstream | Tampering | Phase 3 treats names as opaque match strings only; **Phase 4** (which turns names into staging paths) owns path-traversal sanitization — flag this forward to Phase 4 |
| ReDoS via crafted folder name in the regex tokenizer | Denial of Service | Keep tokenizer regexes anchored/bounded (no catastrophic backtracking); test with pathological inputs |

## Project Constraints (from CLAUDE.md)
- **Platform `linux/amd64` only** (Synology DS423+, Intel J4125) — every new dep MUST ship a `manylinux` x86_64 wheel (rapidfuzz does; guessit is pure-Python). No source builds on-device.
- **Quality: defer to Lidarr quality profiles/cutoffs; gate candidates BEFORE download; never downgrade.** — exactly QUAL-01/02; the gate is pre-download by construction.
- **Precision/fallback-only, fully hands-off; no manual approval queues.** — two-outcome accept/decline, no human-adjudicated medium tier.
- **State store is SQLite** — Phase 3 reads existing ledger columns (`foreign_id`, `quality_profile_id`); no schema change required.
- **Books (Readarr) best-effort, isolated behind the *-arr-agnostic adapter; music must work first.** — same matcher abstraction, music-first, never gates music.
- **Firewall (Phase 2, locked):** *arr field names live ONLY in `app/adapters/`; `core`/`state` see only neutral types — enforced by the grep test (extend it).

## Sources

### Primary (HIGH confidence)
- Curator codebase — `app/adapters/base.py` (GapItem + ArrAdapter Protocol with stubbed `get_quality_profile`), `lidarr.py` (`profileId`/`foreignAlbumId` verified, the firewall), `readarr.py`, `core/gap_detector.py`, `state/schema.sql` (ledger columns, Phase-2-scope comment), `state/repo.py`, `tests/conftest.py` + `tests/test_gap_detector.py` (offline-fixture/FakeAdapter test pattern), `config.py` (hand-rolled `Settings.from_env()`), `requirements.txt`/`requirements-dev.txt` (Phase-2 package-legitimacy checkpoint precedent).
- `.planning/phases/phase-3/RESEARCH-SEED.md` — binding owner directives.
- `.planning/ROADMAP.md` (Phase 3 success criteria), `.planning/REQUIREMENTS.md` (QUAL/MATCH IDs), `.planning/research/STACK.md` (rapidfuzz/guessit/pydantic-settings pre-selection; *arr qualityprofile/command API table), `.planning/research/PITFALLS.md` (Pitfalls 2/3/9 — matching/quality/completeness), CLAUDE.md.
- beets documentation — `[CITED: beets.readthedocs.io/en/stable/reference/config.html#match]` distance_weights + recommendation thresholds (the model being ported); beets LICENSE = MIT.
- PyPI — `rapidfuzz` latest 3.13.x with cp312 manylinux x86_64 wheels `[VERIFIED: PyPI]`.

### Secondary (MEDIUM confidence)
- beets `Distance` model internals (weighted-average penalties, rec-gap, missing/unmatched track penalties) — well-established design, cross-referenced against official config docs.
- Soulseek/slskd per-file attribute schema (bitRate/length/sampleRate/bitDepth; result-level username/uploadSpeed/free-slots) — stable, long-documented; exact JSON keys to be confirmed against live slskd swagger in Phase 4 (non-blocking; Phase 3 owns the dataclass contract).
- Lidarr `GET /api/v1/qualityprofile/{id}` ordered `items[]`+`cutoff` shape — from STACK.md *arr API table + Servarr docs; confirm exact keys live at plan time.

### Tertiary (LOW confidence)
- Exact starting threshold/weight/fake-FLAC-floor numbers — `[ASSUMED]`, to be calibrated against the labeled corpus (this is by design, not a gap).
- `guessit` exact 3.8.x version/deps — pypi json fetch rate-limited this pass; non-blocking (guessit is fallback-only).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — rapidfuzz verified on PyPI with cp312 wheels and already pre-selected in STACK.md; beets model is MIT and documented; port-not-depend decision well-justified.
- Architecture: HIGH — fits the locked Phase-2 firewall + offline-fixture pattern exactly; pure-function/offline-testable directive directly satisfiable.
- Pitfalls: HIGH — drawn from the firewall constraint, the anchoring rule, the precision-over-recall directive, and PITFALLS.md Pitfalls 2/3/9.
- Exact thresholds/weights & slskd JSON keys: MEDIUM/LOW — intentionally calibration-driven (corpus) and Phase-4-confirmable; Phase 3 owns the contract, so these are non-blocking.

**Research date:** 2026-05-30
**Valid until:** ~2026-06-29 (30 days; rapidfuzz/guessit/beets-model are stable; re-pin exact patch versions at plan time via `pip index versions`).
