# Curator matcher — the phase's hard core: a PURE, deterministic, explainable weighted-distance
# scorer ported from the beets autotagger album-distance model (beets is MIT; this is a clean
# reimplementation, RESEARCH §1). It answers ONE question: "does this slskd candidate folder
# correspond to THIS authoritative manifest?" — anchoring every sub-distance on the manifest, never
# on the candidate's self-description (anchoring rule, Pitfall 1).
#
# This is the core side of the firewall (PITFALL #6): it imports ONLY stdlib (unicodedata),
# rapidfuzz.fuzz, and the neutral Candidate/Manifest contract types. It carries ZERO *arr / wire
# vocabulary and NEVER reads candidate.username / upload_speed / free_upload_slots — those are
# SELECTOR-only (Pitfall 5 / THREAT T-03-06); uploader speed must never bleed into the match score.
#
# No I/O, no network, no clock. Every sub-score and the final decision emit a human-readable reason
# string (Soularr's opacity was the prior pain point — RESEARCH §3, lines 328-335).
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

from rapidfuzz import fuzz

from core.candidate import Candidate
from core.manifest import Manifest

# Public type aliases for the two return shapes (documentation, not enforcement).
Scored = Tuple[float, Candidate, List[str]]  # one element of the recommend() input list


@dataclass(frozen=True)
class MatchConfig:
    """Tunable weights + thresholds for the weighted-distance scorer (SP-1 frozen dataclass).

    Defaults MUST equal config.Settings' match_* defaults (RESEARCH 207-215) so behavior with no
    env override is identical to the hard-coded scorer; the gate layer (plan 03-05) reads Settings
    and builds a MatchConfig from it, letting the owner tune thresholds/weights WITHOUT a rebuild.

    Weights are adapted for Soulseek noise (RESEARCH §2): track-count and track-title coverage are
    the strongest authenticity signals when free-text folder tags are absent/lying, so they are
    RAISED to 4.0 vs artist/album 3.0 — a folder with the right name but the wrong file count is the
    classic incomplete/wrong grab the seed wants declined. Candidate-self-described facets
    (year/label/catalog/media/source) are DROPPED entirely (anchoring rule, Pitfall 1) — there is
    deliberately NO weight named year/label/catalognum/country here.
    """

    w_artist: float = 3.0
    w_album: float = 3.0
    w_track_count: float = 4.0       # RAISED vs beets — completeness is paramount for slskd
    w_track_titles: float = 4.0      # RAISED — per-track coverage is the strongest authenticity signal
    strong_thresh: float = 0.15      # ACCEPT only if total distance <= this (conservative ~1σ)
    rec_gap_thresh: float = 0.10     # runner-up must be >= this much worse, else ambiguous -> decline
    same_album_thresh: float = 0.30  # a within-gap rival is a DIFFERENT release (ambiguity) only if its
    #                                  (artist, album) is fuzzily THIS far from the best. Edition/year/
    #                                  spelling variants of the SAME album fold under it and are treated
    #                                  as same-release copies (the selector picks the source), not
    #                                  ambiguity — exact-key equality false-declined them live (2026-06).
    max_track_ratio: float = 1.5     # hard oversize guard: a candidate with MORE than this multiple of
    #                                  the manifest's track count is declined outright (a 40-track deluxe
    #                                  BOX is not the ~10-track album the gap wants, even when the names
    #                                  match perfectly — the weighted average alone lets it slip under
    #                                  strong_thresh). 0 disables. Live 2026-06: Queen 'The Miracle' box.


def _norm(s: Optional[str]) -> str:
    """NFKD-fold + strip combining marks + lower/strip so diacritic/non-Latin names compare cleanly.

    None / empty -> "" (so a garbage-metadata candidate folds to "" and yields max penalty below,
    rather than crashing). Mirrors release_parse._fold so both sides of the fuzz are folded the same.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _str_distance(a: Optional[str], b: Optional[str]) -> float:
    """1 - token_set_ratio/100, in [0,1] (0 = identical). Order/duplication tolerant.

    token_set_ratio is chosen over ratio/partial_ratio because slskd names reorder and duplicate
    tokens ("Artist - Album (2007) [FLAC]" noise). An empty/None side (garbage metadata) yields 1.0
    (max penalty) — token_set_ratio("", "radiohead") == 0 — never a crash (SP-3 graceful).
    """
    return 1.0 - (fuzz.token_set_ratio(_norm(a), _norm(b)) / 100.0)


def _same_release(a: Candidate, b: Candidate, thresh: float) -> bool:
    """Are two candidates the SAME release (just different copies/editions), fuzzily?

    True iff BOTH the parsed artist AND the parsed album are within `thresh` string-distance of each
    other. This replaces the old exact (artist, album) key equality, which treated every edition /
    year / spelling variant of one album as a different release ("A Kind of Magic" vs "(1986) A Kind
    of Magic" vs "A Kind of Magic (Remastered)") and so false-declined gettable popular titles whose
    variant copies tied within rec_gap (live 2026-06). token_set_ratio tolerance means a variant adds
    a token but stays well under the threshold, while a genuinely different album ("OK Computer" vs
    "Kid A") sits far above it and is still flagged as cross-release ambiguity. Pure; never raises.
    """
    return (
        _str_distance(a.parsed_artist, b.parsed_artist) <= thresh
        and _str_distance(a.parsed_album, b.parsed_album) <= thresh
    )


def _track_count_distance(cand_audio_files: int, manifest_tracks: int) -> float:
    """|cand - manifest| / manifest, clamped to [0,1]. 1.0 when the manifest declares no tracks.

    This is the completeness driver (RESEARCH §1 A1): 4 files for a 12-track album -> 0.667; a
    23-track deluxe edition vs 12 -> clamped to ~0.92. A manifest with track_count <= 0 (e.g. a
    book, where track-count doesn't apply) yields 1.0 here, but the gate omits this sub-distance for
    such manifests in practice — defensively it never divides by zero.
    """
    if manifest_tracks <= 0:
        return 1.0
    return min(1.0, abs(cand_audio_files - manifest_tracks) / manifest_tracks)


def _track_title_coverage(
    file_titles: Tuple[str, ...], manifest_titles: Tuple[str, ...]
) -> Tuple[float, int, int]:
    """Greedy per-manifest-track best-match coverage distance in [0,1] (RESEARCH line 244).

    For each authoritative track title, find its BEST (lowest) string distance to any candidate file
    title; the coverage distance is the mean of those per-track bests. A manifest track with no good
    candidate match contributes a high distance (drives wrong/incomplete albums up). Returns
    (distance, matched_count, total) where matched_count is the number of manifest tracks that found
    a strong (<= 0.30) candidate match — used only for the human-readable reason string.

    Pure and order-insensitive: each manifest track independently picks its nearest candidate title,
    so a reordered or differently-numbered file list ("01 - Airbag" vs "Airbag") still matches. With
    no candidate titles at all, every manifest track scores 1.0 (max penalty).
    """
    total = len(manifest_titles)
    if total == 0:
        return 0.0, 0, 0
    if not file_titles:
        return 1.0, 0, total
    dists = []
    matched = 0
    for mt in manifest_titles:
        best = min(_str_distance(mt, ft) for ft in file_titles)
        dists.append(best)
        if best <= 0.30:
            matched += 1
    return sum(dists) / total, matched, total


def score(
    candidate: Candidate, manifest: Manifest, cfg: MatchConfig = MatchConfig()
) -> Tuple[float, List[str]]:
    """Score a Candidate against a Manifest as a weighted average of sub-distances (MATCH-01).

    distance = Σ(wᵢ·dᵢ) / Σ(wᵢ) over the ACTIVE sub-distances — beets' Distance semantics. Lower is
    better; 0.0 == perfect. Always active: artist, album, track-count. The track-title sub-distance
    is active ONLY when manifest.track_titles is truthy (the graceful-omission path for a manifest
    with no per-track list, or a book where it doesn't apply) — it is OMITTED from BOTH the numerator
    AND the denominator, never penalized (RESEARCH §2).

    Every sub-score appends a reason string in the RESEARCH 328-335 format. Pure; never raises — an
    empty/None parsed_artist or parsed_album folds to "" and yields a 1.0 sub-distance with a reason
    (the garbage_metadata path), not an exception.

    Reads ONLY manifest identity + candidate.parsed_artist/parsed_album/audio_file_count/file_titles.
    It does NOT read the uploader identity/slots/speed fields (Pitfall 5 — selector-only) and does NOT
    score year / label / catalog / format (anchoring rule, Pitfall 1).
    """
    penalties: List[float] = []
    weights: List[float] = []
    reasons: List[str] = []

    # Hard oversize guard (precision over recall): a candidate carrying FAR more audio files than the
    # manifest declares is a deluxe/box-set edition, NOT the album the gap wants — decline it outright
    # (distance 1.0) BEFORE the weighted average can dilute the track-count penalty under strong_thresh
    # when the artist/album names happen to match perfectly. Skipped when the manifest has no track
    # count (track_count <= 0, e.g. a book) or the guard is disabled (max_track_ratio <= 0).
    if (
        cfg.max_track_ratio > 0
        and manifest.track_count > 0
        and candidate.audio_file_count > manifest.track_count * cfg.max_track_ratio
    ):
        return 1.0, [
            f"DECLINE oversized: {candidate.audio_file_count} files vs {manifest.track_count}-track "
            f"album (> {cfg.max_track_ratio:.2f}x — deluxe/box-set guard)"
        ]

    da = _str_distance(candidate.parsed_artist, manifest.artist)
    penalties.append(da)
    weights.append(cfg.w_artist)
    reasons.append(f"artist '{candidate.parsed_artist}' vs '{manifest.artist}' dist={da:.2f}")

    dl = _str_distance(candidate.parsed_album, manifest.album)
    penalties.append(dl)
    weights.append(cfg.w_album)
    reasons.append(f"album '{candidate.parsed_album}' vs '{manifest.album}' dist={dl:.2f}")

    dc = _track_count_distance(candidate.audio_file_count, manifest.track_count)
    penalties.append(dc)
    weights.append(cfg.w_track_count)
    reasons.append(
        f"track-count {candidate.audio_file_count}/{manifest.track_count} dist={dc:.2f}"
    )

    if manifest.track_titles:  # only when the upstream metadata gave us a per-track list
        dt, matched, total = _track_title_coverage(candidate.file_titles, manifest.track_titles)
        penalties.append(dt)
        weights.append(cfg.w_track_titles)
        reasons.append(f"track-title coverage {matched}/{total} dist={dt:.2f}")

    total_weight = sum(weights)
    total = sum(p * w for p, w in zip(penalties, weights)) / total_weight if total_weight else 1.0
    return total, reasons


def recommend(
    scored: List[Scored], cfg: MatchConfig = MatchConfig()
) -> Tuple[str, Optional[Candidate], float, List[str]]:
    """Collapse beets' strong/medium/none tiers into ACCEPT / DECLINE (MATCH-02, precision over recall).

    `scored` is the list of (distance, candidate, reasons) for the candidates that already passed the
    quality + fake-FLAC eligibility gates (supplied pre-filtered by plan 03-05). This function sorts
    them ascending and applies two conservative conditions; ACCEPT requires BOTH:

      1. best distance <= cfg.strong_thresh                         (confident enough), AND
      2. runner-up is at least cfg.rec_gap_thresh worse             (unambiguous winner).

    Otherwise DECLINE — the structural expression of "target the obvious ~1σ center, decline the
    tail; a human is never asked to adjudicate" (RESEARCH §3). The three decision reasons
    (RESEARCH 333) are appended to the chosen/best candidate's reasons so the outcome is explainable.

    Returns (decision, chosen|None, distance, reasons). `chosen` is the best Candidate only on ACCEPT.
    """
    if not scored:
        return ("decline", None, 1.0, ["no eligible candidates"])

    ordered = sorted(scored, key=lambda t: t[0])
    best_d, best_c, best_r = ordered[0]

    if best_d > cfg.strong_thresh:
        return (
            "decline",
            None,
            best_d,
            best_r + [f"DECLINE total={best_d:.2f} > strong={cfg.strong_thresh:.2f}"],
        )

    # Rec-gap ambiguity, BUT album-aware: a near-tie runner-up that is the SAME release as the best
    # (multiple uploaders sharing the same correct album — the common case for any popular title) is
    # NOT ambiguity — the selector picks the best SOURCE among them. Genuine ambiguity is a DIFFERENT
    # release tying within the gap ("which album is this?"). Decline ONLY when a within-gap rival is a
    # different (artist, album) than the best; same-album copies fall through to ACCEPT (RESEARCH §3,
    # the rec-gap was always meant to guard cross-release confusion, not same-release duplication).
    #
    # The same-release test is FUZZY (_same_release), not exact-key: edition/year/spelling variants of
    # ONE album ("A Kind of Magic" vs "(1986) A Kind of Magic") fold under same_album_thresh and are
    # treated as copies, so they no longer false-decline a gettable title (live 2026-06). A genuinely
    # different album stays far above the threshold and still trips the ambiguous decline.
    rivals = [
        c
        for d, c, _ in ordered[1:]
        if (d - best_d) < cfg.rec_gap_thresh
        and not _same_release(best_c, c, cfg.same_album_thresh)
    ]
    if rivals:
        return (
            "decline",
            None,
            best_d,
            best_r
            + [
                f"DECLINE ambiguous: different release within rec_gap "
                f"('{rivals[0].parsed_artist}' / '{rivals[0].parsed_album}')"
            ],
        )

    return (
        "accept",
        best_c,
        best_d,
        best_r + [f"ACCEPT total={best_d:.2f} <= strong={cfg.strong_thresh:.2f}"],
    )
