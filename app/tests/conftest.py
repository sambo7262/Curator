"""Shared Phase-2 pytest fixtures: temp SQLite path, JSON fixture loader, and an
offline httpx mock client so adapter/ledger tests run with NO live Lidarr/Readarr.

The dev sandbox is Python 3.9 + offline (RESEARCH "Environment Availability"); the real
pytest run is Python 3.12 at CI/NAS. These fixtures make every Phase-2 behavior provable
against the recorded JSON in app/tests/fixtures/ — no network is referenced anywhere.
"""
import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def tmp_db_path(tmp_path):
    """A throwaway SQLite file path under pytest's tmp_path.

    Returned as a string (sqlite3.connect signature). The file does not exist yet —
    state.db.connect()/run_migrations() create it, which lets tests reconnect to the
    same path to prove restart-durability (STATE-01, criterion 1).
    """
    return str(tmp_path / "curator-test.sqlite")


@pytest.fixture
def load_fixture():
    """Loader: name (without .json) -> parsed dict from app/tests/fixtures/.

    Example: load_fixture("lidarr_missing") -> {"page": 1, ..., "records": [...]}.
    """

    def _load(name: str) -> dict:
        path = FIXTURES_DIR / f"{name}.json"
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    return _load


@pytest.fixture
def httpx_client(load_fixture):
    """Factory -> offline httpx.Client serving recorded *arr envelopes.

    Uses httpx.MockTransport (ships with httpx — no respx import needed, so adapter tests
    stay green even if respx is absent). Call the factory mapping a route suffix to a
    fixture name, e.g.::

        client = httpx_client({
            "wanted/missing": "lidarr_missing",
            "wanted/cutoff": ["lidarr_cutoff", "lidarr_cutoff_page2"],  # paged: page N -> Nth fixture
        })

    Routes are matched by the request path ending in "/api/v1/<suffix>". A list value
    serves a different fixture per `page` query param (1-indexed) so the adapter's paging
    loop can be exercised end-to-end. Unmapped paths return 404.
    """
    import httpx  # imported lazily so conftest still imports where httpx is absent (offline sandbox)

    def _factory(routes: dict) -> "httpx.Client":
        def _handler(request: "httpx.Request") -> "httpx.Response":
            for suffix, fixture in routes.items():
                if request.url.path.endswith(f"/api/v1/{suffix}"):
                    if isinstance(fixture, (list, tuple)):
                        page = int(request.url.params.get("page", "1"))
                        idx = min(page, len(fixture)) - 1
                        body = load_fixture(fixture[idx])
                    else:
                        body = load_fixture(fixture)
                    return httpx.Response(200, json=body)
            return httpx.Response(404, json={"error": "no route", "path": request.url.path})

        return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")

    return _factory
