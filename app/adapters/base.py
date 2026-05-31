# Curator adapters — the uniform *arr seam (ARR-01): one Protocol both Lidarr and Readarr
# satisfy, and one GapItem model the core ever sees. This module is the firewall's contract:
# *arr field names (foreignAlbumId/profileId/records[]/X-Api-Key) live ONLY in the concrete
# adapters (lidarr.py/readarr.py), never here and never in core/state.
#
# Phase 2 IMPLEMENTED only get_wanted(); Phase 3 IMPLEMENTS get_quality_profile + get_manifest,
# which return the NEUTRAL Profile / Manifest types (core.quality.Profile / core.manifest.Manifest).
# Importing those neutral shapes here is allowed and is the WHOLE POINT of the firewall: the adapter
# normalizes the *arr profile/manifest JSON into these neutral types so only they cross into core —
# no *arr field name ever does. The remaining queue/import methods stay declared-and-stubbed for
# Phase 4-5 (the seam shape is locked now without building that behavior).
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable

from core.manifest import Manifest  # neutral authoritative-target shape (NOT *arr/MB JSON)
from core.quality import Profile    # neutral already-normalized quality profile (NOT *arr JSON)

# missing  = monitored item with no acceptable release at all     (GAP-01, wanted/missing)
# cutoff   = monitored item present but below its quality cutoff   (GAP-02, wanted/cutoff)
GapType = Literal["missing", "cutoff"]


@dataclass(frozen=True)
class GapItem:
    """The uniform gap the core acts on — the ONLY shape that crosses the adapter firewall.

    Carries both the dedup identity (arr_app + arr_id, the STATE-02 key) and the canonical
    foreign_id (MBID release-group for Lidarr / foreign book id for Readarr) that Phase 3
    matching anchors against. quality_profile_id is stored, NOT acted on, in Phase 2 (the
    quality DECISION is Phase 3). `raw` preserves the original *arr record for later phases.
    """

    arr_app: Literal["lidarr", "readarr"]   # which adapter produced it (namespaces the id)
    arr_id: str                             # the *arr's own record id, stringified (stable per instance)
    kind: Literal["album", "book"]
    gap_type: GapType                        # missing | cutoff
    title: Optional[str]
    artist_or_author: Optional[str]
    foreign_id: Optional[str]                # MBID release-group (Lidarr) / foreign book id (Readarr)
    quality_profile_id: Optional[int]        # AlbumResource.profileId (NOTE: 'profileId', not 'qualityProfileId')
    raw: Dict[str, Any] = field(default_factory=dict)   # original record (provenance; later phases mine it)


@runtime_checkable
class ArrAdapter(Protocol):
    """The *arr-agnostic interface. Lidarr (primary) and Readarr (best-effort, breaker-wrapped)
    each satisfy it structurally — no inheritance coupling. The core depends on THIS, never on a
    concrete *arr client, so Readarr stays pluggable and can never gate the music path (ARR-01).
    """

    app: str

    def get_wanted(self) -> List[GapItem]:
        """Phase 2: monitored missing + cutoff merged into uniform GapItems (GAP-01/GAP-02)."""
        ...

    # --- IMPLEMENTED in Phase 3: normalize *arr profile + manifest into the NEUTRAL types ---------
    def get_quality_profile(self, profile_id: int) -> Profile:
        """Phase 3 — fetch the *arr quality profile and normalize it into a neutral Profile.

        The adapter reads the *arr profile JSON (allowed qualities + cutoff) and converts it to
        Profile(allowed: frozenset[int], cutoff_rank: int) over the neutral QualityRank ladder, so
        core/quality.py never sees *arr JSON shapes. QUAL-01: all *arr field names stay in the adapter.
        """
        ...

    def get_manifest(self, foreign_id: str) -> Manifest:
        """Phase 3 — fetch the authoritative release/book by foreign_id and normalize it to a Manifest.

        Lidarr maps the MB album+track data -> Manifest(artist, album, track_count, track_titles,
        kind='album', year); Readarr maps the book record -> Manifest(author->artist, title->album,
        track_count=1, track_titles=None, kind='book'). Only the neutral Manifest crosses into core.
        """
        ...

    # --- declared now, IMPLEMENTED in later phases (the seam shape is locked; do NOT build these) ---
    def get_queue_status(self, item: GapItem) -> Any:
        """Phase 5 — fallback-only race check against the *arr download queue. Stubbed in Phase 2."""
        ...

    def manual_import_candidates(self, path: str) -> list:
        """Phase 4 — *arr Manual Import API candidates for a staging path. Stubbed in Phase 2."""
        ...

    def execute_import(self, decisions: list) -> None:
        """Phase 4 — commit a Manual Import decision set. Stubbed in Phase 2."""
        ...

    def verify_imported(self, item: GapItem) -> bool:
        """Phase 4 — confirm the *arr imported the item into the library. Stubbed in Phase 2."""
        ...
