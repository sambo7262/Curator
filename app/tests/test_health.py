"""Phase-1 unit coverage for the Curator FastAPI stub."""
import os

from fastapi.testclient import TestClient

import main
from main import app

client = TestClient(app)


def test_healthz_returns_ok():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["phase"] == 2                      # IN-01: reflects the running phase, not stale 1
    assert body["version"] == "0.2.0-phase2"


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


def test_startup_retains_and_shutdown_closes_db(tmp_path, monkeypatch):
    """BL-02: startup opens ONE connection, migrates on it, and retains it on app.state.db
    (the single-writer connection); shutdown closes it. Using TestClient as a context manager
    fires the startup/shutdown events."""
    from config import Settings

    db_file = str(tmp_path / "lifecycle.sqlite")
    # main.py binds the module-level `settings`; rebind it to a Settings pointing at a temp DB
    # (frozen dataclass -> build a new instance rather than mutate).
    monkeypatch.setattr(main, "settings", Settings(db_path=db_file), raising=True)

    with TestClient(app) as c:
        c.get("/healthz")
        conn = main.app.state.db
        assert conn is not None
        # the retained connection is the one migrations ran on: user_version is bumped
        assert conn.execute("PRAGMA user_version;").fetchone()[0] >= 1
        # and it is usable as the live writer connection
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    # after shutdown the connection is closed and cleared
    assert main.app.state.db is None
