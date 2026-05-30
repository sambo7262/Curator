# Curator LidarrAdapter — music, PRIMARY. Maps the verified Servarr v1 wanted/missing +
# wanted/cutoff envelope into uniform GapItems (GAP-01/GAP-02). This file is part of the
# firewall: the *arr field names (foreignAlbumId/profileId/records[]/X-Api-Key) live HERE
# and nowhere in core/state.
#
# Lidarr is the primary path, so a hard fault is allowed to surface (raise_for_status) — it is
# deliberately NOT breaker-wrapped (unlike Readarr). The injected httpx.Client makes it testable
# offline with httpx.MockTransport / respx (RESEARCH "Environment Availability").
import httpx

from adapters.base import GapItem


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
        """Monitored missing (gap_type='missing') + cutoff-unmet (gap_type='cutoff') merged."""
        missing = [self._map(rec, "missing") for rec in self._paged("wanted/missing")]
        cutoff = [self._map(rec, "cutoff") for rec in self._paged("wanted/cutoff")]
        return missing + cutoff

    def _map(self, rec: dict, gap_type: str) -> GapItem:
        # [VERIFIED AlbumResource fields: id, foreignAlbumId, artistId, title, monitored, profileId]
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
