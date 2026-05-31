"""REL-02 — the infra-vs-genuine classifier seam (RESEARCH open question A1), Wave 0 scaffold.

Created RED in 05-02 and landed GREEN in 05-02 Task 4: acquire.py gains the `INFRA_EXC`
classification (the five httpx connection/timeout exception types) plus a contained `_safe_call`
adjustment so an INFRA-class fault on the decision-input fetch (get_manifest/get_quality_profile)
RE-RAISES (classifiable as infra → no burned attempt) while a genuine not-found (None return)
still maps to None → stuck. The scheduler + reconcile (Waves 1/2) import the SAME INFRA_EXC.

The KEY distinction (Pitfall 2): a VPN flap / *arr outage must NOT silently become `stuck` and
push an available album toward permanently-unavailable. acquire_item's genuine flow is unchanged
(test_acquire.py stays fully green).

acquire.py is core — httpx exception TYPES are neutral library types (not *arr/slskd wire keys),
so the firewall holds (test_adapter_protocol.py stays clean).
"""
import httpx
import pytest

from core import acquire


# --- INFRA_EXC is defined once, importable, and contains the five connection/timeout types ------

def _infra_types():
    """Resolve the INFRA_EXC classification whether it is exposed as a tuple or an accessor."""
    infra = acquire.INFRA_EXC
    if callable(infra):
        infra = infra()
    return tuple(infra)


def test_infra_exc_contains_the_five_httpx_connection_timeout_types():
    """REL-02: INFRA_EXC classifies the connection/timeout family so the caller can treat an outage
    as infra (no burned attempt), distinct from a genuine acquisition failure."""
    types = _infra_types()
    for exc in (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.PoolTimeout,
        httpx.RemoteProtocolError,
    ):
        assert exc in types, f"{exc.__name__} must be classified as infra"


def test_infra_exc_does_not_classify_a_generic_error_as_infra():
    """A generic ValueError/RuntimeError is NOT infra — only the connection/timeout family is."""
    types = _infra_types()
    assert ValueError not in types
    assert RuntimeError not in types


# --- _safe_call: infra fault RE-RAISES; genuine not-found -> None -------------------------------

def test_safe_call_reraises_infra_fault():
    """An INFRA-class fault on a decision-input fetch RE-RAISES (so the caller classifies it as
    infra and burns NO attempt), rather than silently collapsing to None -> stuck."""
    def _boom(arg):
        raise httpx.ConnectError("slskd/arr unreachable")

    with pytest.raises(httpx.ConnectError):
        acquire._safe_call(_boom, "any-arg")


def test_safe_call_reraises_read_timeout():
    """A read-timeout (the world is slow/unreachable) is also infra -> re-raised, not None."""
    def _slow(arg):
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(httpx.ReadTimeout):
        acquire._safe_call(_slow, "x")


def test_safe_call_returns_none_on_genuine_none():
    """A genuine None return (the adapter found nothing) still maps to None -> the caller marks the
    gap stuck (UNCHANGED behavior — the genuine not-found path is preserved)."""
    assert acquire._safe_call(lambda arg: None, "x") is None


def test_safe_call_passes_through_a_real_value():
    """A successful fetch returns its value unchanged."""
    sentinel = object()
    assert acquire._safe_call(lambda arg: sentinel, "x") is sentinel


def test_safe_call_non_infra_exception_maps_to_none():
    """A NON-infra exception (a genuine 'not found' that raises, e.g. a value/runtime error) is
    treated as a genuine unavailable -> None -> stuck, NOT re-raised as infra. Only the
    connection/timeout family is infra (a 404-style absence is genuine)."""
    def _genuine_absent(arg):
        raise RuntimeError("no such album / parse error")

    assert acquire._safe_call(_genuine_absent, "x") is None
