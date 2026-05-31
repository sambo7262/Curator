# Curator manifest model — the normalized authoritative target the matcher anchors against.
# Manifest is the core side of the firewall (PITFALL #6): it carries ZERO *arr / MusicBrainz field
# names. The adapter (get_manifest) builds it from the gap's foreign_id (MBID) and hands core ONLY
# this neutral shape; *arr/MB vocabulary stays behind the adapter.
#
# Anchoring rule (Pitfall 1): the matcher trusts THIS, never the candidate's self-description.
from dataclasses import dataclass
from typing import Literal, Optional, Tuple


@dataclass(frozen=True)
class Manifest:
    """The authoritative identity a Candidate is scored against (the match target).

    track_titles is None when the upstream metadata source gave no per-track list — that is the
    GRACEFUL-OMISSION path: the matcher omits the track-title sub-distance entirely (it does NOT
    penalize) and leans on track_count instead (RESEARCH §2). For BOOKS the SAME type carries the
    book identity (author -> artist, title -> album) per RESEARCH §9; track_count/track_titles
    simply don't apply (a book is one file) and the matcher omits them, exactly like the no-titles
    path. kind disambiguates which interpretation applies.
    """

    artist: str                                     # music: artist / books: author
    album: str                                      # music: album  / books: title
    track_count: int
    track_titles: Optional[Tuple[str, ...]] = None  # None => omit the title sub-distance (graceful)
    kind: Literal["album", "book"] = "album"
    year: Optional[int] = None                      # informational; NOT trusted from the candidate
