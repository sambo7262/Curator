# Curator quality gate — the pre-download format/bitrate firewall (QUAL-01 / QUAL-02).
# This is the CORE side of the *arr firewall (PITFALL #6): it carries ZERO *arr field names and
# ZERO wire vocabulary. The adapter (plan 03-05's get_quality_profile) fetches the *arr quality
# profile JSON and NORMALIZES it — inside the adapter — into the neutral `Profile` below, so this
# module never sees `qualityProfileId`/`items[]`/`allowed`-as-JSON-key. The only inbound vocabulary
# here is the neutral Candidate contract (core.candidate) + plain Python ints (the QualityRank).
#
# The gate is PURE (no I/O, no clock, no network) and runs BEFORE any download by construction
# (Phase 3 is pre-download). Its job is the no-downgrade rule: reject the candidate if ANY wanted
# audio file ranks below the profile's cutoff or outside the allowed set (QUAL-02 — RESEARCH §5).
# Books ride the SAME Profile type with a book-format rank ladder (EPUB/MOBI/PDF/AZW3, RESEARCH
# §9); only the music ranks are implemented here.
from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

# ---------------------------------------------------------------------------
# Neutral QualityRank ladder — integer ranks ordered worst -> best. These ints are the ONLY
# quality vocabulary that crosses the firewall; the adapter maps *arr quality names onto exactly
# these numbers (the profile fixtures in tests/fixtures/candidates/profiles encode the same ladder).
# Mirrors tests/fixtures/candidates/INDEX.md:
#   1 = MP3-192   2 = MP3-256   3 = MP3-320/V0   4 = ALAC (lossless)   5 = FLAC (lossless)
# ---------------------------------------------------------------------------
RANK_MP3_192 = 1
RANK_MP3_256 = 2
RANK_MP3_320 = 3
RANK_ALAC = 4
RANK_FLAC = 5

# The lossless tier. ALAC/WAV/APE/FLAC are all lossless; we map every lossless extension to the
# single highest rank so a lossless-only profile (cutoff at the lossless rank) admits them all.
# (The profile fixtures distinguish ALAC=4/FLAC=5, but for GATING any lossless file is at/above a
# lossless cutoff, so collapsing lossless to one rank keeps the no-downgrade gate correct and
# avoids false-rejecting ALAC against a FLAC-cutoff profile that allows {4,5}.)
RANK_LOSSLESS = RANK_FLAC

# Extensions that are lossless regardless of any (often absent/garbage) bitrate attribute.
_LOSSLESS_EXTENSIONS = frozenset({"flac", "alac", "wav", "ape", "m4a"})
# Lossy extensions that rank by bitrate bucket.
_LOSSY_EXTENSIONS = frozenset({"mp3", "aac", "ogg"})


@dataclass(frozen=True)
class Profile:
    """A NEUTRAL, already-normalized quality profile (SP-1) — the firewall-clean Profile type.

    `allowed` is the set of neutral QualityRank ints a file may carry; `cutoff_rank` is the lowest
    rank that still counts as "cutoff met" (no downgrades below it). The adapter produces this from
    the *arr profile's ordered allowed-list + cutoff (plan 03-05); core only ever sees these ints.
    A book Profile rides this same type with a book-format ladder (RESEARCH §9).
    """

    allowed: FrozenSet[int]
    cutoff_rank: int


def rank_for(extension: str, bitrate_kbps: Optional[int]) -> Optional[int]:
    """Map a candidate file's (extension, bitrate) to a neutral QualityRank int, or None if unknown.

    - Lossless extensions (flac/alac/wav/ape/m4a) -> RANK_LOSSLESS regardless of bitrate. A claimed
      low bitrate on a FLAC is NOT demoted here — detecting re-wrapped lossy is fakeflac.check's job
      (separation of concerns); the format gate trusts the extension for losslessness.
    - Lossy extensions (mp3/aac/ogg) -> a bitrate bucket: >=320 -> mp3-320, >=256 -> mp3-256, else
      mp3-192 (the lowest lossy rank). Bitrate ABSENT -> None (conservative; an unknown-bitrate lossy
      file cannot be assumed to meet a 320 cutoff — RESEARCH 350).
    - Anything else (unknown extension) -> None, so the gate rejects it as not-in-allowed.

    Pure; never raises (defensive on a non-str extension).
    """
    if not isinstance(extension, str):
        return None
    ext = extension.lower().lstrip(".")
    if ext in _LOSSLESS_EXTENSIONS:
        return RANK_LOSSLESS
    if ext in _LOSSY_EXTENSIONS:
        if bitrate_kbps is None:
            return None  # unknown lossy bitrate -> conservative (cannot claim it meets a 320 cutoff)
        if bitrate_kbps >= 320:
            return RANK_MP3_320
        if bitrate_kbps >= 256:
            return RANK_MP3_256
        return RANK_MP3_192  # 192 and anything lower collapse to the lowest lossy rank
    return None


def gate(candidate, profile: Profile) -> Tuple[bool, str]:
    """The QUAL-02 no-downgrade format/bitrate gate (verbatim per RESEARCH 442-451).

    Evaluate EVERY wanted audio file (audio_files()): map each to a rank; reject the WHOLE candidate
    if any file's rank is unknown / not in profile.allowed, or below profile.cutoff_rank. Returns
    (True, "quality OK: ...") only if every audio file is at/above cutoff AND allowed. Reason strings
    follow RESEARCH 334. Pure; the pre-download structural defense against Pitfall 3 (downgrades).
    """
    allowed_str = sorted(profile.allowed)
    for f in candidate.audio_files():
        rank = rank_for(f.extension, f.bitrate_kbps)
        if rank is None or rank not in profile.allowed:
            # Include the computed rank + the profile's allowed ranks + cutoff so a live decline is
            # diagnosable: rank=None => the file's format/bitrate was not recognized; a real rank not
            # in `allowed` => the *arr profile genuinely does not permit that quality (config), e.g. a
            # FLAC (rank 5) rejected by an MP3-only profile allowed={3}.
            return False, (
                f"quality REJECT: {f.filename} (rank {rank}) not in profile "
                f"allowed={allowed_str} cutoff={profile.cutoff_rank}"
            )
        if rank < profile.cutoff_rank:
            return (
                False,
                f"quality REJECT: {f.filename} rank {rank} below cutoff "
                f"{profile.cutoff_rank} (no downgrade)",
            )
    return True, "quality OK: all audio files >= cutoff"
