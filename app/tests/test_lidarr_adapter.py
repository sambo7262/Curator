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
from adapters.base import ArrAdapter, GapItem
from adapters.lidarr import LidarrAdapter


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
