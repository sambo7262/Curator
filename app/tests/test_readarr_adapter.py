"""Phase-2 adapter coverage: ReadarrAdapter graceful degradation + the breaker (ARR-02).

The load-bearing requirement: a Readarr fault must NEVER crash or gate the music path.
- test_empty                 : an empty wanted envelope -> get_wanted() == [] (no exception).
- test_garbage_skips_and_logs: garbage/malformed records are SKIPPED+logged; only the valid
                               ones become GapItems; no exception is raised.
- test_5xx_returns_empty     : a 500 from Readarr -> get_wanted() == [] (fault swallowed in _paged).
- test_breaker_opens         : a ReadarrAdapter that always raises, wrapped in a CircuitBreaker,
                               returns [] every call past the threshold and NEVER raises.

Offline-only (conftest httpx.MockTransport); real run is Python 3.12 at CI/NAS.
"""
import logging

import httpx

from adapters.breaker import CircuitBreaker
from adapters.readarr import ReadarrAdapter


def _adapter(client) -> ReadarrAdapter:
    return ReadarrAdapter("http://test-arr", "k", client)


def test_empty(httpx_client):
    """ARR-02: an empty wanted envelope yields [] without raising."""
    client = httpx_client({"wanted/missing": "readarr_empty", "wanted/cutoff": "readarr_empty"})
    assert _adapter(client).get_wanted() == []


def test_garbage_skips_and_logs(httpx_client, caplog):
    """ARR-02: bad records (missing id, non-dict) are skipped+logged; valid ones survive; no raise."""
    # readarr_garbage has 4 records per route: 1 valid (id=401), 1 missing-id (skip),
    # 1 non-dict string (skip), 1 valid-with-null-fields (id=404). Served on BOTH routes.
    client = httpx_client({"wanted/missing": "readarr_garbage", "wanted/cutoff": "readarr_garbage"})
    with caplog.at_level(logging.WARNING):
        gaps = _adapter(client).get_wanted()

    # 2 valid records per route x 2 routes (missing + cutoff) = 4 GapItems.
    assert len(gaps) == 4
    assert {g.arr_id for g in gaps} == {"401", "404"}
    assert all(g.kind == "book" and g.arr_app == "readarr" for g in gaps)
    # the null-field record still maps (valid id) but with None metadata
    null_rec = next(g for g in gaps if g.arr_id == "404")
    assert null_rec.title is None and null_rec.artist_or_author is None
    # the bad records were logged, not raised
    assert any("skipping" in r.message.lower() or "missing id" in r.message.lower()
               for r in caplog.records)


def test_missing_api_key_raises(httpx_client):
    """CR-01: a None/empty READARR_API_KEY raises at construction (the caller catches this to skip
    Readarr) rather than building {"X-Api-Key": None} and failing opaquely on first request."""
    import pytest
    for bad in (None, ""):
        with pytest.raises(ValueError, match="READARR_API_KEY"):
            ReadarrAdapter("http://test-arr", bad, httpx_client({}))


def test_5xx_returns_empty():
    """ARR-02: a 5xx from Readarr is swallowed in _paged -> get_wanted() == [] (music unaffected)."""
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "readarr is down"})

    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    assert _adapter(client).get_wanted() == []


def test_breaker_opens():
    """ARR-02: a hard-down Readarr trips the breaker -> get_wanted() returns [] and never raises."""
    class _AlwaysRaises:
        app = "readarr"

        def get_wanted(self):
            raise RuntimeError("readarr exploded")

    breaker = CircuitBreaker(_AlwaysRaises(), fail_threshold=3)
    # Call past the threshold; every call must return [] and never propagate the exception.
    for _ in range(breaker.fail_threshold + 2):
        assert breaker.get_wanted() == []
    assert breaker._open()              # breaker latched open after repeated faults
    assert breaker.app == "readarr"     # drop-in ArrAdapter identity preserved


def test_breaker_recovers_after_cooldown():
    """WR-04: once the cooldown elapses the breaker goes half-open, and a now-healthy inner call
    CLOSES it — books re-enable automatically without a process restart."""
    class _Flaky:
        app = "readarr"

        def __init__(self):
            self.healthy = False

        def get_wanted(self):
            if not self.healthy:
                raise RuntimeError("readarr down")
            return ["recovered"]

    inner = _Flaky()
    # reset_after=0 -> the cooldown is always 'elapsed', so the call after open is half-open.
    breaker = CircuitBreaker(inner, fail_threshold=2, reset_after=0.0)

    assert breaker.get_wanted() == []   # failure 1
    assert breaker.get_wanted() == []   # failure 2 -> tripped/open
    assert breaker._open()

    # Readarr recovers; the next (half-open) trial call succeeds and closes the breaker.
    inner.healthy = True
    assert breaker.get_wanted() == ["recovered"]
    assert not breaker._open()          # closed again — recovery is automatic (no restart)
    assert breaker.get_wanted() == ["recovered"]


def test_breaker_stays_open_during_cooldown():
    """WR-04: while the cooldown window has NOT elapsed, the breaker short-circuits to [] WITHOUT
    attempting the inner call (so a hard-down Readarr can't stall the run)."""
    calls = {"n": 0}

    class _Counting:
        app = "readarr"

        def get_wanted(self):
            calls["n"] += 1
            raise RuntimeError("down")

    # Large reset_after -> cooldown never elapses within the test.
    breaker = CircuitBreaker(_Counting(), fail_threshold=2, reset_after=10_000.0)
    breaker.get_wanted()   # failure 1 (inner called)
    breaker.get_wanted()   # failure 2 -> open (inner called)
    attempts_at_open = calls["n"]
    assert attempts_at_open == 2

    # Further calls while cooling down must NOT touch the inner adapter.
    for _ in range(3):
        assert breaker.get_wanted() == []
    assert calls["n"] == attempts_at_open   # inner never attempted during cooldown
