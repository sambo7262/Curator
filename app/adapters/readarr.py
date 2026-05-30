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

import httpx

from adapters.base import GapItem

log = logging.getLogger(__name__)


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
