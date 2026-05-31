"""Phase-3 gate END-TO-END proofs — the integration of MATCH-01/02 + QUAL-01/02/03 composed.

test_gate.py is to gate.evaluate() what test_gap_detector.py is to detect_gaps(): it drives the
WHOLE pipeline — (candidate folders, authoritative Manifest, quality Profile) -> GateResult(decision,
chosen, distance, reasons) — over the labeled fixture corpus (app/tests/fixtures/candidates/INDEX.md)
and asserts each fixture's LABELED outcome, never an invented one (Pitfall 3: calibrate to the labels).

What this module proves end-to-end (composed, not in isolation):
  * MATCH-01/02 — the correct/complete/genuine candidates ACCEPT with a chosen copy; every
    wrong/incomplete/ambiguous/garbage candidate DECLINES (zero false-accepts across the corpus).
  * QUAL-02 BOTH directions — below_cutoff_mp3 (MP3-192) DECLINES at the QUALITY stage, AND
    known_good_mp3_320 (MP3-320) is ACCEPTED against the mp3_320_cutoff profile (the load-bearing
    permit direction — an over-strict ladder that rejected it would be a T-03-12 DoS).
  * QUAL-03 — fake_flac DECLINES at the FAKEFLAC stage (re-wrapped lossy), not as a match failure.
  * Eligibility-before-acceptance — a below-cutoff candidate is excluded BEFORE scoring, so it can
    never slip past as a "good match".
  * Explainability — every GateResult carries a non-empty reason trail (the Soularr-opacity fix).
  * matching != selection (Pitfall 5 / T-03-06) — a source-level grep proves the uploader fields
    (upload_speed / free_upload_slots / username) are read ONLY in selector.py.

Sandbox note: gate.py -> matching.py -> rapidfuzz (absent in the Python 3.9 offline sandbox; present
at CI/NAS Python 3.12). A module-level importorskip keeps pytest green where rapidfuzz is unavailable,
exactly like test_matching.py; the authoritative green is CI/NAS.
"""
import re
from pathlib import Path

import pytest

pytest.importorskip("rapidfuzz", reason="gate composes the matcher (needs rapidfuzz; CI/NAS has it)")

from core.candidate import build_candidate  # noqa: E402  (after importorskip by design)
from core.gate import GateResult, evaluate  # noqa: E402
from core.manifest import Manifest  # noqa: E402
from core.quality import Profile  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[1]   # .../app


# --- local builders (SP-6: a FakeAdapter-style helper supplies normalized Profile/Manifest from the
#     corpus fixtures, so the gate is driven end-to-end with NO live *arr) -------------------------

def _manifest(load_fixture, name: str) -> Manifest:
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


def _profile(load_fixture, name: str) -> Profile:
    """Load a corpus profile fixture (the already-normalized neutral shape) into a Profile."""
    raw = load_fixture(f"candidates/profiles/{name}")
    return Profile(allowed=frozenset(raw["allowed"]), cutoff_rank=raw["cutoff_rank"])


def _candidate(load_fixture, name: str):
    return build_candidate(load_fixture(f"candidates/{name}"))


# The full INDEX.md label table: candidate -> (manifest, profile, expected decision). Every row is the
# LABELED outcome from INDEX.md — the gate must reproduce exactly this, end-to-end.
CORPUS = {
    "known_good_flac":   ("standard_12track", "lossless_only",  "accept"),
    "known_good_alac":   ("standard_12track", "lossless_only",  "accept"),
    "known_good_mp3_320": ("standard_12track", "mp3_320_cutoff", "accept"),  # QUAL-02 PERMIT direction
    "borderline_accept": ("standard_12track", "lossless_only",  "accept"),
    "non_latin":         ("non_latin",        "lossless_only",  "accept"),
    "incomplete_tracks": ("standard_12track", "lossless_only",  "decline"),  # match: track-count
    "wrong_album":       ("standard_12track", "lossless_only",  "decline"),  # match: album
    "wrong_edition":     ("standard_12track", "lossless_only",  "decline"),  # match: track-count
    "fake_flac":         ("standard_12track", "lossless_only",  "decline"),  # fakeflac stage
    "below_cutoff_mp3":  ("standard_12track", "mp3_320_cutoff", "decline"),  # QUAL-02 REJECT direction
    "garbage_metadata":  ("standard_12track", "lossless_only",  "decline"),  # match: max-penalty
    "no_audio_files":    ("standard_12track", "lossless_only",  "decline"),  # match: 0 audio files
}


# === Headline: every corpus fixture grades to its INDEX-labeled decision, end-to-end ==============

@pytest.mark.parametrize("name,manifest_name,profile_name,expected",
                         [(n, m, p, e) for n, (m, p, e) in sorted(CORPUS.items())])
def test_corpus_fixture_grades_to_label(load_fixture, name, manifest_name, profile_name, expected):
    """gate.evaluate([candidate], manifest, profile) reproduces each fixture's labeled decision."""
    cand = _candidate(load_fixture, name)
    man = _manifest(load_fixture, manifest_name)
    prof = _profile(load_fixture, profile_name)

    result = evaluate([cand], man, prof)

    assert isinstance(result, GateResult)
    assert result.decision == expected, (
        f"{name}: expected {expected} got {result.decision} "
        f"(dist={result.distance:.3f}) reasons={result.reasons}"
    )
    # chosen is set iff accept; reasons are ALWAYS non-empty (explainability on every verdict).
    assert (result.chosen is not None) == (expected == "accept")
    if expected == "accept":
        assert result.chosen is cand
    assert result.reasons, f"{name}: GateResult must carry a reason trail"


def test_no_false_accepts_across_full_corpus(load_fixture):
    """Headline guarantee: NOT ONE decline-labeled fixture may accept through the composed gate."""
    mistakes = []
    for name, (manifest_name, profile_name, expected) in sorted(CORPUS.items()):
        cand = _candidate(load_fixture, name)
        man = _manifest(load_fixture, manifest_name)
        prof = _profile(load_fixture, profile_name)
        result = evaluate([cand], man, prof)
        if result.decision != expected:
            mistakes.append((name, expected, result.decision, round(result.distance, 3)))
    assert not mistakes, f"corpus mis-graded end-to-end: {mistakes}"


# === Targeted stage proofs =======================================================================

def test_known_good_accepts(load_fixture):
    """The canonical happy path: a genuine complete FLAC accepts with the chosen copy set."""
    cand = _candidate(load_fixture, "known_good_flac")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([cand], man, prof)
    assert result.decision == "accept"
    assert result.chosen is cand
    assert any("ACCEPT" in r for r in result.reasons)


def test_declines_below_cutoff(load_fixture):
    """QUAL-02 REJECT (eligibility-before-acceptance): a correct/complete but MP3-192 candidate is
    EXCLUDED at the QUALITY stage and never scored — proving quality gates BEFORE matching can accept.
    Run against lossless_only so the exclusion is unambiguous (any lossy is below a lossless cutoff)."""
    cand = _candidate(load_fixture, "below_cutoff_mp3")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([cand], man, prof)
    assert result.decision == "decline"
    assert result.chosen is None
    # the candidate was excluded at the quality stage, not merely out-scored
    assert any("excluded" in r and "quality REJECT" in r for r in result.reasons)


def test_accepts_mp3_320_when_cutoff_allows(load_fixture):
    """QUAL-02 PERMIT direction (the load-bearing mp3_320_cutoff proof, paired with the reject test):
    known_good_mp3_320 differs from known_good_flac ONLY in format, so against the mp3_320_cutoff
    profile (cutoff rank 3) it MUST pass the quality stage AND match end-to-end -> ACCEPT with chosen
    set. A non-accept here means the cutoff/rank ladder over-rejects a profile-acceptable lossy
    candidate (T-03-12 DoS). Calibration discipline (Pitfall 3): tune the ladder, never this test."""
    cand = _candidate(load_fixture, "known_good_mp3_320")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "mp3_320_cutoff")
    result = evaluate([cand], man, prof)
    assert result.decision == "accept", f"permit direction failed: {result.reasons}"
    assert result.chosen is cand
    # it actually passed the quality stage (eligible), it was not declined there
    assert any("eligible" in r for r in result.reasons)


def test_declines_fake_flac(load_fixture):
    """QUAL-03: fake_flac (claims .flac, ~128 kbps effective + lossy source token) is excluded at the
    FAKEFLAC stage — a re-wrapped-lossy reject, NOT a match failure (it would otherwise score well)."""
    cand = _candidate(load_fixture, "fake_flac")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([cand], man, prof)
    assert result.decision == "decline"
    assert result.chosen is None
    assert any("excluded" in r and "fakeflac REJECT" in r for r in result.reasons)


def test_declines_ambiguous(load_fixture):
    """The ambiguous_twin pair, evaluated TOGETHER, declines via the rec-gap branch (not strong-thresh).

    Both twins clear quality+fakeflac and each alone clears strong, but their distances are within
    rec_gap of each other -> ambiguous -> decline. Proves the composed gate routes a near-tie through
    recommend()'s rec-gap branch (matching != selection: selector never even runs on a decline)."""
    a = _candidate(load_fixture, "ambiguous_twin_a")
    b = _candidate(load_fixture, "ambiguous_twin_b")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([a, b], man, prof)
    assert result.decision == "decline"
    assert result.chosen is None
    assert any("ambiguous" in r for r in result.reasons)


def test_declines_incomplete(load_fixture):
    """incomplete_tracks (4 of 12) is eligible on quality but its track-count distance declines it
    at the MATCH stage — eligibility passes, acceptance does not."""
    cand = _candidate(load_fixture, "incomplete_tracks")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([cand], man, prof)
    assert result.decision == "decline"
    assert result.chosen is None
    # it was eligible (quality OK) but declined on the match score, not excluded at the quality stage
    assert any("eligible" in r for r in result.reasons)


def test_empty_candidate_list_declines(load_fixture):
    """No candidates at all -> decline with chosen=None and the explicit no-candidates reason."""
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([], man, prof)
    assert result.decision == "decline"
    assert result.chosen is None
    assert result.reasons


def test_clear_winner_accepts_over_a_worse_eligible(load_fixture):
    """A clear best (known_good_flac) with a worse-but-still-eligible runner-up (a complete but
    minor-noise FLAC at a distance > rec_gap behind) ACCEPTS the clear winner — proving the gate
    reaches recommend()'s accept branch and runs selector exactly once on the winning copy.

    (Two NEAR-IDENTICAL copies of the same album are a genuine rec-gap tie and correctly DECLINE —
    that ambiguity is proven by test_declines_ambiguous; selector's own tie-break ladder is unit-
    tested in test_selector.py. Here we exercise the accept->select path with an unambiguous winner.)"""
    good = _candidate(load_fixture, "known_good_flac")
    # a genuinely worse (but quality-eligible) FLAC: wrong album, so it scores far behind the winner
    # yet still passes the lossless quality gate — it must NOT cause an ambiguous decline.
    worse = _candidate(load_fixture, "wrong_album")
    man = _manifest(load_fixture, "standard_12track")
    prof = _profile(load_fixture, "lossless_only")
    result = evaluate([good, worse], man, prof)
    assert result.decision == "accept", f"clear winner should accept: {result.reasons}"
    assert result.chosen is good
    assert any("selected" in r for r in result.reasons)


# === matching != selection: source-level firewall (Pitfall 5 / T-03-06) ==========================

def _strip_comment(line: str) -> str:
    """Drop the Python (#) comment tail so the source grep ignores documentation mentions."""
    idx = line.find("#")
    return line[:idx] if idx != -1 else line


def test_selector_only_reads_uploader_fields():
    """STRUCTURAL proof of matching != selection: the uploader fields (upload_speed /
    free_upload_slots / username) are READ (attribute access) ONLY in core/selector.py.

    Greps every app/core/*.py for attribute-access of those fields (comment-stripped, so docstring
    mentions are ignored) and asserts the only file with such access is selector.py. The dataclass
    DEFINITION + the build_candidate kwargs in candidate.py use the bare names (not `.name` access),
    so this attribute-access regex correctly admits them while catching any real read elsewhere."""
    uploader_access = re.compile(r"\.(?:upload_speed|free_upload_slots|username)\b")
    offenders = []
    for path in (APP_DIR / "core").rglob("*.py"):
        for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = _strip_comment(raw)
            if uploader_access.search(code) and path.name != "selector.py":
                offenders.append(f"{path}:{n}: {raw.strip()}")
    assert not offenders, (
        "uploader fields read outside selector.py (matching!=selection broken):\n"
        + "\n".join(offenders)
    )


# === Phase-4 firewall: core/acquire.py carries ZERO *arr/slskd wire vocabulary ====================

def test_acquire_has_no_arr_field_names():
    """*arr-agnostic firewall, extended over the Phase-4 composition point (04-04): core/acquire.py
    must contain ZERO *arr/slskd wire vocabulary in EXECUTABLE code. acquire_item orchestrates only
    through the neutral adapter/client method surfaces and the neutral Candidate/Manifest/Profile/
    GateResult/TransferProgress types — the *arr import keys + the slskd transfer/search wire keys all
    stay inside the adapters (lidarr.py/readarr.py/slskd.py), never crossing into core.

    Greps acquire.py line-by-line, comment-stripped via the existing _strip_comment helper (so a
    docstring/comment mention is admitted — acquire.py avoids them in code anyway), for the wire-
    vocabulary token set. This is ADDITIVE: it does not weaken the matching!=selection grep above or
    the *arr-field grep in test_adapter_protocol.py."""
    arr_fields = re.compile(
        r"\b(?:folder|downloadId|albumReleaseId|importMode|X-Api-Key|X-API-Key"
        r"|ManualImport|artistId|albumId|trackIds|searchText|bytesTransferred"
        r"|isComplete|hasFreeUploadSlot)\b"
    )
    acquire_path = APP_DIR / "core" / "acquire.py"
    offenders = []
    for n, raw in enumerate(acquire_path.read_text(encoding="utf-8").splitlines(), start=1):
        code = _strip_comment(raw)
        if arr_fields.search(code):
            offenders.append(f"{acquire_path}:{n}: {raw.strip()}")
    assert not offenders, (
        "core/acquire.py leaked *arr/slskd wire vocabulary into executable code "
        "(the Phase-4 firewall is broken):\n" + "\n".join(offenders)
    )
