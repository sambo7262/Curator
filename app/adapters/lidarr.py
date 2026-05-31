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

    # === Phase 4: the explicit *arr-agnostic ManualImport path (IMPORT-02/03, D-03/D-09) ==========
    # This is the entire reason Curator exists — it replaces Soularr's blind drop-folder rescan with
    # an explicit per-file ManualImport(Move). ALL *arr wire vocabulary
    # (folder/downloadId/albumReleaseId/importMode/files[] keys, AND the rejections/tracks reads that
    # drive the importability filter) lives ONLY in this file (the firewall): core/acquire.py gets the
    # already-filtered importable subset back as opaque dicts and never reads an *arr key itself.

    def manual_import_candidates(self, path: str, download_id: Optional[str] = None) -> list:
        """GET the *arr's proposed file->track mapping for a staging folder, and return ONLY the
        importable subset (D-09). The ADAPTER makes the importability decision here by reading the
        *arr `rejections`/`tracks` keys: a resource is importable iff its `rejections` list is empty
        AND its `tracks` list is non-empty. Everything else (folder.jpg, unmatched files, rejected
        quality) is dropped so it can never reach execute_import. The returned dicts are opaque to
        core — it passes them straight back to execute_import without reading a single *arr key.

        base.py declares the param name `path`; it maps to the *arr `folder` query param here.
        Lidarr is primary -> raise_for_status surfaces a hard fault (NOT swallowed).
        """
        r = self._client.get(
            f"{self._base}/api/v1/manualimport",
            headers=self._headers,
            params={
                "folder": path,                       # base.py `path` -> *arr `folder`
                "downloadId": download_id,
                "filterExistingFiles": "true",
                "replaceExistingFiles": "true",
            },
            timeout=60.0,
        )
        r.raise_for_status()
        resources = r.json()
        if not isinstance(resources, list):
            return []
        # The importability filter (the *arr rejections/tracks reads stay HERE — core stays key-blind):
        # keep only resources Lidarr matched to a wanted track with no rejection.
        return [
            res for res in resources
            if isinstance(res, dict) and not res.get("rejections") and res.get("tracks")
        ]

    def execute_import(self, decisions: list) -> None:
        """POST an explicit ManualImport command in importMode=Move for exactly the chosen files
        (D-09 — never a DownloadedAlbumsScan blind rescan, T-04-09). Each decision is a candidate
        dict returned by manual_import_candidates (opaque to core); this method reads its *arr keys
        and builds the per-file files[] envelope. importMode "Move" is the atomic-hardlink contract
        within the shared /data tree (the #1 import-failure cause is a cross-FS copy).

        [ASSUMED A1] the importMode casing ("Move") and the files[] element key set are pinned live
        in 04-05 via a DevTools capture; when confirmed, update the expected_post.json fixture + here.

        Lidarr is primary -> raise_for_status surfaces a hard fault.
        """
        body = {
            "name": "ManualImport",
            "importMode": "Move",   # [ASSUMED A1: casing — verify live 04-05] atomic hardlink (D-09)
            "files": [
                {
                    "path": d.get("path"),
                    "artistId": (d.get("artist") or {}).get("id"),
                    "albumId": (d.get("album") or {}).get("id"),
                    "albumReleaseId": d.get("albumReleaseId"),
                    "trackIds": [t.get("id") for t in (d.get("tracks") or []) if isinstance(t, dict)],
                    "quality": d.get("quality"),
                    "indexerFlags": d.get("indexerFlags", 0),
                    "disableReleaseSwitching": False,
                    "downloadId": d.get("downloadId"),
                }
                for d in decisions
            ],
        }
        r = self._client.post(
            f"{self._base}/api/v1/command",
            headers=self._headers,
            json=body,
            timeout=60.0,
        )
        r.raise_for_status()

    def verify_imported(self, item: "GapItem") -> bool:
        """D-03: confirm a REAL import by re-querying the *arr — return True iff the item's id has
        LEFT the wanted/missing+cutoff list. 'Downloaded' never counts as 'imported': if the album
        is still wanted, the import did not land and this returns False (forcing quarantine upstream).

        Re-uses the same wanted read get_wanted() drives, then checks the item's arr_id is absent.
        Lidarr is primary -> raise_for_status surfaces a hard fault.
        """
        still_wanted = {g.arr_id for g in self.get_wanted()}
        return item.arr_id not in still_wanted
