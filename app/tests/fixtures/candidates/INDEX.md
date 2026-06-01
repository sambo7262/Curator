# Phase 3 Labeled Fixture Corpus — Index

This is the **label table** for the Phase 3 gating corpus. Every candidate fixture is a
slskd-shaped search result (consumed by `core.candidate.build_candidate`). Each row pairs a
candidate with the **Manifest** it is scored against and the **Profile** its files are gated by,
plus its **expected decision** (`ACCEPT` / `DECLINE`) and the reason. Later waves (matching /
quality / fakeflac / gate) and `verify-work` assert the *labeled* outcome here — never an invented
one (Pitfall 3: calibrate to the labels, tune the number not the test).

> All JSON in this directory is hand-authored, fully offline, and treated as opaque match text
> (THREAT T-03-02 / V5): slskd folder/file strings are never used as filesystem paths in Phase 3.

## Quality rank ladder (neutral, encoded in the profiles)

| rank | quality |
|------|---------|
| 1 | MP3-192 |
| 2 | MP3-256 |
| 3 | MP3-320 |
| 4 | ALAC (lossless) |
| 5 | FLAC (lossless) |

- `profiles/lossless_only.json` — allowed `{4,5}`, cutoff rank `5`. Any lossy file is below cutoff.
- `profiles/mp3_320_cutoff.json` — allowed `{3,4,5}`, cutoff rank `3`. MP3-320+ and lossless pass; MP3-256/192 are below cutoff.

## Manifests

| manifest | artist / album | track_count | track_titles | note |
|----------|----------------|-------------|--------------|------|
| `manifests/standard_12track.json` | Radiohead / OK Computer | 12 | present (12) | the primary ACCEPT-direction target |
| `manifests/no_titles.json` | Boards of Canada / Music Has the Right to Children | 17 | **null (omitted)** | graceful-omission path: matcher skips the title sub-distance |
| `manifests/non_latin.json` | Björk / Homogénic | 10 | present (10) | NFKD-fold robustness target |

## Candidate label table

| candidate | manifest | profile | expected | why |
|-----------|----------|---------|----------|-----|
| `known_good_flac` | standard_12track | lossless_only | **ACCEPT** | correct artist+album, complete 12 tracks, genuine FLAC (~800 kbps) — the canonical happy path |
| `known_good_alac` | standard_12track | lossless_only | **ACCEPT** | same correct/complete album in lossless ALAC (.m4a); ALAC is in the allowed set + at/above cutoff |
| `known_good_mp3_320` | standard_12track | **mp3_320_cutoff** | **ACCEPT** | profile-acceptable lossy passes when the cutoff allows — **QUAL-02 PERMIT direction**; differs from known_good_flac ONLY in format; makes the mp3_320_cutoff profile load-bearing |
| `borderline_accept` | standard_12track | lossless_only | **ACCEPT** | correct+complete genuine FLAC with minor album-name noise ("OK Comp") — lands just inside strong_thresh (the just-above-boundary ACCEPT side) |
| `non_latin` | non_latin | lossless_only | **ACCEPT** | diacritic folder NFKD-folds to match Björk / Homogénic; complete 10 tracks, genuine FLAC — unicode robustness |
| `incomplete_tracks` | standard_12track | lossless_only | **DECLINE** | only 4 of 12 tracks — track-count completeness gap pushes total > strong_thresh |
| `wrong_album` | standard_12track | lossless_only | **DECLINE** | correct artist but wrong album (Kid A vs OK Computer) — album sub-distance too high |
| `wrong_edition` | standard_12track | lossless_only | **DECLINE** | Deluxe/OKNOTOK edition, 23 tracks vs 12 — track-count mismatch declines the long-tail edition |
| `ambiguous_twin_a` | standard_12track | lossless_only | **ACCEPT** | two uploaders sharing the SAME release: same-album near-tie is NOT ambiguity → ACCEPT, selector picks the source (reversed 2026-05-31 after live evidence; only meaningful WITH twin_b in the scored set) |
| `ambiguous_twin_b` | standard_12track | lossless_only | **ACCEPT** | the other identical copy of the same release — the selector ratifies one source (only meaningful WITH twin_a in the scored set) |
| `fake_flac` | standard_12track | lossless_only | **DECLINE** | claims .flac but ~128 kbps effective bytes/sec (< 400 floor) + lossy source token — fakeflac REJECT, not a match failure |
| `below_cutoff_mp3` | standard_12track | **mp3_320_cutoff** (and lossless_only) | **DECLINE** | correct/complete but MP3-192 (rank 1) below cutoff in BOTH profiles — **QUAL-02 REJECT direction** (no-downgrade); straddles the boundary opposite known_good_mp3_320 |
| `garbage_metadata` | standard_12track | lossless_only | **DECLINE** | meaningless folder name folds to None artist/album; matcher scores max-penalty → DECLINE and NEVER throws (error-path robustness) |
| `no_audio_files` | standard_12track | lossless_only | **DECLINE** | only .cue/.log/.jpg/.nfo — audio_file_count == 0, immediate-decline path (nothing to import) |

## Boundary-straddle coverage (Nyquist)

The corpus straddles the QUAL-02 cutoff boundary in **both directions** against the
`mp3_320_cutoff` profile, so the gate is proven to permit *and* reject, not just reject:

- **PERMIT:** `known_good_mp3_320` (MP3-320, rank 3) → **ACCEPT** (at cutoff, allowed).
- **REJECT:** `below_cutoff_mp3` (MP3-192, rank 1) → **DECLINE** (below cutoff).

And the match-confidence boundary is straddled by `borderline_accept` (just inside strong) and
`ambiguous_twin_a`/`ambiguous_twin_b` (same-album near-tie → ACCEPT, selector picks source) vs
`incomplete_tracks` / `wrong_album` / `wrong_edition` (clearly outside → decline). Genuine
cross-release ambiguity (two DIFFERENT albums within rec_gap → decline) is proven in
test_matching.test_recommend_different_release_within_gap_declines.
