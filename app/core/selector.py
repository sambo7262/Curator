# Curator selector — the DUMB, swappable best-pick over ALREADY-accepted candidates.
# This is the deliberate other half of "matching != selection" (Pitfall 5 / THREAT T-03-06):
# matching.score() answers "is this the right album?" with ZERO uploader awareness; selection
# answers "of the candidates we already accepted, which copy do we grab?" and is the ONLY place
# in the whole codebase that may read Candidate.username / upload_speed / free_upload_slots.
#
# It is intentionally trivial and swappable (SP-2): a pure sort with a deterministic tie-break
# ladder, no I/O, no clock, no network. Keeping it small and isolated is what lets the firewall
# grep in test_gate.py prove that uploader speed can NEVER bleed into the match decision — the
# gate calls selector ONLY after recommend() has already accepted, so a faster-but-worse-matching
# folder can never out-rank a better match (the prior Soularr failure mode).
from typing import List, Optional, Tuple

from core.candidate import Candidate

# One scored, ALREADY-ACCEPTED candidate: (distance, candidate, reasons). Same shape matching emits.
Accepted = Tuple[float, Candidate, List[str]]

# Format preference order (best -> worst) for the tie-break. Lossless first, then mp3-320. This is a
# preference among EQUALLY-MATCHED accepts only — it never overrides match distance, which sorts first.
_FORMAT_PREFERENCE = ("flac", "alac", "wav", "ape", "m4a", "mp3", "aac", "ogg")


def _format_rank(candidate: Candidate) -> int:
    """Index of the candidate's best audio extension in the preference list (lower = preferred).

    Returns len(_FORMAT_PREFERENCE) (worst) when the candidate has no recognizable audio format,
    so a format-less accept never tie-beats a real one. Pure; never raises.
    """
    best = len(_FORMAT_PREFERENCE)
    for f in candidate.audio_files():
        if f.extension in _FORMAT_PREFERENCE:
            best = min(best, _FORMAT_PREFERENCE.index(f.extension))
    return best


def select(accepted: List[Accepted]) -> Optional[Candidate]:
    """Pick the single best already-accepted candidate, or None if the accepted set is empty.

    Tie-break ladder (each key only breaks ties left by the previous):
      1. distance ascending          — the best match wins outright (matching's verdict is primary).
      2. format preference            — lossless before mp3-320 among equally-good matches.
      3. free_upload_slots descending — prefer an uploader with slots free (likelier to succeed).
      4. upload_speed descending      — then the faster uploader.
    A None free_upload_slots / upload_speed sorts last in its tier (treated as 0) so a candidate
    advertising the attribute beats one that doesn't — never a crash on missing data.

    This is the ONLY function that reads candidate.username / upload_speed / free_upload_slots
    (Pitfall 5). It assumes every input candidate has ALREADY passed quality + fakeflac + matching
    acceptance; it does NOT re-judge the match.
    """
    if not accepted:
        return None
    ordered = sorted(
        accepted,
        key=lambda t: (
            t[0],                                   # 1. match distance (primary — never overridden)
            _format_rank(t[1]),                     # 2. format preference
            -(t[1].free_upload_slots or 0),         # 3. more free slots first
            -(t[1].upload_speed or 0),              # 4. faster uploader first
        ),
    )
    return ordered[0][1]
