# Curator ReadarrAdapter — books, BEST-EFFORT. Structurally identical to the LidarrAdapter
# except: includeAuthor (not includeArtist), kind="book", and DEFENSIVE parsing throughout.
# This is the ARR-02 load-bearing module: Readarr is unmaintained (development halted 2024) and
# its metadata server can return empty/garbage records. A bad record/fault must SKIP+log, never
# raise into the core — books must never gate music.
#
# Field-key confidence: foreignBookId / (qualityProfileId or profileId) are MEDIUM-confidence
# (A-R1/A-R2). The defensive _map tolerates both profile-id spellings; a wrong guess skips a book,
# it does not crash. *arr field names live HERE (the firewall), never in core/state.
import logging
from typing import Optional

import httpx

from adapters.base import GapItem  # noqa: F401  (used by Phase-4 verify_imported signature)
from core.manifest import Manifest
from core.quality import Profile

log = logging.getLogger(__name__)

# Book quality NAME -> a neutral format ladder (worst -> best): PDF < MOBI < AZW3 < EPUB. These ints
# ride the SAME neutral Profile type as music (core has no book-specific vocabulary). This map is the
# firewall boundary for books: the Readarr quality-name vocabulary lives ONLY here. [A-R2: best-effort]
_BOOK_FORMAT_RANKS = {
    "pdf": 1,
    "mobi": 2,
    "azw3": 3,
    "epub": 4,
}


def _book_rank_for_name(name: Optional[str]) -> Optional[int]:
    """Map a Readarr book-format quality NAME to a neutral rank, or None if unknown (defensive)."""
    if not isinstance(name, str):
        return None
    return _BOOK_FORMAT_RANKS.get(name.strip().lower())


class ReadarrAdapter:
    """Reads monitored missing + cutoff-unmet books from Readarr, degrading gracefully.

    _paged() swallows httpx/JSON/shape errors -> [] (a Readarr fault never propagates).
    _map() returns None on a non-dict / missing-id / malformed record (skip + log.warning).
    """

    app = "readarr"

    def __init__(self, base_url: str, api_key: str, client: httpx.Client):
        # A None/empty key would yield {"X-Api-Key": None} and an opaque httpx header error on the
        # first request. Readarr is BEST-EFFORT: rather than crash, the caller (build_adapters)
        # treats a missing key as "Readarr disabled" and skips it, honouring ARR-02 (CR-01).
        if not api_key:
            raise ValueError("READARR_API_KEY is not set")
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {"X-Api-Key": api_key}   # [VERIFIED: Servarr v1 auth header]

    # Defensive cap mirroring LidarrAdapter — terminates even if Readarr reports bad pagination.
    _MAX_PAGES = 1000

    def _paged(self, path: str) -> list:
        """Page through the verified envelope BUT swallow ANY fault -> [].

        Identical paging loop to Lidarr's except includeAuthor=true and a try/except over
        httpx errors (HTTPError, timeout) + JSON/shape errors: log a warning and return []
        so a 5xx / timeout / hung Readarr never propagates into the detection loop (ARR-02).
        Termination guard mirrors Lidarr (BL-01): stop on an empty page, on the cutoff, or at
        a hard page cap so a pageSize:0 / bad-totalRecords envelope cannot spin forever.
        """
        records, page = [], 1
        try:
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
                        "includeAuthor": "true",
                    },
                    timeout=30.0,
                )
                r.raise_for_status()
                body = r.json()
                batch = body.get("records", [])
                records += batch
                page_size = body.get("pageSize") or 100   # treat 0/None as the default page size
                if not batch or page * page_size >= body.get("totalRecords", 0):
                    break
                page += 1
        except (httpx.HTTPError, ValueError, TypeError) as e:
            # Precisely which call raises each caught type (WR-05 — KeyError dropped, the loop
            # uses only .get() so no subscript can raise it):
            #   httpx.HTTPError -> raise_for_status() 4xx/5xx, timeouts, transport errors;
            #   ValueError      -> r.json() on a non-JSON body (json.JSONDecodeError subclasses it);
            #   TypeError       -> arithmetic on a non-numeric pageSize/totalRecords in the cutoff.
            # Intent is "any Readarr-shape fault -> []", never let one reach the core (ARR-02).
            log.warning("readarr _paged(%s) swallowed fault -> []: %s", path, e)
            return []
        return records

    def get_wanted(self) -> list:
        """Monitored missing + cutoff-unmet books; only successfully-mapped records are kept."""
        out = []
        for gap_type in ("missing", "cutoff"):
            for rec in self._paged(f"wanted/{gap_type}"):
                mapped = self._map(rec, gap_type)
                if mapped is not None:
                    out.append(mapped)
        return out

    def _map(self, rec, gap_type: str):
        """Defensive BookResource -> GapItem mapping; returns None (logged) on a bad record.

        A non-dict record or one missing `id` is skipped, not fatal (ARR-02). The body is also
        wrapped so a KeyError/TypeError/ValueError on an unexpected shape skips the book too.
        """
        if not isinstance(rec, dict) or rec.get("id") is None:
            log.warning("readarr record not a dict or missing id; skipping: %r", rec)
            return None
        try:
            author = rec.get("author") or {}
            return GapItem(
                arr_app="readarr",
                arr_id=str(rec["id"]),
                kind="book",
                gap_type=gap_type,
                title=rec.get("title"),
                artist_or_author=author.get("authorName"),
                foreign_id=rec.get("foreignBookId"),                       # A-R1: confirm vs live BookResource
                quality_profile_id=rec.get("qualityProfileId") or rec.get("profileId"),  # A-R2: tolerate both
                raw=rec,
            )
        except (KeyError, TypeError, ValueError) as e:
            log.warning("skipping malformed readarr record: %s", e)
            return None

    # An empty allowed-set Profile: the BEST-EFFORT safe default when a book profile cannot be read.
    # An empty `allowed` makes the gate reject every candidate (a book whose profile we can't resolve
    # is simply never acquired) rather than crashing the loop or over-permitting (ARR-02 fail-safe).
    _SAFE_DEFAULT_PROFILE = Profile(allowed=frozenset(), cutoff_rank=1)

    def get_quality_profile(self, profile_id: int) -> Profile:
        """BEST-EFFORT (ARR-02): GET the Readarr book quality profile and normalize to a neutral
        Profile over the book-format ladder. ANY fault (HTTP / JSON / unexpected shape) is swallowed
        to the empty-allowed safe default — a Readarr profile fault must NEVER raise into the loop and
        must NEVER gate music. Tolerates both nested `{"quality": {...}}` and bare-quality item shapes.
        """
        try:
            r = self._client.get(
                f"{self._base}/api/v1/qualityprofile/{profile_id}",
                headers=self._headers,
                timeout=30.0,
            )
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                return self._SAFE_DEFAULT_PROFILE

            allowed, id_to_rank = set(), {}
            for item in body.get("items") or []:
                if not isinstance(item, dict):
                    continue
                q = item.get("quality") if isinstance(item.get("quality"), dict) else item
                q = q or {}
                rank = _book_rank_for_name(q.get("name"))
                if rank is not None and q.get("id") is not None:
                    id_to_rank[q.get("id")] = rank
                if item.get("allowed") and rank is not None:
                    allowed.add(rank)

            cutoff = body.get("cutoff")
            if isinstance(cutoff, dict):
                cutoff = cutoff.get("id")
            cutoff_rank = id_to_rank.get(cutoff) or (min(allowed) if allowed else 1)
            return Profile(allowed=frozenset(allowed), cutoff_rank=cutoff_rank)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            log.warning("readarr get_quality_profile(%s) degraded -> safe default: %s", profile_id, e)
            return self._SAFE_DEFAULT_PROFILE

    def get_manifest(self, foreign_id: str) -> Manifest:
        """BEST-EFFORT (ARR-02): build a neutral book Manifest (author->artist, title->album,
        track_count=1, track_titles=None, kind='book') from the Readarr book record. ANY fault is
        swallowed to a stub-safe empty Manifest — a manifest fault degrades the single book, it never
        raises into the loop and never gates music. A book is one file, so track_count is always 1.
        """
        try:
            r = self._client.get(
                f"{self._base}/api/v1/book",
                headers=self._headers,
                params={"foreignBookId": foreign_id},
                timeout=30.0,
            )
            r.raise_for_status()
            payload = r.json()
            rec = payload[0] if isinstance(payload, list) and payload else payload
            if not isinstance(rec, dict):
                rec = {}
            author = rec.get("author") or {}
            return Manifest(
                artist=author.get("authorName") or "",
                album=rec.get("title") or "",
                track_count=1,                 # a book is a single item — track-count doesn't apply
                track_titles=None,             # omit the per-track sub-distance (graceful)
                kind="book",
                year=rec.get("releaseYear") if isinstance(rec.get("releaseYear"), int) else None,
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            log.warning("readarr get_manifest(%s) degraded -> empty book manifest: %s", foreign_id, e)
            return Manifest(artist="", album="", track_count=1, track_titles=None, kind="book")

    # === Phase 4: BEST-EFFORT import methods — swallow->safe default, books never gate music ========
    # Mirror LidarrAdapter's import shapes (the explicit ManualImport(Move) path) but wrap EVERY body
    # in the readarr swallow block so any fault degrades to a safe default (Pitfall 5 / ARR-02): a
    # Readarr 5xx/timeout/garbage can NEVER raise into the loop. Book-identity wire fields
    # (bookId/editionId/authorId) stay adapter-local (A5 — Readarr is unmaintained; a wrong guess
    # degrades the single book, music is untouched). Safe defaults: [] / None / False.

    def manual_import_candidates(self, path: str, download_id: Optional[str] = None) -> list:
        """BEST-EFFORT: GET the Readarr Manual Import mapping and return ONLY the importable subset
        (adapter-filtered, mirroring Lidarr — empty `rejections` + a non-empty resolved track list),
        so core stays *arr-key-blind. ANY fault swallows to [] (Pitfall 5 — core's filter step then
        sees an empty list and the book goes to quarantine-on-failure; music is never affected)."""
        try:
            r = self._client.get(
                f"{self._base}/api/v1/manualimport",
                headers=self._headers,
                params={
                    "folder": path,
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
            return [
                res for res in resources
                if isinstance(res, dict) and not res.get("rejections") and res.get("tracks")
            ]
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            log.warning("readarr manual_import_candidates degraded -> []: %s", e)
            return []

    def execute_import(self, decisions: list) -> Optional[None]:
        """BEST-EFFORT: POST an explicit ManualImport(Move) command for the chosen book files
        (mirrors Lidarr — never a blind rescan). Book wire fields (bookId/editionId/authorId) stay
        adapter-local (A5). ANY fault swallows to None (the book degrades; music is untouched)."""
        try:
            body = {
                "name": "ManualImport",
                "importMode": "Move",   # [ASSUMED A1: casing — verify live 04-05] atomic hardlink (D-09)
                "files": [
                    {
                        "path": d.get("path"),
                        "authorId": (d.get("author") or {}).get("id"),
                        "bookId": (d.get("book") or {}).get("id"),
                        "editionId": d.get("editionId"),
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
            return None
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            log.warning("readarr execute_import degraded -> None (book skipped): %s", e)
            return None

    def verify_imported(self, item: GapItem) -> bool:
        """BEST-EFFORT: confirm a real import by re-querying — True iff the book id LEFT the wanted
        list (D-03). ANY fault returns False (Pitfall 5 — a false-NEGATIVE forces quarantine, which is
        safe; a false-POSITIVE would skip cleanup and leave junk). Books never gate music (ARR-02).

        Note: this issues the wanted re-query DIRECTLY (not via get_wanted, whose _paged already
        swallows a fault to []) so a 5xx is observed here and degrades to False — never a fake True.
        """
        try:
            still_wanted = set()
            for gap_type in ("missing", "cutoff"):
                r = self._client.get(
                    f"{self._base}/api/v1/wanted/{gap_type}",
                    headers=self._headers,
                    params={"page": 1, "pageSize": 100, "monitored": "true", "includeAuthor": "true"},
                    timeout=30.0,
                )
                r.raise_for_status()
                body = r.json()
                for rec in (body.get("records") or []):
                    if isinstance(rec, dict) and rec.get("id") is not None:
                        still_wanted.add(str(rec["id"]))
            return item.arr_id not in still_wanted
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
            log.warning("readarr verify_imported degraded -> False (forces quarantine): %s", e)
            return False
