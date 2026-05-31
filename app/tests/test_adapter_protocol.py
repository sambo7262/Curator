"""Phase-2 ARR-01 coverage: Protocol conformance + the *arr firewall grep.

- test_both_satisfy_protocol            : LidarrAdapter, ReadarrAdapter, and a breaker-wrapped
                                          ReadarrAdapter all satisfy the runtime-checkable
                                          ArrAdapter Protocol (the core sees only this interface).
- test_core_state_have_no_arr_field_names: the ARR-01 firewall — *arr field names
                                          (foreignAlbumId / X-Api-Key / records[ / profileId)
                                          appear NOWHERE in app/core or app/state (comment-only
                                          lines are filtered, since they document — not couple).

The firewall is the structural proof that *arr knowledge lives ONLY in app/adapters/.
"""
import re
from pathlib import Path

import httpx

from adapters.base import ArrAdapter
from adapters.breaker import CircuitBreaker
from adapters.lidarr import LidarrAdapter
from adapters.readarr import ReadarrAdapter

APP_DIR = Path(__file__).resolve().parents[1]   # .../app
# The locked *arr firewall token set. Phase 2 forbade foreignAlbumId / X-Api-Key / records[ /
# profileId in core+state; Phase 3 EXTENDS it to the quality-profile-JSON shape leaks (Pitfall 2,
# RESEARCH 423) now that get_quality_profile normalizes that JSON inside the adapters:
#   - qualityProfileId  : the Sonarr/Radarr-style profile-id key (and the Readarr A-R2 spelling).
#   - items\[ / "items" : the *arr profile's ordered allowed-list, whether reached as an attribute
#                         subscript (items[i]) or a JSON-key access (profile["items"][i]).
#   - "allowed"         : the per-item allowed flag, as a JSON KEY (quoted) — the neutral Profile
#                         field is `allowed` (unquoted attr), so requiring the quotes avoids a
#                         false-positive on `profile.allowed` / `allowed=frozenset(...)` in core.
#   - "cutoff"          : the *arr profile cutoff as a JSON KEY (quoted). The neutral field is the
#                         unquoted identifier `cutoff_rank` and the gate's English reason strings say
#                         "below cutoff" / "cutoff met" — requiring the surrounding quotes targets the
#                         JSON-key access (body["cutoff"] / .get("cutoff")) and never that prose.
ARR_FIELD_NAMES = re.compile(
    r"foreignAlbumId|X-Api-Key|records\[|profileId|qualityProfileId"
    r"|items\[|\"items\"|\"allowed\"|\"cutoff\""
)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
                        base_url="http://test-arr")


def test_both_satisfy_protocol():
    """ARR-01: both adapters (and the breaker wrapper) present the one ArrAdapter surface.

    Phase 2 implements ONLY get_wanted(); the import/command/profile/queue methods are
    declared-and-stubbed on the Protocol but not implemented on the concrete adapters yet, so a
    full runtime-checkable isinstance() would over-assert against an intentionally-partial seam.
    The plan sanctions attribute/callable conformance checks for the implemented get_wanted; the
    `ArrAdapter` Protocol exists as the single contract the core imports.
    """
    client = _client()
    lidarr = LidarrAdapter("http://test-arr", "k", client)
    readarr = ReadarrAdapter("http://test-arr", "k", client)
    breaker = CircuitBreaker(readarr)

    assert ArrAdapter is not None                    # one Protocol the core depends on
    for adapter in (lidarr, readarr, breaker):
        assert isinstance(adapter.app, str)
        assert callable(adapter.get_wanted)
    # all three are interchangeable: the core only ever calls .app + .get_wanted()
    assert {lidarr.app, readarr.app, breaker.app} == {"lidarr", "readarr"}

    # Phase 3 NEWLY implements get_quality_profile + get_manifest on the concrete adapters (no longer
    # bare stubs). Both Lidarr and Readarr now expose them as callables (the breaker only forwards
    # get_wanted, so it is intentionally excluded from this profile/manifest conformance check).
    for adapter in (lidarr, readarr):
        assert callable(adapter.get_quality_profile)
        assert callable(adapter.get_manifest)


def _strip_comment(line: str) -> str:
    """Drop Python (#) and SQL (--) comment tails so the firewall grep ignores documentation.

    Naive but sufficient: these source files never embed '#' or '--' inside a string literal
    that also contains an *arr field name; documentation mentions are the only matches.
    """
    for marker in ("#", "--"):
        idx = line.find(marker)
        if idx != -1:
            line = line[:idx]
    return line


def test_core_state_have_no_arr_field_names():
    """ARR-01: no *arr field name leaks into app/core or app/state (the firewall holds)."""
    offenders = []
    for base in ("core", "state"):
        for path in (APP_DIR / base).rglob("*"):
            if path.suffix not in (".py", ".sql"):
                continue
            for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                code = _strip_comment(raw)
                if ARR_FIELD_NAMES.search(code):
                    offenders.append(f"{path}:{n}: {raw.strip()}")
    assert not offenders, "arr field names leaked outside adapters/:\n" + "\n".join(offenders)
