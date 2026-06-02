"""Phase-3 matcher proofs — MATCH-01 (weighted-distance scoring) + MATCH-02 (precision-over-recall
accept/decline with strong-thresh + rec-gap).

These close the two headline matching requirements against the labeled fixture corpus
(app/tests/fixtures/candidates/INDEX.md):

  * MATCH-01 — score(candidate, manifest, cfg) is a beets-ported weighted average of
    artist/album/track-count/track-title sub-distances; the best is selected; every sub-score emits
    a human-readable reason string (no black box).
  * MATCH-02 — recommend(scored, cfg) ACCEPTS only when the best distance <= strong_thresh AND the
    runner-up is at least rec_gap_thresh worse; otherwise DECLINES. The headline guarantee is
    ZERO false-accepts across the labeled corpus.

Calibrated thresholds/weights (from config.Settings defaults == MatchConfig defaults, so they are
config-tunable via env WITHOUT a rebuild — RESEARCH §3 lines 323-327):

    strong_thresh   = 0.15   (MATCH_STRONG_THRESH)   accept iff best total distance <= this
    rec_gap_thresh  = 0.10   (MATCH_REC_GAP_THRESH)  runner-up must be this much worse, else ambiguous
    w_artist = 3.0 (MATCH_W_ARTIST), w_album = 3.0 (MATCH_W_ALBUM),
    w_track_count = 4.0 (MATCH_W_TRACK_COUNT), w_track_titles = 4.0 (MATCH_W_TRACK_TITLES)

These numbers were CALIBRATED against the labeled corpus, not invented: known-good lands well under
strong, every wrong/incomplete/ambiguous lands over strong or trips the rec-gap. Per the calibration
discipline (RESEARCH Pitfall 3): if a known-good assertion ever fails, the fix is to tune the
number/weight HERE (and the matching config defaults in config.py), NEVER to weaken the assertion.

Sandbox note: the dev sandbox is Python 3.9 + offline; the authoritative green is CI/NAS Python 3.12.
matching.py imports `rapidfuzz` (absent by default in the 3.9 sandbox), so — exactly like the Phase-2
httpx adapter tests — this module is collected at CI/NAS where rapidfuzz is installed. A module-level
skip keeps `pytest` green when rapidfuzz is unavailable rather than erroring on import.
"""
import pytest

pytest.importorskip("rapidfuzz", reason="matcher needs rapidfuzz (present at CI/NAS, absent in 3.9 sandbox)")

from core.candidate import build_candidate  # noqa: E402  (after importorskip by design)
from core.manifest import Manifest  # noqa: E402
from core.matching import MatchConfig, _same_release, recommend, score  # noqa: E402


# --- local builders (SP-6: keep tests independent of live wiring) -------------------------------

def _manifest(load_fixture, name: str) -> Manifest:
    """Load a corpus manifest fixture into the neutral Manifest dataclass."""
    raw = load_fixture(f"candidates/manifests/{name}")
    titles = raw.get("track_titles")
    return Manifest(
        artist=raw["artist"],
        album=raw["album"],
        track_count=raw["track_count"],
        track_titles=tuple(titles) if titles else None,
        kind=raw.get("kind", "album"),
        year=raw.get("year"),
    )


def _candidate(load_fixture, name: str):
    """Load a corpus candidate fixture through the Phase-3 factory (parsed_* via release_parse)."""
    return build_candidate(load_fixture(f"candidates/{name}"))


# The labeled corpus -> (manifest, expected recommend() decision) per INDEX.md. The two QUAL/fakeflac
# DECLINE cases (fake_flac, below_cutoff_mp3) are gate-layer (03-05) rejections, NOT match failures —
# the matcher would otherwise ACCEPT them, so they are intentionally EXCLUDED from this pure-matching
# corpus table (proven separately in the gate plan). The matching corpus is every fixture whose
# decision is determined by the SCORE, plus the structural decline paths (garbage / no-audio).
ACCEPT_FIXTURES = {
    "known_good_flac": "standard_12track",
    "known_good_alac": "standard_12track",
    "known_good_mp3_320": "standard_12track",
    "borderline_accept": "standard_12track",
    "non_latin": "non_latin",
}

DECLINE_FIXTURES = {
    "incomplete_tracks": "standard_12track",
    "wrong_album": "standard_12track",
    "wrong_edition": "standard_12track",
    "garbage_metadata": "standard_12track",
    "no_audio_files": "standard_12track",
}


# === MATCH-01: score() — weighted-distance sub-scores + reasons ==================================

def test_score_known_good_lands_under_strong(load_fixture):
    """A correct, complete, genuine candidate scores <= strong_thresh with artist/album/track-count reasons."""
    cand = _candidate(load_fixture, "known_good_flac")
    man = _manifest(load_fixture, "standard_12track")
    dist, reasons = score(cand, man)
    assert dist <= MatchConfig().strong_thresh
    # every sub-score emits a non-empty human-readable reason
    assert all(isinstance(r, str) and r for r in reasons)
    joined = " | ".join(reasons)
    assert "artist" in joined and "album" in joined and "track-count" in joined


def test_score_wrong_album_pushes_album_sub_distance_high(load_fixture):
    """A correct-artist / wrong-album candidate scores a HIGH total > strong (album sub-distance large)."""
    cand = _candidate(load_fixture, "wrong_album")  # Radiohead - Kid A vs OK Computer
    man = _manifest(load_fixture, "standard_12track")
    dist, reasons = score(cand, man)
    assert dist > MatchConfig().strong_thresh


def test_score_incomplete_tracks_pushes_track_count_high(load_fixture):
    """Only 4 of 12 tracks -> the track-count sub-distance drives total > strong_thresh."""
    cand = _candidate(load_fixture, "incomplete_tracks")
    man = _manifest(load_fixture, "standard_12track")
    assert cand.audio_file_count == 4  # 4 audio files present
    dist, reasons = score(cand, man)
    assert dist > MatchConfig().strong_thresh
    assert any("track-count 4/12" in r for r in reasons)


def test_score_omits_track_title_term_when_manifest_has_no_titles(load_fixture):
    """track_titles=None -> the track-title sub-distance is OMITTED from the weighted average.

    Proven structurally: the no_titles manifest yields exactly 3 reason lines (artist/album/track-
    count) with NO 'track-title' line, and the denominator excludes w_track_titles. We build a
    synthetic candidate that matches the no_titles manifest's artist/album so only the omission is
    under test.
    """
    man = _manifest(load_fixture, "no_titles")  # Boards of Canada, track_titles=null, 17 tracks
    assert man.track_titles is None
    # a candidate whose parsed artist/album match the manifest; 17 audio files for a clean track-count
    files = [{"filename": f"{i:02d} - Track.flac", "size": 20000000} for i in range(1, 18)]
    cand = build_candidate(
        {"folder": "Boards of Canada - Music Has the Right to Children (1998) [FLAC]", "files": files}
    )
    dist, reasons = score(cand, man)
    assert not any("track-title" in r for r in reasons)  # omitted, not penalized
    assert sum(1 for r in reasons if "dist=" in r) == 3  # artist + album + track-count only
    # sanity: with perfect artist/album/track-count and no title term, total is ~0
    assert dist <= MatchConfig().strong_thresh


def test_score_garbage_metadata_max_penalty_no_crash(load_fixture):
    """A meaningless folder folds to None artist/album -> sub-distance 1.0 with a reason, never raises."""
    cand = _candidate(load_fixture, "garbage_metadata")
    man = _manifest(load_fixture, "standard_12track")
    assert cand.parsed_artist is None  # release_parse folded the garbage to nothing
    dist, reasons = score(cand, man)  # MUST NOT raise
    assert dist > MatchConfig().strong_thresh
    # the empty-name artist sub-distance is the max penalty
    assert any("dist=1.00" in r for r in reasons)


def test_score_returns_only_reason_strings(load_fixture):
    """Every returned reason is a non-empty str (explainability contract — no opaque scores)."""
    cand = _candidate(load_fixture, "known_good_flac")
    man = _manifest(load_fixture, "standard_12track")
    _, reasons = score(cand, man)
    assert reasons and all(isinstance(r, str) and r.strip() for r in reasons)


# === MATCH-02: recommend() — strong-thresh + rec-gap ============================================

def test_recommend_empty_declines():
    """No eligible candidates -> decline with the explicit no-candidates reason."""
    decision, chosen, dist, reasons = recommend([])
    assert decision == "decline"
    assert chosen is None
    assert dist == 1.0
    assert reasons == ["no eligible candidates"]


def test_recommend_best_above_strong_declines(load_fixture):
    """A single best candidate whose distance exceeds strong_thresh -> decline (over-threshold reason)."""
    cand = _candidate(load_fixture, "wrong_album")
    man = _manifest(load_fixture, "standard_12track")
    dist, reasons = score(cand, man)
    decision, chosen, out_d, out_r = recommend([(dist, cand, reasons)])
    assert decision == "decline"
    assert chosen is None
    assert any("DECLINE total=" in r and "> strong=" in r for r in out_r)


def test_recommend_clear_winner_accepts(load_fixture):
    """A clear best <= strong with the runner-up >= rec_gap worse -> accept the best candidate."""
    good = _candidate(load_fixture, "known_good_flac")
    bad = _candidate(load_fixture, "wrong_album")
    man = _manifest(load_fixture, "standard_12track")
    gd, gr = score(good, man)
    bd, br = score(bad, man)
    decision, chosen, out_d, out_r = recommend([(gd, good, gr), (bd, bad, br)])
    assert decision == "accept"
    assert chosen is good
    assert any("ACCEPT total=" in r and "<= strong=" in r for r in out_r)


def test_recommend_same_album_copies_accept_not_ambiguous(load_fixture):
    """Two copies of the SAME release (ambiguous_twin_a/b are identical OK Computer FLAC folders)
    near-tying within rec_gap is NOT ambiguity -> ACCEPT (the selector picks the best SOURCE).

    LIVE REVERSAL (2026-05-31): the original Phase-3 decision declined same-album near-ties as
    'ambiguous'. The live NAS daemon proved that wrong — a Queen 'A Kind of Magic' FLAC scored 0.00
    yet was declined because other uploaders' copies of the SAME album tied within rec_gap, so EVERY
    popular title was thrown away. recommend() is now album-aware: a within-gap rival only triggers
    the ambiguous decline when it is a DIFFERENT (artist, album). Same-album copies fall through to
    ACCEPT; matching != selection still holds (the selector ratifies the source on accept).
    """
    man = _manifest(load_fixture, "standard_12track")
    a = _candidate(load_fixture, "ambiguous_twin_a")
    b = _candidate(load_fixture, "ambiguous_twin_b")
    ad, ar = score(a, man)
    bd, br = score(b, man)
    # precondition: each twin alone clears strong AND they're within rec_gap (a same-album near-tie)
    assert ad <= MatchConfig().strong_thresh and bd <= MatchConfig().strong_thresh
    assert abs(ad - bd) < MatchConfig().rec_gap_thresh
    # precondition: they ARE the same parsed release (so the album-aware branch treats them as copies)
    assert (a.parsed_artist, a.parsed_album) == (b.parsed_artist, b.parsed_album)
    decision, chosen, out_d, out_r = recommend([(ad, a, ar), (bd, b, br)])
    assert decision == "accept"
    assert any("ACCEPT total=" in r for r in out_r)


def test_recommend_different_release_within_gap_declines():
    """Genuine cross-release ambiguity is preserved: two DIFFERENT albums tying within rec_gap ->
    DECLINE (the 'which album is this?' case the rec-gap was always meant to guard). Distances are
    injected so the decision logic is exercised deterministically, independent of scorer calibration.
    """
    a = build_candidate({
        "folder": "Radiohead - OK Computer (1997) [FLAC]",
        "files": [{"filename": "01 - Airbag.flac"}, {"filename": "02 - Paranoid Android.flac"}],
    })
    b = build_candidate({
        "folder": "Radiohead - Kid A (2000) [FLAC]",
        "files": [{"filename": "01 - Everything In Its Right Place.flac"}, {"filename": "02 - Kid A.flac"}],
    })
    assert (a.parsed_artist, a.parsed_album) != (b.parsed_artist, b.parsed_album)
    decision, chosen, out_d, out_r = recommend([(0.10, a, []), (0.12, b, [])])
    assert decision == "decline"
    assert chosen is None
    assert any("different release" in r for r in out_r)


def test_same_release_is_fuzzy_not_exact_key():
    """The rec-gap same-release test (_same_release) is FUZZY: token-varying spellings of ONE album
    (whose EXACT parsed (artist, album) keys differ) are still the same release, so they no longer
    false-decline a gettable title (live 2026-06). A genuinely different album stays different."""
    base = build_candidate({"folder": "Radiohead - OK Computer [FLAC]",
                            "files": [{"filename": "01 - Airbag.flac"}]})
    variant = build_candidate({"folder": "Radiohead - OK Computer OKNOTOK [FLAC]",
                               "files": [{"filename": "01 - Airbag.flac"}]})
    other = build_candidate({"folder": "Radiohead - Kid A [FLAC]",
                             "files": [{"filename": "01 - Everything In Its Right Place.flac"}]})

    # precondition: the variant's EXACT parsed album key DIFFERS from base (old logic = different release)
    assert (base.parsed_artist, base.parsed_album) != (variant.parsed_artist, variant.parsed_album)
    # fuzzy: a token-varying spelling of the same album folds together; a different album does not
    assert _same_release(base, variant, MatchConfig().same_album_thresh) is True
    assert _same_release(base, other, MatchConfig().same_album_thresh) is False


def test_recommend_same_album_token_variant_accepts():
    """End-to-end of the live fix: two copies of the same album with token-varying names tying within
    rec_gap are treated as same-release copies (not ambiguity) -> ACCEPT (the selector picks a source).
    Old exact-key logic declined this as a 'different release'."""
    a = build_candidate({"folder": "Radiohead - OK Computer [FLAC]",
                         "files": [{"filename": "01 - Airbag.flac"}, {"filename": "02 - Paranoid Android.flac"}]})
    b = build_candidate({"folder": "Radiohead - OK Computer OKNOTOK [FLAC]",
                         "files": [{"filename": "01 - Airbag.flac"}, {"filename": "02 - Paranoid Android.flac"}]})
    decision, chosen, _, _ = recommend([(0.05, a, []), (0.10, b, [])])  # within rec_gap (0.10)
    assert decision == "accept"
    assert chosen is a


def test_score_oversized_release_declines_even_when_names_match():
    """Hard oversize guard (live 2026-06, Queen 'The Miracle' box): a candidate with FAR more audio
    files than the manifest declares (40 vs 10, a 4-disc deluxe box) is forced to distance 1.0 — a
    DECLINE — even though the artist + album names match perfectly. Without the guard the weighted
    average would dilute the maxed track-count penalty and slip the box under strong_thresh."""
    man = Manifest(artist="Queen", album="The Miracle", track_count=10, track_titles=None, kind="album")
    box = build_candidate({
        "folder": "Queen - The Miracle [FLAC]",
        "files": [{"filename": f"{i:02d} - Track.flac"} for i in range(40)],
    })
    dist, reasons = score(box, man)
    assert dist == 1.0
    assert any("oversized" in r.lower() for r in reasons)
    decision, chosen, _, _ = recommend([(dist, box, reasons)])
    assert decision == "decline"


def test_score_right_sized_release_not_tripped_by_oversize_guard():
    """The guard does NOT fire at the boundary: a candidate within max_track_ratio (e.g. 12 vs 10, a
    normal edition with a couple of bonus tracks) is scored normally, not force-declined."""
    man = Manifest(artist="Queen", album="The Miracle", track_count=10, track_titles=None, kind="album")
    ok = build_candidate({
        "folder": "Queen - The Miracle [FLAC]",
        "files": [{"filename": f"{i:02d} - Track.flac"} for i in range(12)],  # 12 <= 10 * 1.5
    })
    dist, reasons = score(ok, man)
    assert dist < 1.0
    assert not any("oversized" in r.lower() for r in reasons)


def test_borderline_accept_just_inside_strong(load_fixture):
    """borderline_accept (minor album-name noise 'OK Comp') lands just inside strong -> accept."""
    cand = _candidate(load_fixture, "borderline_accept")
    man = _manifest(load_fixture, "standard_12track")
    dist, reasons = score(cand, man)
    assert dist <= MatchConfig().strong_thresh
    decision, chosen, _, _ = recommend([(dist, cand, reasons)])
    assert decision == "accept"


def test_non_latin_accepts_when_correct(load_fixture):
    """A diacritic folder (Björk / Homogénic) NFKD-folds to match -> accept."""
    cand = _candidate(load_fixture, "non_latin")
    man = _manifest(load_fixture, "non_latin")
    dist, reasons = score(cand, man)
    assert dist <= MatchConfig().strong_thresh
    decision, chosen, _, _ = recommend([(dist, cand, reasons)])
    assert decision == "accept"


# === The headline guarantee: ZERO false-accepts across the labeled corpus =======================

@pytest.mark.parametrize("name,manifest_name", sorted(ACCEPT_FIXTURES.items()))
def test_accept_labeled_fixtures_accept(load_fixture, name, manifest_name):
    """Every ACCEPT-labeled fixture (known_good_*/borderline_accept/non_latin) is accepted by recommend()."""
    cand = _candidate(load_fixture, name)
    man = _manifest(load_fixture, manifest_name)
    dist, reasons = score(cand, man)
    decision, chosen, _, _ = recommend([(dist, cand, reasons)])
    assert decision == "accept", f"{name} should ACCEPT but got {decision} (dist={dist:.3f})"
    assert chosen is cand


@pytest.mark.parametrize("name,manifest_name", sorted(DECLINE_FIXTURES.items()))
def test_decline_labeled_fixtures_decline(load_fixture, name, manifest_name):
    """Every score-driven DECLINE-labeled fixture is declined by recommend() (zero false-accepts)."""
    cand = _candidate(load_fixture, name)
    man = _manifest(load_fixture, manifest_name)
    dist, reasons = score(cand, man)
    decision, chosen, _, _ = recommend([(dist, cand, reasons)])
    assert decision == "decline", f"{name} should DECLINE but got {decision} (dist={dist:.3f})"
    assert chosen is None


def test_no_false_accepts_across_full_corpus(load_fixture):
    """Headline: iterate the full match-corpus; ACCEPT iff and only iff the fixture is ACCEPT-labeled.

    Each candidate is scored against its labeled manifest and run through recommend() individually
    (the ambiguous-twin near-tie is proven separately, since it is only meaningful as a pair). This
    is the zero-false-accepts proof: not one DECLINE-labeled fixture may accept.
    """
    all_cases = {**{n: (m, "accept") for n, m in ACCEPT_FIXTURES.items()},
                 **{n: (m, "decline") for n, m in DECLINE_FIXTURES.items()}}
    false_accepts = []
    for name, (manifest_name, expected) in sorted(all_cases.items()):
        cand = _candidate(load_fixture, name)
        man = _manifest(load_fixture, manifest_name)
        dist, reasons = score(cand, man)
        decision, _, _, _ = recommend([(dist, cand, reasons)])
        if decision != expected:
            false_accepts.append((name, expected, decision, round(dist, 3)))
    assert not false_accepts, f"corpus mis-graded (false accept/decline): {false_accepts}"


# === Optional property test (RESEARCH 571): more matching tracks never increases distance =======

def test_more_matching_tracks_never_increases_distance(load_fixture):
    """Monotonicity: adding a correctly-named track (toward the manifest count) cannot raise distance.

    Build a candidate with k correct tracks for k = 1..12 against the 12-track manifest; the total
    distance must be non-increasing as k grows toward the true count. Guards against a sign error in
    the track-count / coverage sub-distances.
    """
    man = _manifest(load_fixture, "standard_12track")
    titles = man.track_titles
    prev = None
    for k in range(1, 13):
        files = [{"filename": f"{i:02d} - {titles[i-1]}.flac", "size": 20000000} for i in range(1, k + 1)]
        cand = build_candidate({"folder": "Radiohead - OK Computer (1997) [FLAC]", "files": files})
        dist, _ = score(cand, man)
        if prev is not None:
            assert dist <= prev + 1e-9, f"distance rose from {prev} to {dist} at k={k}"
        prev = dist
