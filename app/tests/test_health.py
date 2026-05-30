"""Phase-1 unit coverage for the Curator FastAPI stub."""
import os

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_healthz_returns_ok():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "phase": 1}


def test_readyz_shape():
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data_mount_present", "data_readable", "slskd_url"}
    assert isinstance(body["data_mount_present"], bool)
    assert isinstance(body["data_readable"], bool)
    # slskd_url is str when SLSKD_URL is set, else None — not dependent on a real /data.
    assert body["slskd_url"] is None or isinstance(body["slskd_url"], str)


def test_readyz_reflects_slskd_url_env(monkeypatch):
    monkeypatch.setenv("SLSKD_URL", "http://gluetun:5030")
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["slskd_url"] == "http://gluetun:5030"
