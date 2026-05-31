"""Phase-3 coarse fake/transcoded-FLAC proofs (QUAL-03 — the re-wrapped-lossy defense).

These prove the COARSE (non-spectral) fake-FLAC heuristics against the labeled corpus
(app/tests/fixtures/candidates/INDEX.md). Three checks, any one of which rejects a fake claiming
.flac: (1) bytes/sec floor, (2) claimed-bitrate in a lossy bucket, (3) a lossy source token in the
folder name. The HEADLINE guarantee (Pitfall 4 / T-03-09): every check SKIPS when its input is
absent, so a genuine FLAC missing the `length`/`bitRate` attribute is NEVER false-rejected — only
present-and-bad data rejects. Over-rejecting genuine FLAC is a DoS (Curator acquires nothing), so
calibration tunes the floor/heuristic, never the assertion (Pitfall 3).

fakeflac.py imports NO rapidfuzz (only stdlib + the neutral candidate type), so this module runs in
the Python 3.9 + offline dev sandbox exactly as it will at CI/NAS Python 3.12 — the authoritative
green gate remains CI/NAS, but there is no sandbox skip here.
"""
from core.candidate import build_candidate
from core.fakeflac import _has_lossy_source_token, check


def _candidate(load_fixture, name):
    return build_candidate(load_fixture(f"candidates/{name}"))


# ---- QUAL-03 reject direction -----------------------------------------------

def test_fake_flac_declined(load_fixture):
    """The fake_flac fixture (~128 kbps effective + lossy source token) is REJECTED, with a reason
    naming which heuristic fired. This is a fakeflac REJECT, not a match failure (the album is
    otherwise correct/complete), so fakeflac is proven load-bearing."""
    cand = _candidate(load_fixture, "fake_flac")
    ok, reason = check(cand)
    assert ok is False
    assert "fakeflac REJECT" in reason
    # the bytes/sec floor is the primary signal and fires first on this fixture.
    assert "kbps" in reason.lower() or "lossy" in reason.lower()


def test_bytes_per_sec_floor_fires_on_low_effective_bitrate():
    """A FLAC whose size*8/length/1000 is well below the floor is rejected as re-wrapped lossy."""
    cand = build_candidate({
        "folder": "Artist - Album [FLAC]",  # NO lossy token, so only the bytes/sec check can fire
        "files": [
            # ~128 kbps effective: 4.5MB over 284s -> ~127 kbps, < 400 floor.
            {"filename": "01 - a.flac", "size": 4544000, "bitRate": None, "length": 284},
        ],
    })
    ok, reason = check(cand)
    assert ok is False
    assert "fakeflac REJECT" in reason
    assert "kbps" in reason.lower()


def test_claimed_lossy_bitrate_bucket_rejected():
    """A FLAC claiming a classic lossy bitrate (e.g. 320) is suspicious -> rejected, even when its
    bytes/sec would pass (so this proves the claimed-bitrate check independently)."""
    cand = build_candidate({
        "folder": "Artist - Album [FLAC]",
        "files": [
            # large file (bytes/sec well above floor) but bitRate attr claims an exact lossy bucket.
            {"filename": "01 - a.flac", "size": 30000000, "bitRate": 320, "length": 284},
        ],
    })
    ok, reason = check(cand)
    assert ok is False
    assert "fakeflac REJECT" in reason
    assert "320" in reason


def test_lossy_source_token_in_folder_rejected():
    """A folder carrying a lossy source token while claiming .flac is rejected (token heuristic),
    even with a genuine-looking high bytes/sec and no claimed lossy bitrate."""
    cand = build_candidate({
        "folder": "Artist - Album [FLAC] (web-dl from spotify)",
        "files": [
            {"filename": "01 - a.flac", "size": 30000000, "bitRate": None, "length": 284},
        ],
    })
    ok, reason = check(cand)
    assert ok is False
    assert "fakeflac REJECT" in reason
    assert "source token" in reason.lower()


# ---- QUAL-03 permit direction -----------------------------------------------

def test_known_good_flac_passes(load_fixture):
    """A genuine FLAC (~800-900 kbps effective, no lossy token, no lossy claimed bitrate) passes."""
    cand = _candidate(load_fixture, "known_good_flac")
    ok, reason = check(cand)
    assert ok is True
    assert reason == "fakeflac OK"


def test_non_flac_candidate_passes(load_fixture):
    """The FLAC checks only run on flac files; a clean MP3-320 candidate is not a fakeflac concern."""
    cand = _candidate(load_fixture, "known_good_mp3_320")
    ok, _reason = check(cand)
    assert ok is True


# ---- HEADLINE: Pitfall 4 — data-absent -> SKIP, never false-reject ----------

def test_flac_with_missing_length_not_false_rejected():
    """HEADLINE (Pitfall 4 / T-03-09): a FLAC with length_seconds=None must NOT be rejected by the
    bytes/sec check — the sub-check is skipped on absent data, never a rejection on a None input.
    Here size is tiny (would fail the floor IF length were known) but length is absent, and there is
    no lossy claimed bitrate or token, so the candidate must PASS."""
    cand = build_candidate({
        "folder": "Artist - Album [FLAC]",
        "files": [
            # tiny file, but length absent -> bytes/sec check skipped; no other signal -> pass.
            {"filename": "01 - a.flac", "size": 1000000, "bitRate": None, "length": None},
        ],
    })
    ok, reason = check(cand)
    assert ok is True, f"genuine missing-length FLAC must not be false-rejected, got: {reason}"


def test_flac_with_missing_bitrate_not_false_rejected():
    """A FLAC with bitrate_kbps=None must not trip the claimed-bitrate check (skip on absent data)."""
    cand = build_candidate({
        "folder": "Artist - Album [FLAC]",
        "files": [
            {"filename": "01 - a.flac", "size": 26000000, "bitRate": None, "length": 284},
        ],
    })
    ok, _reason = check(cand)
    assert ok is True


# ---- _has_lossy_source_token unit ------------------------------------------

def test_has_lossy_source_token_detects_markers():
    """The token check fires on common lossy markers and stays quiet on a clean lossless folder."""
    assert _has_lossy_source_token("Artist - Album [FLAC] (mp3 source)") is True
    assert _has_lossy_source_token("Artist - Album (320)") is True
    assert _has_lossy_source_token("Artist - Album V0") is True
    assert _has_lossy_source_token("Artist - Album (from youtube)") is True
    assert _has_lossy_source_token("Artist - Album [FLAC] (CD)") is False
    assert _has_lossy_source_token("") is False


def test_has_lossy_source_token_no_false_positive_on_substring():
    """A clean album name that merely contains the letters of a marker is not flagged (bounded)."""
    # 'webcam' contains 'web' but 'web-dl'/'web dl' is the marker, not bare 'web'.
    assert _has_lossy_source_token("The Webcams - Live") is False
