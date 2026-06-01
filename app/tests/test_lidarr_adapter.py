"""Phase-2 adapter coverage: LidarrAdapter mapping + pagination (GAP-01, GAP-02, ARR-02).

GAP-01 — test_missing_mapping: wanted/missing records map to GapItem(gap_type="missing")
         with correct stringified arr_id, foreign_id (foreignAlbumId), quality_profile_id
         (profileId), and artist_or_author.
GAP-02 — test_cutoff_and_paging: wanted/cutoff paginates the verified envelope across two
         fixture pages and yields gap_type="cutoff" GapItems for ALL pages' records.

Offline-only: uses the conftest httpx_client factory (httpx.MockTransport) — no live Lidarr.
The real run is Python 3.12 at CI/NAS; the local sandbox (Python 3.9 + no httpx) gates on
AST-parse + grep (see plan <automated>).
"""
import json

import httpx

from adapters.base import ArrAdapter, GapItem
from adapters.lidarr import LidarrAdapter, _resolve_profile_id
from core.manifest import Manifest
from core.quality import RANK_ALAC, RANK_FLAC, RANK_MP3_320, Profile


# --- quality-profile resolution (artist-level, fixes the profileId=0 -> 404 -> permanent-stuck bug) ---

def test_resolve_profile_prefers_artist_when_album_profile_is_zero():
    """REGRESSION: Lidarr quality profiles are artist-level; the album record's `profileId` is 0 for
    many albums, and profileId=0 -> GET /qualityprofile/0 -> 404 -> 'stuck (no search)' FOREVER.
    The embedded artist's qualityProfileId (includeArtist=true) must win over a 0 album profileId."""
    rec = {"id": 7, "profileId": 0, "artist": {"artistName": "Queen", "qualityProfileId": 3}}
    assert _resolve_profile_id(rec) == 3


def test_resolve_profile_falls_back_to_album_when_artist_absent():
    """When the artist has no usable qualityProfileId, fall back to the album profileId (never worse
    than the old behavior)."""
    assert _resolve_profile_id({"id": 1, "profileId": 2, "artist": {"artistName": "X"}}) == 2
    assert _resolve_profile_id({"id": 1, "profileId": 2}) == 2
    # artist profile of 0 is NOT a valid id -> fall back to the album profile id
    assert _resolve_profile_id(
        {"id": 1, "profileId": 5, "artist": {"qualityProfileId": 0}}
    ) == 5


def test_missing_mapping(httpx_client, load_fixture):
    """GAP-01: each wanted/missing record becomes a correctly-mapped 'missing' GapItem."""
    client = httpx_client({"wanted/missing": "lidarr_missing", "wanted/cutoff": "readarr_empty"})
    adapter = LidarrAdapter("http://test-arr", "k", client)

    gaps = adapter.get_wanted()
    missing = [g for g in gaps if g.gap_type == "missing"]

    fixture = load_fixture("lidarr_missing")
    assert len(missing) == len(fixture["records"]) == 2
    for gap, rec in zip(missing, fixture["records"]):
        assert isinstance(gap, GapItem)
        assert gap.arr_app == "lidarr"
        assert gap.kind == "album"
        assert gap.arr_id == str(rec["id"])          # stringified id
        assert gap.foreign_id == rec["foreignAlbumId"]
        assert gap.quality_profile_id == rec["profileId"]   # NOT qualityProfileId
        assert gap.artist_or_author == rec["artist"]["artistName"]
        assert gap.raw == rec


def test_cutoff_and_paging(httpx_client, load_fixture):
    """GAP-02: wanted/cutoff paginates across both fixture pages -> all 'cutoff' GapItems."""
    client = httpx_client({
        "wanted/missing": "readarr_empty",
        "wanted/cutoff": ["lidarr_cutoff", "lidarr_cutoff_page2"],   # page 1 then page 2
    })
    adapter = LidarrAdapter("http://test-arr", "k", client)

    gaps = adapter.get_wanted()
    cutoff = [g for g in gaps if g.gap_type == "cutoff"]

    page1 = load_fixture("lidarr_cutoff")["records"]
    page2 = load_fixture("lidarr_cutoff_page2")["records"]
    assert len(cutoff) == len(page1) + len(page2) == 3   # pagination loop pulled BOTH pages
    ids = {g.arr_id for g in cutoff}
    assert ids == {str(r["id"]) for r in page1 + page2}
    assert all(g.gap_type == "cutoff" and g.kind == "album" for g in cutoff)


def test_paging_terminates_on_malformed_envelope():
    """BL-01: a server reporting pageSize:0 with a non-zero totalRecords (so the arithmetic
    cutoff `page*pageSize >= totalRecords` is ALWAYS false) must NOT spin forever — the empty-page
    / pageSize-normalisation guard terminates the loop instead of hanging the primary path."""
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Page 1 returns one record but a poisoned pageSize:0 + totalRecords:5; every later
        # page returns an EMPTY records list. Without the guard the loop never terminates.
        if int(request.url.params.get("page", "1")) == 1:
            return httpx.Response(200, json={
                "page": 1, "pageSize": 0, "totalRecords": 5,
                "records": [{"id": 1, "title": "A", "foreignAlbumId": "m1",
                             "profileId": 1, "artist": {"artistName": "X"}}],
            })
        return httpx.Response(200, json={"page": 2, "pageSize": 0, "totalRecords": 5, "records": []})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    adapter = LidarrAdapter("http://test-arr", "k", client)

    gaps = adapter.get_wanted()   # must RETURN (not hang); empty page stops paging
    # one mapped record per route (missing + cutoff) since each route empties on page 2
    assert len(gaps) == 2
    # the loop made a bounded number of requests, not an unbounded spin
    assert calls["n"] < 10


def test_missing_api_key_fails_fast(httpx_client):
    """CR-01: a None/empty LIDARR_API_KEY must raise a clear error at construction, NOT defer to an
    opaque httpx header-encoding TypeError on the first request (Lidarr is the primary path)."""
    import pytest
    for bad in (None, ""):
        with pytest.raises(ValueError, match="LIDARR_API_KEY"):
            LidarrAdapter("http://test-arr", bad, httpx_client({}))


def test_missing_id_record_is_skipped_not_fatal(caplog):
    """WR-03: a record missing `id` must be skipped+logged, not KeyError-abort the primary run.
    One good + one bad record on the missing route -> one GapItem, no exception."""
    import logging

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/wanted/missing"):
            return httpx.Response(200, json={
                "page": 1, "pageSize": 100, "totalRecords": 2,
                "records": [
                    {"id": 1, "title": "Good", "foreignAlbumId": "m1",
                     "profileId": 1, "artist": {"artistName": "X"}},
                    {"title": "No id here", "foreignAlbumId": "m2"},   # malformed: missing id
                ],
            })
        return httpx.Response(200, json={"page": 1, "pageSize": 100, "totalRecords": 0, "records": []})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    adapter = LidarrAdapter("http://test-arr", "k", client)

    with caplog.at_level(logging.WARNING):
        gaps = adapter.get_wanted()   # must NOT raise KeyError

    assert [g.arr_id for g in gaps] == ["1"]   # only the well-formed record survives
    assert any("missing id" in r.message.lower() or "skipping" in r.message.lower()
               for r in caplog.records)


def test_lidarr_satisfies_protocol(httpx_client):
    """ARR-01: a LidarrAdapter exposes the Phase-2 ArrAdapter surface (app + callable get_wanted).

    Phase 2 implements ONLY get_wanted(); the import/command/profile methods are declared-and-
    stubbed on the Protocol but intentionally NOT implemented yet, so a full runtime-checkable
    isinstance() would over-assert. The plan sanctions attribute/callable checks for get_wanted.
    """
    adapter = LidarrAdapter("http://test-arr", "k", httpx_client({}))
    assert adapter.app == "lidarr"
    assert callable(adapter.get_wanted)
    assert ArrAdapter is not None   # the Protocol the core depends on exists / imports


# === QUAL-01: get_quality_profile normalizes *arr JSON -> neutral Profile (keys stay in lidarr.py) ===

def _profile_client(profile_json):
    """An offline client whose qualityprofile/{id} route returns the given *arr profile JSON."""
    def _handler(request: httpx.Request) -> httpx.Response:
        if "/api/v1/qualityprofile/" in request.url.path:
            return httpx.Response(200, json=profile_json)
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")


def test_get_quality_profile_returns_neutral_profile():
    """A lossless-only *arr profile (FLAC+ALAC allowed, cutoff=FLAC) maps to Profile({4,5}, cutoff 5)."""
    arr_json = {
        "id": 1, "name": "Lossless",
        "cutoff": 50,   # the FLAC quality id
        "items": [
            {"allowed": False, "quality": {"id": 10, "name": "MP3-320"}},
            {"allowed": True,  "quality": {"id": 40, "name": "ALAC"}},
            {"allowed": True,  "quality": {"id": 50, "name": "FLAC"}},
        ],
    }
    adapter = LidarrAdapter("http://test-arr", "k", _profile_client(arr_json))
    prof = adapter.get_quality_profile(1)
    assert isinstance(prof, Profile)
    assert prof.allowed == frozenset({RANK_ALAC, RANK_FLAC})
    assert prof.cutoff_rank == RANK_FLAC


def test_get_quality_profile_recurses_into_quality_groups():
    """REGRESSION (live: allowed=[] -> every candidate, FLAC included, rejected): real Lidarr profiles
    NEST qualities inside GROUPS (the 'Lossless' group wraps FLAC/ALAC). The flat loop saw only the
    group wrapper and returned an EMPTY allowed set. The walk must recurse so an allowed group's nested
    FLAC/ALAC are collected."""
    arr_json = {
        "id": 3, "name": "Any",
        "cutoff": 50,   # FLAC quality id (nested inside the Lossless group)
        "items": [
            {"allowed": False, "quality": {"id": 10, "name": "MP3-320"}},
            # a GROUP wrapper (no `quality` key; member qualities live in nested `items`):
            {"id": 1000, "name": "Lossless", "allowed": True, "items": [
                {"allowed": True, "quality": {"id": 40, "name": "ALAC"}},
                {"allowed": True, "quality": {"id": 50, "name": "FLAC"}},
            ]},
        ],
    }
    adapter = LidarrAdapter("http://test-arr", "k", _profile_client(arr_json))
    prof = adapter.get_quality_profile(3)
    assert prof.allowed == frozenset({RANK_ALAC, RANK_FLAC}), "nested FLAC/ALAC must be collected"
    assert prof.cutoff_rank == RANK_FLAC


def test_get_quality_profile_mp3_320_cutoff_maps_allowed_and_cutoff():
    """An MP3-320-cutoff profile maps allowed {320,ALAC,FLAC} with cutoff_rank at the MP3-320 tier."""
    arr_json = {
        "id": 2, "name": "MP3-320+",
        "cutoff": 10,   # the MP3-320 quality id
        "items": [
            {"allowed": True, "quality": {"id": 10, "name": "MP3-320"}},
            {"allowed": True, "quality": {"id": 40, "name": "ALAC"}},
            {"allowed": True, "quality": {"id": 50, "name": "FLAC"}},
            {"allowed": False, "quality": {"id": 5, "name": "MP3-192"}},
        ],
    }
    adapter = LidarrAdapter("http://test-arr", "k", _profile_client(arr_json))
    prof = adapter.get_quality_profile(2)
    assert prof.allowed == frozenset({RANK_MP3_320, RANK_ALAC, RANK_FLAC})
    assert prof.cutoff_rank == RANK_MP3_320


def test_get_quality_profile_defensive_on_missing_fields():
    """An empty/garbage profile body must NOT crash (A4-unconfirmed shape -> .get()-defensive)."""
    adapter = LidarrAdapter("http://test-arr", "k", _profile_client({}))
    prof = adapter.get_quality_profile(99)
    assert isinstance(prof, Profile)
    assert prof.allowed == frozenset()


# === get_manifest normalizes the album record -> neutral Manifest ================================

def test_get_manifest_returns_neutral_manifest_with_titles():
    """A Lidarr album record with a release track list maps to a Manifest with artist/album/titles."""
    album_json = [{
        "title": "OK Computer",
        "artist": {"artistName": "Radiohead"},
        "releaseYear": 1997,
        "releases": [{"tracks": [{"title": "Airbag"}, {"title": "Karma Police"}]}],
    }]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/album"):
            return httpx.Response(200, json=album_json)
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    man = LidarrAdapter("http://test-arr", "k", client).get_manifest("mbid-123")
    assert isinstance(man, Manifest)
    assert man.artist == "Radiohead"
    assert man.album == "OK Computer"
    assert man.kind == "album"
    assert man.track_titles == ("Airbag", "Karma Police")
    assert man.track_count == 2


def test_get_manifest_graceful_without_track_list():
    """No release track list -> track_titles is None (the graceful-omission path), never a crash."""
    album_json = [{"title": "Some Album", "artist": {"artistName": "X"}, "trackCount": 9}]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/album"):
            return httpx.Response(200, json=album_json)
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    man = LidarrAdapter("http://test-arr", "k", client).get_manifest("mbid-x")
    assert isinstance(man, Manifest)
    assert man.track_titles is None
    assert man.track_count == 9


def test_get_manifest_track_count_from_statistics():
    """LIVE REGRESSION: Lidarr's album record has NO top-level trackCount and no inline release track
    list — the real count lives in statistics.totalTrackCount. Reading the missing top-level field
    yielded track_count=0, which maxed matching._track_count_distance and floored EVERY candidate at
    ~0.40 (nothing ever accepted). The resolver must pull the count from statistics."""
    album_json = [{
        "title": "Safety EP",
        "artist": {"artistName": "Coldplay"},
        "statistics": {"trackFileCount": 0, "trackCount": 0, "totalTrackCount": 3},
        "releases": [{"monitored": True, "trackCount": 3}],
    }]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/album"):
            return httpx.Response(200, json=album_json)
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    man = LidarrAdapter("http://test-arr", "k", client).get_manifest("mbid-coldplay")
    assert man.artist == "Coldplay"
    assert man.album == "Safety EP"
    assert man.track_count == 3          # NOT 0 — the bug that floored every match at 0.40


def test_get_manifest_track_count_from_monitored_release_when_no_statistics():
    """Fallback chain: no statistics block -> take the monitored release's trackCount (not the first)."""
    album_json = [{
        "title": "A", "artist": {"artistName": "B"},
        "releases": [
            {"monitored": False, "trackCount": 17},   # a deluxe edition we are NOT tracking
            {"monitored": True, "trackCount": 10},     # the monitored release is authoritative
        ],
    }]

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/album"):
            return httpx.Response(200, json=album_json)
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    man = LidarrAdapter("http://test-arr", "k", client).get_manifest("mbid-b")
    assert man.track_count == 10


def test_lidarr_now_exposes_profile_and_manifest_methods(httpx_client):
    """Both new Phase-3 methods are now callable on the concrete adapter (Protocol conformance)."""
    adapter = LidarrAdapter("http://test-arr", "k", httpx_client({}))
    assert callable(adapter.get_quality_profile)
    assert callable(adapter.get_manifest)


# === Phase 4: ManualImport(Move) candidates / execute / verify (D-03/D-09; the firewall stays here) ===
# The *arr-agnostic import path (the reason this project exists, replacing Soularr's blind rescan):
#   - manual_import_candidates GETs /manualimport and returns ONLY the importable subset (the adapter
#     filters out resources with a non-empty rejections[] or an empty tracks[] — core stays key-blind);
#   - execute_import POSTs a ManualImport command in importMode=Move with a per-file files[] entry
#     (NEVER a DownloadedAlbumsScan blind rescan, T-04-09);
#   - verify_imported re-queries and returns True only if the item LEFT the wanted/missing list
#     (D-03: "downloaded" != "imported").
# All *arr wire keys (folder/downloadId/albumReleaseId/importMode/files[] + the rejections/tracks reads
# that drive the filter) live ONLY in lidarr.py.

def _capturing_command_client(captured: dict, *, status: int = 200):
    """An offline client whose /api/v1/command POST records the JSON body it received."""
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/command"):
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(status, json={"id": 1, "status": "queued"})
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")


def test_manual_import_candidates_returns_only_importable_subset(load_fixture):
    """The adapter GETs /manualimport and returns ONLY the importable resources (empty rejections +
    non-empty tracks). The rejected fixture resource (id 103, folder.jpg with rejections) is excluded
    — the adapter, not core, read rejections/tracks to make the importability decision."""
    mapping = load_fixture("manualimport/get_mapping")
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/manualimport"):
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=mapping)
        return httpx.Response(404, json={})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    adapter = LidarrAdapter("http://test-arr", "k", client)

    candidates = adapter.manual_import_candidates(
        "/data/downloads/soulseek/curator-lidarr-1234", download_id="curator-lidarr-1234"
    )

    # only the two importable resources (101 + 102) survive; the rejected 103 is dropped
    assert [c["id"] for c in candidates] == [101, 102]
    assert all(not c["rejections"] and c["tracks"] for c in candidates)
    # the GET carried the *arr query params (folder mapped from `path`, downloadId, filter flags)
    assert captured["params"]["folder"] == "/data/downloads/soulseek/curator-lidarr-1234"
    assert captured["params"]["downloadId"] == "curator-lidarr-1234"
    assert captured["params"]["filterExistingFiles"] == "true"
    assert captured["params"]["replaceExistingFiles"] == "true"


def test_execute_import_posts_manualimport_move_per_file(load_fixture):
    """execute_import POSTs a ManualImport command in importMode="move" (A1 live-pinned lowercase)
    with one files[] entry per decision carrying path/artistId/albumId/albumReleaseId/trackIds/quality
    — matching the A1-reconciled expected_post fixture."""
    decisions = [c for c in load_fixture("manualimport/get_mapping") if not c["rejections"] and c["tracks"]]
    expected = load_fixture("manualimport/expected_post")
    captured = {}
    adapter = LidarrAdapter("http://test-arr", "k", _capturing_command_client(captured))

    adapter.execute_import(decisions)

    body = captured["body"]
    assert body["name"] == "ManualImport"
    # A1 LIVE: importMode is LOWERCASE "move" (Curator's deliberate choice for the atomic hardlink,
    # D-09) — NOT the old [ASSUMED] capital "Move", NOT the UI default "copy".
    assert body["importMode"] == "move"
    assert body["importMode"] == expected["importMode"]
    # A1 LIVE: top-level replaceExistingFiles/sendUpdatesToClient as captured from the real UI POST.
    assert body["replaceExistingFiles"] is False
    assert body["sendUpdatesToClient"] is True
    assert len(body["files"]) == len(expected["files"]) == 2
    for got, exp in zip(body["files"], expected["files"]):
        assert got["path"] == exp["path"]
        assert got["artistId"] == exp["artistId"]
        assert got["albumId"] == exp["albumId"]
        assert got["albumReleaseId"] == exp["albumReleaseId"]
        assert got["trackIds"] == exp["trackIds"]
        assert got["quality"] == exp["quality"]   # the FULL QualityModel echoed from the candidate
        assert got["indexerFlags"] == exp["indexerFlags"]
        assert got["disableReleaseSwitching"] is False
        # A1 LIVE: the real POST carries NO per-file downloadId (command-queue metadata is not body).
        assert "downloadId" not in got


def test_execute_import_never_issues_downloaded_albums_scan(load_fixture):
    """T-04-09: the command must be an explicit ManualImport, NEVER a blind DownloadedAlbumsScan."""
    decisions = [c for c in load_fixture("manualimport/get_mapping") if not c["rejections"] and c["tracks"]]
    captured = {}
    adapter = LidarrAdapter("http://test-arr", "k", _capturing_command_client(captured))

    adapter.execute_import(decisions)

    assert captured["body"]["name"] == "ManualImport"
    assert captured["body"]["name"] != "DownloadedAlbumsScan"


def _verify_item(arr_id="1", foreign_id="m1"):
    return GapItem(
        arr_app="lidarr", arr_id=arr_id, kind="album", gap_type="missing",
        title="OK Computer", artist_or_author="Radiohead", foreign_id=foreign_id,
        quality_profile_id=1, raw={"id": int(arr_id), "foreignAlbumId": foreign_id},
    )


def test_verify_imported_true_when_item_left_wanted_list():
    """D-03: verify_imported returns True when a re-query shows the item's id is ABSENT from
    wanted/missing+cutoff — the item truly left the wanted list (a real import)."""
    def _handler(request: httpx.Request) -> httpx.Response:
        # wanted re-query returns OTHER ids only (the imported item 1 is gone)
        if request.url.path.endswith("/api/v1/wanted/missing"):
            return httpx.Response(200, json={
                "page": 1, "pageSize": 100, "totalRecords": 1,
                "records": [{"id": 99, "title": "Other", "foreignAlbumId": "m99",
                             "profileId": 1, "artist": {"artistName": "Z"}}],
            })
        return httpx.Response(200, json={"page": 1, "pageSize": 100, "totalRecords": 0, "records": []})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    adapter = LidarrAdapter("http://test-arr", "k", client)
    assert adapter.verify_imported(_verify_item(arr_id="1")) is True


def test_verify_imported_false_when_item_still_present():
    """D-03: 'downloaded' is NOT 'imported' — if the item is STILL on the wanted list, verify is False."""
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/v1/wanted/missing"):
            return httpx.Response(200, json={
                "page": 1, "pageSize": 100, "totalRecords": 1,
                "records": [{"id": 1, "title": "OK Computer", "foreignAlbumId": "m1",
                             "profileId": 1, "artist": {"artistName": "Radiohead"}}],
            })
        return httpx.Response(200, json={"page": 1, "pageSize": 100, "totalRecords": 0, "records": []})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    adapter = LidarrAdapter("http://test-arr", "k", client)
    assert adapter.verify_imported(_verify_item(arr_id="1")) is False


def test_import_methods_propagate_5xx_primary_posture():
    """Lidarr is primary: a 5xx on any import method surfaces (raise_for_status), never swallowed."""
    import pytest

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "lidarr down"})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    adapter = LidarrAdapter("http://test-arr", "k", client)
    with pytest.raises(httpx.HTTPStatusError):
        adapter.manual_import_candidates("/data/downloads/x", download_id="x")
    with pytest.raises(httpx.HTTPStatusError):
        adapter.execute_import([{"path": "/p", "artist": {"id": 1}, "album": {"id": 2},
                                 "albumReleaseId": 3, "tracks": [{"id": 4}],
                                 "quality": {}, "downloadId": "x"}])
    with pytest.raises(httpx.HTTPStatusError):
        adapter.verify_imported(_verify_item())


def test_lidarr_exposes_import_methods(httpx_client):
    """Protocol conformance: the three Phase-4 import methods are callable on the concrete adapter."""
    adapter = LidarrAdapter("http://test-arr", "k", httpx_client({}))
    assert callable(adapter.manual_import_candidates)
    assert callable(adapter.execute_import)
    assert callable(adapter.verify_imported)
