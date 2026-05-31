# Curator coarse fake/transcoded-FLAC heuristics (QUAL-03) — the re-wrapped-lossy defense.
# CORE side of the *arr firewall (PITFALL #6): ZERO *arr field names, ZERO wire vocabulary. Inputs
# are only the neutral Candidate contract (core.candidate) + an int floor (settings.fakeflac_min_kbps
# is passed in by plan 03-05's gate; the default here matches Settings.fakeflac_min_kbps == 400).
#
# These are COARSE, not spectral (spectral/frequency-cutoff detection is QUAL-04, explicitly v2 —
# RESEARCH §6). The check runs ONLY on files whose extension is "flac" and applies three cheap
# heuristics, ANY of which rejects a fake claiming FLAC:
#   1. bytes/sec floor    — effective_kbps = size*8/length/1000 below the floor => re-wrapped lossy.
#   2. claimed-bitrate    — a `bitRate` attr sitting in a classic lossy bucket (128/192/256/320).
#   3. lossy source token — an mp3/320/v0/cbr/spotify/youtube/web-dl marker in the folder name.
#
# CRITICAL (Pitfall 4 / T-03-09): every sub-check SKIPS when its input is absent — a genuine FLAC
# missing the `length`/`bitRate` attribute must NEVER be false-rejected (that is a DoS: Curator
# acquires nothing). Only present-and-bad data rejects; no rejection reason ever references a None.
import re
from typing import Tuple

# Lossy source markers, matched as bounded WORD tokens (anchored on \b or explicit separators) so a
# clean album name that merely contains the letters of a marker (e.g. 'webcam' contains 'web') is
# NOT flagged. These are the §6.3 markers: classic lossy formats/bitrates + transcode-source tags.
_LOSSY_SOURCE_TOKENS = (
    r"\bmp3\b",
    r"\baac\b",
    r"\bogg\b",
    r"\b320\b",
    r"\b256\b",
    r"\b192\b",
    r"\b128\b",
    r"\bv0\b",
    r"\bv2\b",
    r"\bcbr\b",
    r"\bvbr\b",
    r"\bspotify\b",
    r"\byoutube\b",
    r"\bweb[\s._-]?dl\b",   # web-dl / web dl / webdl — but NOT bare 'web' (avoids 'Webcams' FP)
)
# Pre-compiled, case-insensitive, alternation of bounded tokens. All branches are simple literals
# with no nested quantifiers -> linear-time, ReDoS-safe on hostile folder names (THREAT T-03-01).
_LOSSY_SOURCE_RE = re.compile("|".join(_LOSSY_SOURCE_TOKENS), re.IGNORECASE)

# Classic lossy CBR bitrate buckets — a FLAC claiming exactly one of these is suspect (§6.2).
_LOSSY_BITRATE_BUCKETS = frozenset({128, 192, 256, 320})


def _has_lossy_source_token(folder: str) -> bool:
    """True if the folder name carries a lossy source/format marker (bounded, ReDoS-safe).

    Pure; defensive on a non-str / empty folder (-> False, never raises). Uses word-bounded matches
    so marker letters embedded in a legitimate album word do not false-positive (§4 cheap string
    check on already-parsed folder text).
    """
    if not isinstance(folder, str) or not folder:
        return False
    return _LOSSY_SOURCE_RE.search(folder) is not None


def check(candidate, min_kbps: int = 400) -> Tuple[bool, str]:
    """Coarse fake-FLAC gate (verbatim per RESEARCH 456-469). Returns (pass, reason).

    For each audio file with extension == "flac":
      (1) bytes/sec floor — ONLY `if f.length_seconds:` compute effective_kbps and reject if below
          min_kbps; absent length -> SKIP (Pitfall 4, never reject on a None input).
      (2) claimed-bitrate — ONLY `if f.bitrate_kbps` and it sits in a classic lossy bucket -> reject.
      (3) source token  — a lossy marker in candidate.folder -> reject (folder-level, once).
    Non-flac files are ignored (the FLAC checks only apply to FLAC). Default min_kbps == 400 matches
    Settings.fakeflac_min_kbps; plan 03-05's gate passes settings.fakeflac_min_kbps in. Reason
    strings follow RESEARCH 335. Pure; runs offline before any download.
    """
    for f in candidate.audio_files():
        if f.extension != "flac":
            continue
        # (1) bytes/sec floor — primary signal; SKIP entirely when length is absent (Pitfall 4).
        if f.length_seconds:
            eff = (f.size_bytes * 8) / f.length_seconds / 1000
            if eff < min_kbps:
                return (
                    False,
                    f"fakeflac REJECT: {f.filename} effective {eff:.0f} kbps < {min_kbps} floor",
                )
        # (2) claimed-bitrate sanity — SKIP when bitrate is absent/zero (Pitfall 4).
        if f.bitrate_kbps and f.bitrate_kbps in _LOSSY_BITRATE_BUCKETS:
            return (
                False,
                f"fakeflac REJECT: {f.filename} claims lossy bitrate {f.bitrate_kbps} as FLAC",
            )
    # (3) folder-level lossy source token — only meaningful if the candidate has FLAC files at all.
    if any(f.extension == "flac" for f in candidate.audio_files()) and _has_lossy_source_token(
        candidate.folder
    ):
        return False, "fakeflac REJECT: lossy source token in FLAC candidate folder name"
    return True, "fakeflac OK"
