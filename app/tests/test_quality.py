"""Phase-3 quality-gate proofs (QUAL-01 firewall-clean Profile + QUAL-02 no-downgrade gate).

These prove the pre-download format/bitrate gate in BOTH directions against the labeled corpus
(app/tests/fixtures/candidates/INDEX.md — calibrate to the labels, tune the ladder not the test;
Pitfall 3):

  - REJECT  : below_cutoff_mp3 (MP3-192, rank 1) vs lossless_only -> DECLINE (no-downgrade).
  - PERMIT  : known_good_mp3_320 (MP3-320, rank 3) vs mp3_320_cutoff -> ACCEPT (cutoff allows it).

The PERMIT direction makes the mp3_320_cutoff profile fixture load-bearing (T-03-12: an over-strict
ladder that false-rejected a profile-acceptable lossy candidate would be a DoS — Curator would
decline a download the profile permits). The REJECT direction enforces QUAL-02 no-downgrade
(T-03-08: a below-cutoff candidate must never slip past as "cutoff met").

quality.py imports NO rapidfuzz (only stdlib + the neutral candidate type), so this module runs in
the Python 3.9 + offline dev sandbox exactly as it will at CI/NAS Python 3.12 — the authoritative
green gate remains CI/NAS, but there is no sandbox skip here.
"""
from core.candidate import build_candidate
from core.quality import (
    RANK_FLAC,
    RANK_LOSSLESS,
    RANK_MP3_192,
    RANK_MP3_256,
    RANK_MP3_320,
    Profile,
    gate,
    rank_for,
)

LOSSLESS_ONLY = Profile(allowed=frozenset({4, 5}), cutoff_rank=5)
MP3_320_CUTOFF = Profile(allowed=frozenset({3, 4, 5}), cutoff_rank=3)


def _candidate(load_fixture, name):
    return build_candidate(load_fixture(f"candidates/{name}"))


# ---- rank_for() ladder -------------------------------------------------------

def test_rank_for_flac_is_lossless_regardless_of_bitrate():
    """A lossless extension maps to the lossless rank ignoring whatever bitrate is claimed."""
    assert rank_for("flac", None) == RANK_LOSSLESS
    assert rank_for("flac", 900) == RANK_LOSSLESS
    # claimed-low bitrate does NOT demote a FLAC here — that's fakeflac's job, not the ladder's.
    assert rank_for("flac", 128) == RANK_LOSSLESS


def test_rank_for_lossless_aliases():
    """alac/m4a/wav/ape all rank as lossless (m4a is how ALAC arrives via the file extension)."""
    for ext in ("alac", "m4a", "wav", "ape"):
        assert rank_for(ext, None) == RANK_LOSSLESS


def test_rank_for_mp3_buckets_by_bitrate():
    """mp3 ranks by bitrate bucket: 320 -> mp3-320, 256 -> mp3-256, 192/lower -> mp3-192."""
    assert rank_for("mp3", 320) == RANK_MP3_320
    assert rank_for("mp3", 256) == RANK_MP3_256
    assert rank_for("mp3", 192) == RANK_MP3_192
    assert rank_for("mp3", 128) == RANK_MP3_192  # at/below 192 -> the lowest lossy rank


def test_rank_for_unknown_extension_is_none():
    """An unknown extension yields None so the gate rejects it as not-in-allowed (conservative)."""
    assert rank_for("xyz", None) is None


def test_rank_for_mp3_absent_bitrate_is_conservative_not_lossless():
    """mp3 with NO bitrate must not be treated as lossless; it cannot out-rank a real 320 (RESEARCH 350)."""
    r = rank_for("mp3", None)
    assert r is None or r < RANK_FLAC


# ---- gate(): QUAL-02 REJECT direction ---------------------------------------

def test_below_cutoff_mp3_declined_against_lossless_only(load_fixture):
    """QUAL-02 REJECT: a complete MP3-192 album is below cutoff for a lossless-only profile."""
    cand = _candidate(load_fixture, "below_cutoff_mp3")
    ok, reason = gate(cand, LOSSLESS_ONLY)
    assert ok is False
    # the reason names the offending file and the no-downgrade/not-allowed cause.
    assert ".mp3" in reason.lower()
    assert "reject" in reason.lower()


def test_below_cutoff_mp3_declined_against_mp3_320_cutoff(load_fixture):
    """QUAL-02 REJECT: MP3-192 (rank 1) is below the mp3_320_cutoff profile's cutoff rank 3."""
    cand = _candidate(load_fixture, "below_cutoff_mp3")
    ok, reason = gate(cand, MP3_320_CUTOFF)
    assert ok is False
    assert "reject" in reason.lower()


def test_not_in_allowed_set_declined(load_fixture):
    """A file whose rank is outside profile.allowed is declined (not-in-allowed reason)."""
    # known_good_mp3_320 is rank 3; lossless_only allows only {4,5} -> not in allowed.
    cand = _candidate(load_fixture, "known_good_mp3_320")
    ok, reason = gate(cand, LOSSLESS_ONLY)
    assert ok is False
    assert "allowed" in reason.lower() or "reject" in reason.lower()


# ---- gate(): QUAL-02 PERMIT direction ---------------------------------------

def test_known_good_mp3_320_permitted_against_mp3_320_cutoff(load_fixture):
    """QUAL-02 PERMIT (the load-bearing mp3_320_cutoff proof): a profile-acceptable lossy
    MP3-320 candidate PASSES when the cutoff allows it. Over-rejecting here would be T-03-12 DoS.
    Calibration discipline (Pitfall 3): if this fails, tune the rank ladder, never the assertion."""
    cand = _candidate(load_fixture, "known_good_mp3_320")
    ok, reason = gate(cand, MP3_320_CUTOFF)
    assert ok is True
    assert "quality OK" in reason


def test_known_good_flac_permitted_against_lossless_only(load_fixture):
    """Genuine FLAC passes the lossless-only gate (the canonical happy path)."""
    cand = _candidate(load_fixture, "known_good_flac")
    ok, reason = gate(cand, LOSSLESS_ONLY)
    assert ok is True
    assert "quality OK" in reason


def test_known_good_alac_permitted_against_lossless_only(load_fixture):
    """ALAC (.m4a) is lossless rank 4, inside lossless_only's allowed {4,5} and at/above cutoff."""
    cand = _candidate(load_fixture, "known_good_alac")
    ok, _reason = gate(cand, LOSSLESS_ONLY)
    assert ok is True


# ---- gate(): every-file evaluation (no partial pass) -------------------------

def test_gate_evaluates_every_audio_file_one_bad_fails_whole_candidate():
    """One below-cutoff file among otherwise-OK files fails the whole candidate (no downgrades)."""
    good = build_candidate({
        "folder": "Artist - Album [FLAC]",
        "files": [
            {"filename": "01 - a.flac", "size": 24000000, "bitRate": 900, "length": 284},
            {"filename": "02 - b.flac", "size": 24000000, "bitRate": 900, "length": 284},
            # one lossy file sneaks in among the FLACs:
            {"filename": "03 - c.mp3", "size": 6000000, "bitRate": 192, "length": 284},
        ],
    })
    ok, reason = gate(good, LOSSLESS_ONLY)
    assert ok is False
    assert "c.mp3" in reason
