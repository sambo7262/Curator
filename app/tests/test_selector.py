"""Phase-3 selector proofs — the DUMB, swappable best-pick over ALREADY-accepted candidates, and the
ONLY reader of the uploader fields (Pitfall 5 / THREAT T-03-06: matching != selection).

selector.select() never re-judges a match; it assumes every input has already passed quality +
fakeflac + matching acceptance and just picks the best COPY by a deterministic tie-break ladder:
  1. match distance ascending     (the match verdict is primary and never overridden)
  2. format preference             (lossless before mp3-320 among equally-good matches)
  3. free_upload_slots descending  (prefer an uploader with slots free)
  4. upload_speed descending       (then the faster uploader)

selector.py imports only stdlib + the neutral Candidate type (no rapidfuzz), so it runs in the
Python 3.9 offline sandbox exactly as at CI/NAS — no module-level skip here.
"""
from core.candidate import build_candidate
from core.selector import select


def _cand(folder, ext="flac", slots=None, speed=None, n=1):
    """Build a Candidate with n audio files of a given extension + uploader attributes."""
    files = [{"filename": f"{i:02d} - t.{ext}", "size": 20000000} for i in range(1, n + 1)]
    return build_candidate(
        {"folder": folder, "files": files, "freeUploadSlots": slots, "uploadSpeed": speed}
    )


def test_empty_returns_none():
    """An empty accepted set selects nothing (the decline path's chosen=None)."""
    assert select([]) is None


def test_single_accepted_is_chosen():
    """A lone accepted candidate is returned as-is (no tie-break needed)."""
    c = _cand("A - Album [FLAC]")
    assert select([(0.05, c, ["r"])]) is c


def test_distance_is_primary_key():
    """The lowest-distance candidate wins outright, regardless of format/slots/speed.

    The worse-distance candidate is deliberately given the PREFERRED format + more slots + more speed
    to prove distance still dominates (uploader speed can never out-rank a better match)."""
    best_match = _cand("close - Album [MP3]", ext="mp3", slots=0, speed=1)
    fast_but_worse = _cand("far - Other [FLAC]", ext="flac", slots=9, speed=9_000_000)
    chosen = select([(0.20, fast_but_worse, ["r"]), (0.05, best_match, ["r"])])
    assert chosen is best_match


def test_format_preference_breaks_distance_tie():
    """At EQUAL distance, the lossless copy beats the mp3-320 copy (format preference)."""
    flac = _cand("A - Album [FLAC]", ext="flac")
    mp3 = _cand("A - Album [MP3 320]", ext="mp3")
    chosen = select([(0.05, mp3, ["r"]), (0.05, flac, ["r"])])
    assert chosen is flac


def test_free_slots_breaks_format_tie():
    """At equal distance AND equal format, more free upload slots wins (likelier to succeed)."""
    few = _cand("A - Album [FLAC] (seed1)", ext="flac", slots=0, speed=500)
    many = _cand("A - Album [FLAC] (seed2)", ext="flac", slots=5, speed=500)
    chosen = select([(0.05, few, ["r"]), (0.05, many, ["r"])])
    assert chosen is many


def test_speed_breaks_slots_tie():
    """At equal distance, format AND slots, the faster uploader wins (the last tie-break)."""
    slow = _cand("A - Album [FLAC] (slow)", ext="flac", slots=2, speed=100_000)
    fast = _cand("A - Album [FLAC] (fast)", ext="flac", slots=2, speed=900_000)
    chosen = select([(0.05, slow, ["r"]), (0.05, fast, ["r"])])
    assert chosen is fast


def test_none_uploader_attrs_sort_last_never_crash():
    """A candidate with None slots/speed sorts BELOW one that advertises them, and never crashes."""
    unknown = _cand("A - Album [FLAC] (unknown)", ext="flac", slots=None, speed=None)
    known = _cand("A - Album [FLAC] (known)", ext="flac", slots=1, speed=1)
    chosen = select([(0.05, unknown, ["r"]), (0.05, known, ["r"])])
    assert chosen is known
