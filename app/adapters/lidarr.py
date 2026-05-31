# Curator LidarrAdapter — music, PRIMARY. Maps the verified Servarr v1 wanted/missing +
# wanted/cutoff envelope into uniform GapItems (GAP-01/GAP-02). This file is part of the
# firewall: the *arr field names (foreignAlbumId/profileId/records[]/X-Api-Key) live HERE
# and nowhere in core/state.
#
# Lidarr is the primary path, so a hard fault is allowed to surface (raise_for_status) — it is
# deliberately NOT breaker-wrapped (unlike Readarr). The injected httpx.Client makes it testable
# offline with httpx.MockTransport / respx (RESEARCH "Environment Availability").
import logging
from typing import Optional

import httpx

from adapters.base import GapItem
from core.manifest import Manifest
from core.quality import (
    RANK_ALAC,
    RANK_FLAC,
    RANK_MP3_192,
    RANK_MP3_256,
    RANK_MP3_320,
    Profile,
)

log = logging.getLogger(__name__)

# *arr quality NAME -> neutral QualityRank. This map is the firewall boundary: the *arr quality-name
# vocabulary lives ONLY here (lidarr.py), and the adapter emits ONLY the neutral int ranks into core.
# Lidarr quality names (per the qualityprofile API's nested quality {id,name}) are lower-cased and
# looked up here; an unrecognized name maps to None and is simply omitted from the allowed set
# (conservative — a quality core has no rank for cannot satisfy a cutoff). [A4: confirm names live]
_LIDARR_QUALITY_RANKS = {
    "mp3-192": RANK_MP3_192,
    "mp3-256": RANK_MP3_256,
    "mp3-320": RANK_MP3_320,
    "mp3-v0": RANK_MP3_320,    # V0 ~ transparent; treat at the 320 tier for gating
    "mp3 320": RANK_MP3_320,
    "mp3 256": RANK_MP3_256,
    "mp3 192": RANK_MP3_192,
    "alac": RANK_ALAC,
    "flac": RANK_FLAC,
    "flac 24bit": RANK_FLAC,
    "wav": RANK_FLAC,
    "ape": RANK_FLAC,
}


def _rank_for_quality_name(name: Optional[str]) -> Optional[int]:
    """Map a *arr quality NAME to a neutral QualityRank int, or None if unknown (defensive)."""
    if not isinstance(name, str):
        return None
    return _LIDARR_QUALITY_RANKS.get(name.strip().lower())


class LidarrAdapter:
    """Reads monitored missing + cutoff-unmet albums from Lidarr and maps them to GapItems.

    The album-level quality profile is `profileId` on AlbumResource (VERIFIED from
    AlbumResource.cs — it is the `profileId` key, NOT the camelCase quality-profile-id
    spelling Sonarr/Radarr use); foreign_id is the MusicBrainz release-group id `foreignAlbumId`.
    """

    app = "lidarr"

    def __init__(self, base_url: str, api_key: str, client: httpx.Client):
        # Fail fast: a None/empty key would otherwise produce {"X-Api-Key": None}, which httpx
        # rejects with an opaque header-encoding TypeError on the first request. Lidarr is the
        # PRIMARY (music) path, so a missing key is a hard, clearly-reported error (CR-01).
        if not api_key:
            raise ValueError("LIDARR_API_KEY is required (music is the primary path)")
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {"X-Api-Key": api_key}   # [VERIFIED: Servarr v1 auth header]

    # Defensive cap: at pageSize=100 this allows ~100k records before bailing — far above any
    # realistic monitored-gap count — yet still terminates if a server reports bad pagination.
    _MAX_PAGES = 1000

    def _paged(self, path: str) -> list:
        """Page through the verified {page,pageSize,totalRecords,records} envelope.

        Lidarr is primary: r.raise_for_status() lets a hard fault surface (NOT swallowed,
        NOT breaker-wrapped). Stops when a page returns no records, when
        page*pageSize >= totalRecords, or at a hard page cap — so a misbehaving server
        reporting pageSize:0 (or totalRecords > data) can never spin the loop forever (BL-01).
        """
        records, page = [], 1
        while page <= self._MAX_PAGES:
            r = self._client.get(
                f"{self._base}/api/v1/{path}",
                headers=self._headers,
                params={
                    "page": page,
                    "pageSize": 100,
                    "sortKey": "releaseDate",
                    "sortDirection": "ascending",
                    "monitored": "true",
                    "includeArtist": "true",
                },
                timeout=30.0,
            )
            r.raise_for_status()
            body = r.json()                       # { page, pageSize, totalRecords, records:[...] }
            batch = body.get("records", [])
            records += batch
            # Treat a non-positive/None pageSize as the request default (100) so a server-reported
            # pageSize:0 can't make the cutoff test always-false; an empty page always terminates.
            page_size = body.get("pageSize") or 100
            if not batch or page * page_size >= body.get("totalRecords", 0):
                break
            page += 1
        return records

    def get_wanted(self) -> list:
        """Monitored missing (gap_type='missing') + cutoff-unmet (gap_type='cutoff') merged.

        A single malformed record (missing `id`) is skipped+logged rather than aborting the
        whole primary run (WR-03); _map returns None for such records and they are filtered here.
        """
        out = []
        for gap_type in ("missing", "cutoff"):
            for rec in self._paged(f"wanted/{gap_type}"):
                mapped = self._map(rec, gap_type)
                if mapped is not None:
                    out.append(mapped)
        return out

    def _map(self, rec: dict, gap_type: str):
        # [VERIFIED AlbumResource fields: id, foreignAlbumId, artistId, title, monitored, profileId]
        # Defensive: a record without `id` has no stable dedup identity — skip it (mirrors Readarr,
        # WR-03) so one bad record cannot KeyError-abort the entire primary detection pass.
        if not isinstance(rec, dict) or rec.get("id") is None:
            log.warning("lidarr record not a dict or missing id; skipping: %r", rec)
            return None
        artist = rec.get("artist") or {}
        return GapItem(
            arr_app="lidarr",
            arr_id=str(rec["id"]),
            kind="album",
            gap_type=gap_type,
            title=rec.get("title"),
            artist_or_author=artist.get("artistName"),
            foreign_id=rec.get("foreignAlbumId"),
            quality_profile_id=rec.get("profileId"),   # album-level profile id (the `profileId` key)
            raw=rec,
        )

    def get_quality_profile(self, profile_id: int) -> Profile:
        """QUAL-01: GET /api/v1/qualityprofile/{id} and NORMALIZE it to a neutral Profile.

        Reads the ordered `items[]` (each `allowed:bool` over a nested `quality {id,name}`) and the
        profile's `cutoff` quality id — ALL with .get()-defensive access since the exact shape is
        A4-unconfirmed. Each ALLOWED quality name maps through _LIDARR_QUALITY_RANKS to a neutral
        rank; the allowed set is the frozenset of those ranks, and cutoff_rank is the rank of the
        quality whose id == `cutoff`. The *arr key names (`items`, `allowed`, `cutoff`, `quality`)
        never leave this method. Lidarr is primary -> raise_for_status surfaces a hard fault.

        Defensive: an item may wrap its quality either as a nested `{"quality": {...}}` group OR be a
        bare quality dict; both are handled. A cutoff id with no resolvable rank falls back to the
        minimum allowed rank (so the gate still has a floor) rather than crashing.
        """
        r = self._client.get(
            f"{self._base}/api/v1/qualityprofile/{profile_id}",
            headers=self._headers,
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json() if isinstance(r.json(), dict) else {}

        allowed_ranks = set()
        id_to_rank = {}
        for item in body.get("items") or []:
            if not isinstance(item, dict):
                continue
            q = item.get("quality") if isinstance(item.get("quality"), dict) else item
            q = q or {}
            rank = _rank_for_quality_name(q.get("name"))
            qid = q.get("id")
            if rank is not None and qid is not None:
                id_to_rank[qid] = rank
            if item.get("allowed") and rank is not None:
                allowed_ranks.add(rank)

        # `cutoff` is a quality id (or a nested {id} group); resolve it to a neutral rank.
        cutoff = body.get("cutoff")
        if isinstance(cutoff, dict):
            cutoff = cutoff.get("id")
        cutoff_rank = id_to_rank.get(cutoff)
        if cutoff_rank is None:
            # No resolvable cutoff -> floor at the lowest allowed rank (never crash, never over-permit).
            cutoff_rank = min(allowed_ranks) if allowed_ranks else RANK_FLAC

        return Profile(allowed=frozenset(allowed_ranks), cutoff_rank=cutoff_rank)

    def get_manifest(self, foreign_id: str) -> Manifest:
        """Build a neutral Manifest from the Lidarr album identified by its MB foreign_id.

        GET /api/v1/album?foreignAlbumId={id} -> the album record (artist, title, track count, and
        — when the release's media/tracks are present — the per-track title list). All *arr/MB key
        names (`foreignAlbumId`, `artist`, `releases`, `media`, `tracks`, `title`) stay in THIS
        method; only Manifest crosses. When the per-track list is not present in the album payload,
        track_titles is None (the graceful-omission path — the matcher leans on track_count), keeping
        Phase 3 offline-provable without a second MB round-trip.
        """
        r = self._client.get(
            f"{self._base}/api/v1/album",
            headers=self._headers,
            params={"foreignAlbumId": foreign_id},
            timeout=30.0,
        )
        r.raise_for_status()
        payload = r.json()
        rec = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(rec, dict):
            rec = {}

        artist = rec.get("artist") or {}
        # Track titles, when the album payload carries a release's track list; else None (graceful).
        titles = []
        for rel in rec.get("releases") or []:
            if not isinstance(rel, dict):
                continue
            for trk in rel.get("tracks") or []:
                if isinstance(trk, dict) and trk.get("title"):
                    titles.append(trk["title"])
            if titles:
                break

        track_count = rec.get("trackCount")
        if not isinstance(track_count, int):
            track_count = len(titles)

        return Manifest(
            artist=artist.get("artistName") or "",
            album=rec.get("title") or "",
            track_count=track_count,
            track_titles=tuple(titles) if titles else None,
            kind="album",
            year=rec.get("releaseYear") if isinstance(rec.get("releaseYear"), int) else None,
        )
