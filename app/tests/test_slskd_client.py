"""Phase-4 slskd client coverage: SlskdClient covers the full search→enqueue→watch→cancel
surface over /api/v0 with the capitalized X-API-Key header (ACQ-01/02/03, IMPORT-05 setup).

Offline-only: every test drives an httpx.MockTransport handler keyed on request.url.path +
method, serving the 04-01 slskd fixtures (slskd/search_responses.json, slskd/transfer_*.json).
No live slskd, no respx import — mirrors test_lidarr_adapter.py's MockTransport style so the
suite stays green in the Python 3.9 offline sandbox as well as on the 3.12 CI/NAS run.

The verified endpoint contract (04-RESEARCH §Pattern 1, base appends /api/v0, header capital
X-API-Key):
  POST   /api/v0/searches                                    {"searchText": text}  -> {"id": guid}
  GET    /api/v0/searches/{id}                                                     -> {state,...}
  GET    /api/v0/searches/{id}/responses                                           -> [response]
  POST   /api/v0/transfers/downloads/{username}              files list            -> enqueue
  GET    /api/v0/transfers/downloads/{username}/{id}                               -> {state, bytes}
  DELETE /api/v0/transfers/downloads/{username}/{id}?remove=true                   -> cancel
"""
import json

import httpx
import pytest

from adapters.slskd import SlskdClient, TransferHandle


def _record_client(recorder, responses):
    """An offline slskd client whose handler records each (method, path, params, body) and
    returns a canned httpx.Response chosen by a (method, path-predicate) -> response_fn map.

    `responses` is a list of (predicate, response_factory) tuples; the first predicate that
    matches request wins. A predicate is callable(request) -> bool. The response_factory is
    callable(request) -> httpx.Response.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = None
        recorder.append({
            "method": request.method,
            "path": request.url.path,
            "params": dict(request.url.params),
            "headers": dict(request.headers),
            "body": body,
        })
        for predicate, factory in responses:
            if predicate(request):
                return factory(request)
        return httpx.Response(404, json={"error": "no route", "path": request.url.path})

    return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-slskd")


def _client_for(routes, recorder=None):
    """Convenience: build a recording client from a list of route tuples."""
    if recorder is None:
        recorder = []
    return SlskdClient("http://test-slskd", "secret-key", _record_client(recorder, routes)), recorder


# --- construction / fail-fast -------------------------------------------------------------------

def test_missing_api_key_fails_fast():
    """A None/empty SLSKD_API_KEY must raise a clear ValueError at construction (mirrors Lidarr)."""
    bare = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url="http://test-slskd",
    )
    for bad in (None, ""):
        with pytest.raises(ValueError, match="SLSKD_API_KEY"):
            SlskdClient("http://test-slskd", bad, bare)


def test_base_appends_api_v0_and_header_is_capital_api_key():
    """The client base must end /api/v0 and every request must carry X-API-Key (capital API)."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: True, lambda r: httpx.Response(200, json={"id": "s1"}))],
        recorder,
    )
    client.search("Radiohead OK Computer")
    rec = recorder[-1]
    assert "/api/v0/" in rec["path"]
    # httpx lower-cases header keys on the request object; assert the canonical name + value.
    assert rec["headers"].get("x-api-key") == "secret-key"
    # The capitalized header name lives in the client's _headers dict exactly.
    assert client._headers == {"X-API-Key": "secret-key"}


def test_base_url_trailing_slash_normalized():
    """A base_url with a trailing slash must not produce a double slash before /api/v0."""
    bare = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        base_url="http://test-slskd",
    )
    c = SlskdClient("http://test-slskd/", "k", bare)
    assert c._base == "http://test-slskd/api/v0"


# --- search -------------------------------------------------------------------------------------

def test_search_posts_searchtext_and_returns_id():
    """search(text) POSTs to /api/v0/searches with json containing searchText, returns .get('id')."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "POST" and r.url.path.endswith("/api/v0/searches"),
          lambda r: httpx.Response(200, json={"id": "search-guid-123", "state": "InProgress"}))],
        recorder,
    )
    sid = client.search("Radiohead OK Computer")
    assert sid == "search-guid-123"
    rec = recorder[-1]
    assert rec["method"] == "POST"
    assert rec["path"].endswith("/api/v0/searches")
    assert rec["body"] == {"searchText": "Radiohead OK Computer"}


def test_search_id_absent_is_none_safe():
    """If the search POST response lacks 'id', search() returns None (never KeyError)."""
    client, _ = _client_for(
        [(lambda r: r.method == "POST", lambda r: httpx.Response(200, json={"state": "InProgress"}))],
    )
    assert client.search("anything") is None


def test_search_state_gets_search_and_returns_dict():
    """search_state(sid) GETs /api/v0/searches/{sid} and returns the dict (isComplete readable)."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "GET" and r.url.path.endswith("/api/v0/searches/sid-9"),
          lambda r: httpx.Response(200, json={
              "id": "sid-9", "state": "Completed", "isComplete": True,
              "responseCount": 2, "fileCount": 15}))],
        recorder,
    )
    state = client.search_state("sid-9")
    assert isinstance(state, dict)
    assert state.get("isComplete") is True
    assert state.get("responseCount") == 2
    assert recorder[-1]["method"] == "GET"
    assert recorder[-1]["path"].endswith("/api/v0/searches/sid-9")


def test_search_responses_returns_list_from_fixture(load_fixture):
    """search_responses(sid) GETs /searches/{sid}/responses and returns the fixture list."""
    fixture = load_fixture("slskd/search_responses.json".replace(".json", ""))
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "GET" and r.url.path.endswith("/responses"),
          lambda r: httpx.Response(200, json=fixture))],
        recorder,
    )
    responses = client.search_responses("sid-9")
    assert isinstance(responses, list)
    assert len(responses) == len(fixture) == 2
    assert responses[0]["username"] == "good_seeder"
    assert recorder[-1]["path"].endswith("/api/v0/searches/sid-9/responses")


def test_search_responses_non_list_is_empty_safe():
    """A malformed (non-list) responses body degrades to [] (never crashes, T-04-06)."""
    client, _ = _client_for(
        [(lambda r: r.url.path.endswith("/responses"),
          lambda r: httpx.Response(200, json={"unexpected": "dict"}))],
    )
    assert client.search_responses("sid") == []


# --- enqueue ------------------------------------------------------------------------------------

def test_enqueue_posts_files_list_to_downloads_username():
    """enqueue(username, files) POSTs the files list to /transfers/downloads/{username}."""
    recorder = []
    files = [
        {"filename": "Radiohead - OK Computer (1997) [FLAC]/01 - Airbag.flac", "size": 24000000},
        {"filename": "Radiohead - OK Computer (1997) [FLAC]/02 - Paranoid Android.flac", "size": 32000000},
    ]
    client, _ = _client_for(
        [(lambda r: r.method == "POST" and "/api/v0/transfers/downloads/" in r.url.path,
          lambda r: httpx.Response(201, json={}))],
        recorder,
    )
    client.enqueue("good_seeder", files)   # must not raise on 201
    rec = recorder[-1]
    assert rec["method"] == "POST"
    assert rec["path"].endswith("/api/v0/transfers/downloads/good_seeder")
    assert rec["body"] == files


def test_enqueue_200_does_not_raise():
    """enqueue tolerates a 200 (as well as 201) terminal-OK status without raising."""
    client, _ = _client_for(
        [(lambda r: r.method == "POST", lambda r: httpx.Response(200, json={}))],
    )
    client.enqueue("u", [{"filename": "f", "size": 1}])


# --- transfer (watch) ---------------------------------------------------------------------------

def test_transfer_returns_dict_state_and_bytes_readable(load_fixture):
    """transfer(username, tid) GETs the transfer dict; state + bytesTransferred read via .get()."""
    completed = load_fixture("slskd/transfer_completed")
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "GET" and "/transfers/downloads/good_seeder/" in r.url.path,
          lambda r: httpx.Response(200, json=completed))],
        recorder,
    )
    t = client.transfer("good_seeder", "transfer-1")
    assert isinstance(t, dict)
    assert t.get("state") == "Completed, Succeeded"
    assert t.get("bytesTransferred") == 24000000
    assert recorder[-1]["path"].endswith("/api/v0/transfers/downloads/good_seeder/transfer-1")


def test_transfer_missing_fields_never_keyerror():
    """An absent state/bytesTransferred reads as None/absent via .get(), never KeyError (T-04-06)."""
    client, _ = _client_for(
        [(lambda r: r.method == "GET", lambda r: httpx.Response(200, json={"id": "x"}))],
    )
    t = client.transfer("u", "x")
    assert t.get("state") is None
    assert t.get("bytesTransferred") is None      # .get() default, never KeyError


# --- cancel -------------------------------------------------------------------------------------

def test_cancel_issues_delete_with_remove_true():
    """cancel(username, tid) DELETEs /transfers/downloads/{username}/{id} with remove=true query."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "DELETE", lambda r: httpx.Response(204))],
        recorder,
    )
    client.cancel("good_seeder", "transfer-2")
    rec = recorder[-1]
    assert rec["method"] == "DELETE"
    assert rec["path"].endswith("/api/v0/transfers/downloads/good_seeder/transfer-2")
    assert rec["params"].get("remove") == "true"


def test_cancel_remove_false_passes_false():
    """cancel(..., remove=False) issues remove=false (the caller controls the on-disk cleanup)."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "DELETE", lambda r: httpx.Response(204))],
        recorder,
    )
    client.cancel("u", "t", remove=False)
    assert recorder[-1]["params"].get("remove") == "false"


# --- search cleanup (slskd 409-accumulation fix) ----------------------------------------------------

def test_delete_search_issues_delete_on_search_id():
    """delete_search(id) DELETEs /searches/{id} so slskd drops the tracked search (prevents a later
    duplicate query from 409-ing)."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: r.method == "DELETE", lambda r: httpx.Response(204))],
        recorder,
    )
    client.delete_search("search-guid-123")
    rec = recorder[-1]
    assert rec["method"] == "DELETE"
    assert rec["path"].endswith("/api/v0/searches/search-guid-123")


def test_delete_search_404_is_tolerated():
    """A search already gone (404) is fine — delete_search must NOT raise on a missing search."""
    client, _ = _client_for(
        [(lambda r: r.method == "DELETE", lambda r: httpx.Response(404, json={"error": "gone"}))],
    )
    client.delete_search("already-removed")  # no exception


def test_delete_search_other_error_surfaces():
    """A non-2xx, non-404 (e.g. 500) still surfaces (slskd is primary) so the best-effort caller can
    swallow it explicitly rather than it passing silently."""
    client, _ = _client_for(
        [(lambda r: r.method == "DELETE", lambda r: httpx.Response(500, json={"error": "boom"}))],
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.delete_search("s1")


# --- A2: remote-folder-leaf landing-dir resolution (pinned live 2026-05-31) ---------------------

def test_remote_folder_leaf_splits_on_backslash_and_slash():
    """A2: slskd lands files under ONLY the last segment of the peer's remote folder, and slskd
    reports those paths with `\\` separators. _remote_folder_leaf must split on BOTH `\\` and `/`."""
    from adapters.slskd import _remote_folder_leaf

    assert _remote_folder_leaf("music\\ZHU\\BLACK MIDAS (2026)") == "BLACK MIDAS (2026)"
    assert _remote_folder_leaf("music/ZHU/BLACK MIDAS (2026)") == "BLACK MIDAS (2026)"
    assert _remote_folder_leaf("BLACK MIDAS (2026)") == "BLACK MIDAS (2026)"
    assert _remote_folder_leaf("music\\ZHU\\Album\\") == "Album"     # trailing sep tolerated
    assert _remote_folder_leaf("") == ""
    assert _remote_folder_leaf(None) == ""                           # defensive: never raises


def test_enqueue_candidate_handle_carries_remote_folder_leaf():
    """A2: enqueue_candidate returns a TransferHandle whose neutral landing_dir_name is the LEAF of
    the candidate's remote folder — the dir slskd actually lands the files in (no batchId/username
    subdir). acquire reads this to point the import + purge at the real landing folder."""
    from types import SimpleNamespace

    client, _ = _client_for(
        [(lambda r: r.method == "POST", lambda r: httpx.Response(201, json={}))],
    )
    file_obj = SimpleNamespace(filename="music\\ZHU\\BLACK MIDAS (2026)\\01 - Intro.flac", size_bytes=100)
    cand = SimpleNamespace(
        username="zhuseed",
        folder="music\\ZHU\\BLACK MIDAS (2026)",
        files=[file_obj],
        audio_files=lambda: [file_obj],
    )
    handle = client.enqueue_candidate(cand)
    assert handle.landing_dir_name == "BLACK MIDAS (2026)"
    assert handle.username == "zhuseed"
    # the handle carries the enqueued filenames so progress/cancel can find OUR files (slskd has no
    # username-keyed transfer — addressing by username/username 400'd live, 2026-05-31).
    assert handle.filenames == ("music\\ZHU\\BLACK MIDAS (2026)\\01 - Intro.flac",)


def test_enqueue_candidate_leaf_falls_back_to_file_dir_when_folder_empty():
    """A2 fallback: when the candidate folder is empty, the leaf is derived from a file's directory
    portion (slskd filenames carry the full `\\`-separated peer path)."""
    from types import SimpleNamespace

    client, _ = _client_for(
        [(lambda r: r.method == "POST", lambda r: httpx.Response(201, json={}))],
    )
    file_obj = SimpleNamespace(filename="music\\ZHU\\BLACK MIDAS (2026)\\01 - Intro.flac", size_bytes=100)
    cand = SimpleNamespace(
        username="zhuseed", folder="", files=[file_obj], audio_files=lambda: [file_obj]
    )
    handle = client.enqueue_candidate(cand)
    assert handle.landing_dir_name == "BLACK MIDAS (2026)"


# --- transfer_progress / cancel_transfer via the per-user downloads list ------------------------
# REGRESSION (2026-05-31): the handle used to address transfers by username/username, so the progress
# GET hit /transfers/downloads/{user}/{user} -> 400 and EVERY accepted download died. slskd keys each
# file by its own id; progress/cancel now read the per-user list and match OUR files by name.

_F1 = "music\\Queen\\A Night at the Opera\\01 - Death on Two Legs.flac"
_F2 = "music\\Queen\\A Night at the Opera\\02 - Lazing on a Sunday Afternoon.flac"


def _downloads_body(files):
    """The slskd GET /transfers/downloads/{username} shape: user -> directories -> files."""
    return {
        "username": "winterwulf",
        "directories": [{"directory": "music\\Queen\\A Night at the Opera", "files": files}],
    }


def _progress_for(files):
    body = _downloads_body(files)
    client, rec = _client_for(
        [(lambda r: r.method == "GET" and r.url.path.endswith("/transfers/downloads/winterwulf"),
          lambda r: httpx.Response(200, json=body))],
    )
    handle = TransferHandle(username="winterwulf", filenames=(_F1, _F2))
    return client.transfer_progress(handle), rec


def test_transfer_progress_in_progress_sums_bytes_not_terminal():
    """Some files still transferring -> terminal None, bytes_done = sum across OUR files."""
    prog, _ = _progress_for([
        {"id": "g1", "filename": _F1, "state": "InProgress", "bytesTransferred": 100, "size": 500},
        {"id": "g2", "filename": _F2, "state": "Queued", "bytesTransferred": 0, "size": 400},
    ])
    assert prog.terminal is None
    assert prog.bytes_done == 100


def test_transfer_progress_success_only_when_all_completed_succeeded():
    """ALL files 'Completed, Succeeded' -> success with the full byte total."""
    prog, _ = _progress_for([
        {"id": "g1", "filename": _F1, "state": "Completed, Succeeded", "bytesTransferred": 500, "size": 500},
        {"id": "g2", "filename": _F2, "state": "Completed, Succeeded", "bytesTransferred": 400, "size": 400},
    ])
    assert prog.terminal == "success"
    assert prog.bytes_done == 900


def test_transfer_progress_any_failed_file_fails_the_grab():
    """ANY terminal non-success file -> failure (fall to the next candidate)."""
    prog, _ = _progress_for([
        {"id": "g1", "filename": _F1, "state": "Completed, Succeeded", "bytesTransferred": 500, "size": 500},
        {"id": "g2", "filename": _F2, "state": "Completed, Errored", "bytesTransferred": 12, "size": 400},
    ])
    assert prog.terminal == "failure"


def test_transfer_progress_unlisted_is_no_progress_not_terminal():
    """Right after enqueue the file may not be listed yet -> no progress, not terminal (keep watching)."""
    prog, _ = _progress_for([
        {"id": "z", "filename": "music\\Someone Else\\unrelated.flac", "state": "InProgress", "bytesTransferred": 9},
    ])
    assert prog.terminal is None
    assert prog.bytes_done == 0


def test_cancel_transfer_resolves_real_ids_and_deletes_each():
    """cancel_transfer reads the per-user list, resolves each file's real id, and DELETEs by id
    (never username/username). Matches by basename too (path/prefix drift tolerant)."""
    body = _downloads_body([
        {"id": "guid-1", "filename": _F1, "state": "InProgress", "bytesTransferred": 100},
        {"id": "guid-2", "filename": _F2, "state": "InProgress", "bytesTransferred": 50},
    ])
    recorder = []
    client, rec = _client_for(
        [
            (lambda r: r.method == "GET" and r.url.path.endswith("/transfers/downloads/winterwulf"),
             lambda r: httpx.Response(200, json=body)),
            (lambda r: r.method == "DELETE", lambda r: httpx.Response(204)),
        ],
        recorder=recorder,
    )
    client.cancel_transfer(TransferHandle(username="winterwulf", filenames=(_F1, _F2)))
    deletes = [e for e in rec if e["method"] == "DELETE"]
    assert {e["path"].rsplit("/", 1)[-1] for e in deletes} == {"guid-1", "guid-2"}
    assert all(e["params"].get("remove") == "true" for e in deletes)


# --- hard-fault posture (slskd is the new primary download path) --------------------------------

def test_search_raises_on_5xx():
    """slskd is the primary download path — a hard 5xx surfaces (raise_for_status), not swallowed.
    Uses 500 (not the transient 502/503/504 family) so it proves the surface WITHOUT the retry delay;
    the transient-status retry is covered by the _send retry tests below."""
    client, _ = _client_for(
        [(lambda r: True, lambda r: httpx.Response(500, json={"error": "down"}))],
    )
    with pytest.raises(httpx.HTTPStatusError):
        client.search("x")


def test_persistent_503_surfaces_after_retries_exhausted():
    """A transient 502/503/504 IS retried, but if it never clears the final response still surfaces."""
    client, calls = _counting_client([lambda r: httpx.Response(503, json={"error": "down"})], max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        client.search("x")
    assert calls["n"] == 3   # initial + 2 retries, then the 503 surfaces


def test_every_method_carries_api_key_header():
    """Every method uses self._headers carrying X-API-Key on the /api/v0 base (T-04-07 sourcing)."""
    recorder = []
    client, _ = _client_for(
        [(lambda r: True, lambda r: httpx.Response(200, json={"id": "s", "state": "x"}))],
        recorder,
    )
    client.search("a")
    client.search_state("s")
    client.search_responses("s")
    client.enqueue("u", [])
    client.transfer("u", "t")
    client.cancel("u", "t")
    assert len(recorder) == 6
    for rec in recorder:
        assert rec["headers"].get("x-api-key") == "secret-key"
        assert "/api/v0/" in rec["path"]


# --- bounded retry through the slskd<->gluetun VPN reconnect window ------------------------------
# The flap drops slskd's Soulseek connection for ~1-2s; during it, a search submit 409s and a
# poll/shares GET connection-resets (Errno 104). _send rides it out with a short bounded retry so a
# transient flap no longer fails an item or aborts a whole cycle. Real 4xx still surface unchanged.

def _counting_client(script, *, backoff_base=0.0, max_retries=3):
    """Build a client whose Nth call runs script[N] (a request->Response fn that may raise). Returns
    (client, calls) where calls['n'] is the attempt count. backoff_base=0 -> no real sleep in tests."""
    calls = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        i = calls["n"]
        calls["n"] += 1
        return script[min(i, len(script) - 1)](request)

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-slskd")
    return (
        SlskdClient("http://test-slskd", "k", client, backoff_base=backoff_base, max_retries=max_retries),
        calls,
    )


def _raise_reset(_r):
    raise httpx.ReadError("[Errno 104] Connection reset by peer")


def test_send_retries_transport_reset_then_succeeds():
    """A connection-reset (the flap signature on a poll/shares GET) is retried, not raised."""
    client, calls = _counting_client([_raise_reset, lambda r: httpx.Response(200, json={"isComplete": True})])
    assert client.search_state("s1") == {"isComplete": True}
    assert calls["n"] == 2   # one reset + one success


def test_send_retries_flap_409_on_search_submit_then_succeeds():
    """The VPN-flap 409 on POST /searches is retried through the reconnect (search not failed)."""
    client, calls = _counting_client([
        lambda r: httpx.Response(409, json={"error": "conflict"}),
        lambda r: httpx.Response(200, json={"id": "search-9"}),
    ])
    assert client.search("Queen A Night at the Opera") == "search-9"
    assert calls["n"] == 2


def test_enqueue_is_not_retried_on_transport_error():
    """enqueue is non-idempotent (retry=False): a transport fault surfaces immediately, never a
    second POST that could double-enqueue the download."""
    client, calls = _counting_client([_raise_reset, lambda r: httpx.Response(201)])
    with pytest.raises(httpx.TransportError):
        client.enqueue("winterwulf", [{"filename": "f", "size": 1}])
    assert calls["n"] == 1   # no retry


def test_send_gives_up_and_raises_after_max_retries():
    """A persistent transport fault still surfaces after the bounded retries are exhausted."""
    client, calls = _counting_client([_raise_reset], max_retries=3)
    with pytest.raises(httpx.TransportError):
        client.search_state("s")
    assert calls["n"] == 4   # initial attempt + 3 retries


def test_real_4xx_is_not_retried():
    """A genuine 404/400 must surface to the caller on the FIRST response (not retried as transient)."""
    client, calls = _counting_client([lambda r: httpx.Response(404, json={})])
    # search_state raises on the 404 via raise_for_status; the point is it did NOT retry.
    with pytest.raises(httpx.HTTPStatusError):
        client.search_state("missing")
    assert calls["n"] == 1
