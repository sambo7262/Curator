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


def _resolve_profile_id(rec: dict):
    """Resolve the quality profile id for a wanted album, preferring the ARTIST's profile.

    Lidarr quality profiles are ARTIST-level. The album record's own `profileId` is a legacy field
    that is 0 for many albums — and a profileId of 0 makes get_quality_profile do GET
    /qualityprofile/0 -> 404, which the acquire loop reads as 'manifest/profile unavailable' and marks
    the album 'stuck (no search)' FOREVER (it is never even searched). Since the wanted query embeds
    the artist (includeArtist=true), prefer the artist's qualityProfileId when it is a valid (>0) id,
    and fall back to the album-level `profileId` only when the artist's is absent/invalid — so the
    behavior is never worse than before, and the 0-profileId albums become searchable. The `profileId`
    / `qualityProfileId` wire keys stay here in the adapter (the firewall)."""
    artist = rec.get("artist") or {}
    artist_pid = artist.get("qualityProfileId")
    if isinstance(artist_pid, int) and artist_pid > 0:
        return artist_pid
    return rec.get("profileId")


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


def _collect_quality_ranks(items, allowed_ranks, id_to_rank, parent_allowed=False):
    """Recursively walk a Lidarr quality-profile `items[]`, collecting allowed neutral ranks.

    Lidarr NESTS qualities inside GROUPS: a profile item is either a single quality
    {"quality": {id,name}, "allowed": bool} OR a group {"id","name","allowed","items":[...]} whose
    member qualities live in the nested `items`. The previous flat loop only looked at the top level,
    so for any profile that groups its lossless qualities (the default 'Lossless' group holding FLAC/
    ALAC) it saw the group wrapper (no resolvable rank) and NEVER reached the nested FLAC -> allowed
    came back EMPTY -> the gate rejected EVERY candidate (FLAC included) as 'not in allowed=[]'.

    A leaf quality is allowed when its own `allowed` is set OR an enclosing group is allowed (Lidarr
    enables a whole group via the group checkbox). Flat profiles (no nested `items`) behave exactly as
    before, so existing fixtures/tests are unaffected."""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        own_allowed = bool(item.get("allowed")) or parent_allowed
        nested = item.get("items")
        if isinstance(nested, list) and nested:
            _collect_quality_ranks(nested, allowed_ranks, id_to_rank, own_allowed)
            continue
        q = item.get("quality") if isinstance(item.get("quality"), dict) else item
        q = q or {}
        rank = _rank_for_quality_name(q.get("name"))
        qid = q.get("id")
        if rank is not None and qid is not None:
            id_to_rank[qid] = rank
        if own_allowed and rank is not None:
            allowed_ranks.add(rank)


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
            quality_profile_id=_resolve_profile_id(rec),
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
        # Recurse so Lidarr quality GROUPS (e.g. the 'Lossless' group wrapping FLAC/ALAC) are walked —
        # a flat loop missed the nested qualities and returned an EMPTY allowed set (every candidate,
        # FLAC included, then failed the gate as 'not in allowed=[]').
        _collect_quality_ranks(body.get("items"), allowed_ranks, id_to_rank)

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

        # Track count: the album-distance completeness driver (matching._track_count_distance maxes
        # out at 1.0 when this is <= 0 — a 0 here floors EVERY candidate at ~0.40 and nothing ever
        # accepts). Lidarr's album record carries NO usable top-level `trackCount`; the real count is
        # in `statistics.totalTrackCount` (album-total, download-state-agnostic) and on each release's
        # `trackCount`. Resolve the first positive int across those, preferring the monitored release;
        # fall back to the per-track title count only as a last resort. All *arr keys stay in-adapter.
        track_count = None
        stats = rec.get("statistics") or {}
        if isinstance(stats, dict):
            for v in (stats.get("totalTrackCount"), stats.get("trackCount")):
                if isinstance(v, int) and v > 0:
                    track_count = v
                    break
        if track_count is None:
            for rel in rec.get("releases") or []:
                if not isinstance(rel, dict):
                    continue
                rc = rel.get("trackCount")
                if isinstance(rc, int) and rc > 0:
                    track_count = rc
                    if rel.get("monitored"):
                        break  # the monitored release is authoritative; otherwise keep the first seen
        if not isinstance(track_count, int) or track_count <= 0:
            top = rec.get("trackCount")
            track_count = top if isinstance(top, int) and top > 0 else len(titles)

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
        importable = [
            res for res in resources
            if isinstance(res, dict) and not res.get("rejections") and res.get("tracks")
        ]
        # Observability: when Lidarr returns files but NONE are importable, the loop quarantines
        # silently — surface WHY (rejected quality / no track match), mirroring the gate-decline log.
        if resources and len(importable) < len(resources):
            samples = []
            for res in resources:
                if res in importable or not isinstance(res, dict):
                    continue
                rej = res.get("rejections") or []
                msgs = [x.get("reason") for x in rej if isinstance(x, dict) and x.get("reason")]
                samples.append("; ".join(msgs) if msgs else ("no track match" if not res.get("tracks") else "rejected"))
                if len(samples) >= 5:
                    break
            log.info(
                "manualimport: %d of %d files importable from '%s' (dropped: %s)",
                len(importable), len(resources), path, " | ".join(samples),
            )
        return importable

    def execute_import(self, decisions: list) -> None:
        """POST an explicit ManualImport command in importMode="move" for exactly the chosen files
        (D-09 — never a DownloadedAlbumsScan blind rescan, T-04-09). Each decision is a candidate
        dict returned by manual_import_candidates (opaque to core); this method reads its *arr keys
        and builds the per-file files[] envelope. importMode "move" is the atomic-hardlink contract
        within the shared /data tree (the #1 import-failure cause is a cross-FS copy).

        [A1 — PINNED LIVE 2026-05-31, see 04-05-LIVE-PROBE.md] DevTools-captured envelope on the real
        NAS Lidarr. The casing + shape are now confirmed against reality:
          - top-level `importMode` is LOWERCASE ("move"/"copy"). Curator DELIBERATELY sends "move"
            (NOT the UI default "copy", and NOT the old [ASSUMED] capital "Move") so the same-fs
            ManualImport is an atomic hardlink-rename (D-09), after which acquire purges staging.
          - top-level `replaceExistingFiles` is present and false; `sendUpdatesToClient` true is the
            UI default (optional — included to mirror the captured UI POST).
          - per-file: path, artistId, albumId, albumReleaseId, trackIds[], the FULL `quality`
            QualityModel object echoed from the manualimport candidate, indexerFlags (int),
            disableReleaseSwitching (bool). The observed POST carries NO per-file downloadId — the
            command-queue id/priority/status fields Lidarr adds on accept are NOT part of the body.

        Lidarr is primary -> raise_for_status surfaces a hard fault.
        """
        body = {
            "name": "ManualImport",
            "importMode": "move",   # [A1 LIVE] lowercase; Curator sends "move" for the atomic hardlink (D-09)
            "replaceExistingFiles": False,   # [A1 LIVE] top-level, as captured from the UI POST
            "sendUpdatesToClient": True,     # [A1 LIVE] UI default (optional but mirrored)
            "files": [
                {
                    "path": d.get("path"),
                    "artistId": (d.get("artist") or {}).get("id"),
                    "albumId": (d.get("album") or {}).get("id"),
                    "albumReleaseId": d.get("albumReleaseId"),
                    "trackIds": [t.get("id") for t in (d.get("tracks") or []) if isinstance(t, dict)],
                    "quality": d.get("quality"),   # the FULL QualityModel echoed from the candidate
                    "indexerFlags": d.get("indexerFlags", 0),
                    "disableReleaseSwitching": False,
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

    # === Phase 5: the D-02 Usenet-race check (GAP-03) ==============================================
    # Fallback-only politeness: skip an item that already has an active/queued Usenet grab so Curator
    # never races the primary pipeline. The *arr `records`/`albumId` queue wire keys are read HERE
    # (the firewall) — only a neutral bool crosses to the scheduler (05-04). Lidarr is primary, so a
    # hard fault surfaces (raise_for_status); the scheduler classifies that raise as an infra-skip
    # (NOT a burned attempt — REL-02), never as 'no active grab'.

    def get_queue_status(self, item: "GapItem") -> bool:
        """D-02/GAP-03: True iff the *arr download queue has an active/queued grab for this item.

        GET /api/v1/queue (page=1, pageSize=100); returns True iff any record's albumId (stringified)
        == item.arr_id (an in-flight Usenet grab exists -> Curator skips it); False if no match.
        Lidarr is primary -> raise_for_status surfaces a hard fault (the scheduler treats that as
        infra-skip). The `records`/`albumId` keys stay in this method; the return is a neutral bool.
        """
        r = self._client.get(
            f"{self._base}/api/v1/queue",
            headers=self._headers,
            params={"page": 1, "pageSize": 100},
            timeout=30.0,
        )
        r.raise_for_status()
        body = r.json()
        records = body.get("records", []) if isinstance(body, dict) else []
        # A2: confirm the albumId match field live (05-05).
        return any(
            str(rec.get("albumId")) == item.arr_id
            for rec in records
            if isinstance(rec, dict)
        )
