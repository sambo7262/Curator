# Phase 3 Research Seed — Matching & Quality Gating

> Pre-planning directives captured before Phase 3 research/discuss runs.
> The Phase 3 researcher (`gsd-phase-researcher`) and planner MUST read and address these.

## Directive: evaluate and port the beets distance model

Phase 3's hard core is matching messy slskd candidate folders to the authoritative
*arr/MusicBrainz track manifest. **Do not invent a scoring model from scratch.** The
**beets autotagger album-distance model** is mature prior art for almost exactly this
problem and must be evaluated as the basis for Curator's matcher:

- **Track-count penalty** — distance contribution when candidate audio-file count ≠ MB track count (drives the completeness criterion).
- **Per-track title distance** — string distance per track, aggregated.
- **Weighted aggregation** — artist/album/track sub-distances combined into a single tunable score with explicit weights.
- **Recommendation tiers** — strong/medium/none thresholds → maps to Curator's "accept best / decline when unsure."

Also survey **MusicBrainz Picard** matching logic as a secondary reference.

Research must answer:
1. Can beets' distance model be used as a library/dependency, or should its design be ported into Curator's own pure-function matcher? (license, fit, footprint)
2. What does beets weight and how — and which weights need to change for Soulseek's noisier, free-text, sometimes-incomplete-metadata candidates vs beets' cleaner local-file case?
3. How beets handles multi-disc / track grouping and edition/release disambiguation.

## Companion principles (from owner discussion, 2026-05-30)

- **Anchor on the canonical *arr/MusicBrainz manifest, never the candidate's self-description.** Lidarr already holds MBIDs + full track list per gap; matching is "does this folder correspond to this known manifest?" not open-ended.
- **Explainable weighted scoring, no ML/black box** — every sub-score emits a reason string so thresholds can be hand-tuned (Soularr's opacity was a prior pain point).
- **Precision over recall is a feature** — fallback-only + runs-forever means declining a sketchy candidate is cheap (item retries / Usenet gets it). Set conservative thresholds, tune up.
- **Target the ~1σ center of the distribution, NOT the 2–3σ tail (owner directive, 2026-05-30).** The goal is to acquire missing music *easily* with zero hand-holding. By design, Curator should accept the high-confidence, obvious matches (the easy center) and decline everything ambiguous. Recall on the long tail is *intentionally* sacrificed — chasing the last ~5% (deluxe editions, compilations, ambiguous track counts, borderline fakes) is explicitly out of scope. The confidence threshold should be set conservatively HIGH so only "clearly correct" matches pass. This aligns with the project's no-manual-approval-queue, fully-hands-off principle: a human is never asked to adjudicate a borderline match — it's simply declined and retried/left for Usenet.
- **Build the matcher test-first against a labeled fixture corpus** of real slskd search-result JSON (known-good + incomplete + wrong-edition + fake-FLAC + non-Latin), as a pure function `(candidate, manifest, profile) → (decision, score, reasons)` — deterministic, no live Soulseek needed.
- **Separate matching (hard, pure) from selection (uploader speed/slots/format preference — dumb, swappable).**
- **Fake-FLAC = coarse heuristics only** (size-per-duration, claimed-bitrate sanity). Spectral/frequency analysis (QUAL-04) is explicitly v2/out-of-scope.
- **Reuse libraries for the sub-pieces:** RapidFuzz (`token_set_ratio`) for fuzzy string match; a guessit-style parser for release-name tokenization.

## Phase 2 hook
Phase 2's dedup identity key is the canonical *arr/MusicBrainz identity (MBIDs). Ensure the
ledger references the release so Phase 3 has the target manifest ready to match against.
