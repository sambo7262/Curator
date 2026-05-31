"""SHARE-01/02 (D-10) — the slskd shares ensure/self-heal surface, Wave 0 scaffold.

This module is created RED in 05-02 and lands GREEN at the *client* level in 05-02 Task 2
(get_shared_file_count + rescan_shares on SlskdClient). The ensure/self-heal CYCLE itself
(core/shares.py composing the neutral int + bool) is built in 05-03 — those cycle tests are
added there. Here we prove the two SlskdClient methods that the cycle will consume, plus the
FakeSlskd contract the 05-03/04 tests import.

Offline-only: httpx.MockTransport for the real client (mirrors test_slskd_client.py); FakeSlskd
(tests/fakes.py) for the neutral-seam contract. NO live slskd. The slskd `shares.files` wire key
+ the PUT /api/v0/shares rescan live ONLY in adapters/slskd.py (the firewall) — the client returns
a neutral int / bool to core.
"""
import httpx
import pytest

from adapters.slskd import SlskdClient
from tests.fakes import FakeSlskd


def _client(handler) -> SlskdClient:
    """Build an offline SlskdClient over a MockTransport handler (capital X-API-Key)."""
    transport = httpx.MockTransport(handler)
    return SlskdClient(
        "http://test-slskd", "secret-key",
        httpx.Client(transport=transport, base_url="http://test-slskd"),
    )


# --- get_shared_file_count: GET /api/v0/application -> shares.files (int, 0 on absent) ----------

def test_get_shared_file_count_reads_shares_files(load_fixture):
    """SHARE-02: get_shared_file_count GETs /api/v0/application and returns shares.files as an int."""
    application = load_fixture("slskd/application")
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=application)

    count = _client(_handler).get_shared_file_count()
    assert count == 1234
    assert isinstance(count, int)
    assert captured["method"] == "GET"
    assert captured["path"].endswith("/api/v0/application")
    # the capital X-API-Key header is carried, never the key in a log/exception (T-05-05)
    assert captured["headers"].get("x-api-key") == "secret-key"


def test_get_shared_file_count_absent_is_zero():
    """Defensive (T-05-06): an absent/non-dict shares or non-int files reads as 0, never KeyError."""
    for body in ({}, {"shares": None}, {"shares": {"directories": 3}}, {"shares": {"files": "lots"}}):
        def _handler(request: httpx.Request, _b=body) -> httpx.Response:
            return httpx.Response(200, json=_b)

        assert _client(_handler).get_shared_file_count() == 0


def test_get_shared_file_count_raises_on_5xx():
    """slskd is primary: a transport/HTTP fault surfaces (raise_for_status) so the caller can
    classify it as infra (REL-02), rather than silently reading 0."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "slskd down"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(_handler).get_shared_file_count()


# --- rescan_shares: PUT /api/v0/shares -> 204 True / 409 False / else raise ---------------------

def test_rescan_shares_204_returns_true():
    """SHARE-02 self-heal: a 204 (scan started) -> True; the call is a PUT to /api/v0/shares."""
    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        return httpx.Response(204)

    assert _client(_handler).rescan_shares() is True
    assert captured["method"] == "PUT"
    assert captured["path"].endswith("/api/v0/shares")
    assert captured["headers"].get("x-api-key") == "secret-key"


def test_rescan_shares_409_returns_false():
    """A 409 (a scan is already in progress) is 'already healing', NOT an error -> False (no raise)."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "scan already in progress"})

    assert _client(_handler).rescan_shares() is False


def test_rescan_shares_other_non_2xx_raises():
    """Any other non-2xx (e.g. 500) surfaces via raise_for_status (slskd is primary)."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(httpx.HTTPStatusError):
        _client(_handler).rescan_shares()


# --- FakeSlskd contract (imported by the 05-03/04 cycle + scheduler tests) ----------------------

def test_fake_slskd_count_and_rescan_call_counted():
    """tests.fakes.FakeSlskd exposes the neutral shares seam: an injectable count + a call-counted
    rescan (so a self-heal test can assert rescan was triggered exactly once)."""
    fake = FakeSlskd(count_sequence=0, rescan_result=True)
    assert fake.get_shared_file_count() == 0
    assert fake.rescan_calls == 0
    assert fake.rescan_shares() is True
    assert fake.rescan_calls == 1


def test_fake_slskd_count_sequence_advances():
    """A count_sequence lets a cross-cycle test model 'still 0 after a rescan' (Pitfall 6,
    eventually-consistent self-heal): successive reads advance, the last value repeats."""
    fake = FakeSlskd(count_sequence=[0, 0, 1234])
    assert [fake.get_shared_file_count() for _ in range(4)] == [0, 0, 1234, 1234]
