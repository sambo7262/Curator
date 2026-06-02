"""Phase-5 (05-05) coverage for the REL-03 status surface + the scheduler lifecycle wiring.

Task 1 (this file's first block) proves:
  - GET /status.json returns the four buckets + counts + shares_ok + throughput from the ledger.
  - GET /status returns 200 text/html listing the item titles.
  - A `<script>`/`<img onerror>` title is rendered ESCAPED (raw tag absent, escaped form present) —
    the T-05-20 XSS/HTML-injection defense.
  - render_status_html is unit-tested directly on a hand-built snapshot dict (no app needed).

Task 2 APPENDS a DISTINCT `test_scheduler_lifecycle` function (do not fold it into the route tests).

Offline only: a temp-sqlite app.state.db is seeded directly (no network); these run on the Python
3.9 + offline sandbox as well as CI/NAS 3.12.
"""
import main
from fastapi.testclient import TestClient

from config import Settings
from core.status_page import render_status_html


def _seed_rows(conn):
    """Seed one stuck, one quarantined, one permanently-unavailable, and one imported row.

    Writes directly via SQL so the test is independent of the adapter/repo write path; uses the
    neutral ledger columns only.
    """
    base_cols = (
        "arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id, "
        "quality_profile_id, status, discovered_at, last_seen_at, raw_json"
    )
    now = "2026-05-31T00:00:00Z"
    rows = [
        ("lidarr", "10", "album", "missing", "Stuck Album", "Artist A", "mbid-10", 1,
         "stuck", "2020-01-01T00:00:00Z", now, "{}"),
        ("lidarr", "11", "album", "missing", "Quarantined Album", "Artist B", "mbid-11", 1,
         "quarantined", "2020-01-01T00:00:00Z", now, "{}"),
        ("lidarr", "12", "album", "missing", "Gone Forever", "Artist C", "mbid-12", 1,
         "permanently-unavailable", "2020-01-01T00:00:00Z", now, "{}"),
        ("lidarr", "13", "album", "missing", "Imported Album", "Artist D", "mbid-13", 1,
         "imported", "2020-01-01T00:00:00Z", now, "{}"),
    ]
    for r in rows:
        conn.execute(
            f"INSERT INTO items ({base_cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", r
        )
    conn.commit()


def test_status_json_returns_buckets_counts_shares_throughput(tmp_path, monkeypatch):
    """GET /status.json exposes the four buckets + counts + shares_ok + throughput."""
    monkeypatch.setattr(
        main, "settings", Settings(db_path=str(tmp_path / "status.sqlite")), raising=True
    )
    with TestClient(main.app) as c:
        _seed_rows(main.app.state.db)
        resp = c.get("/status.json")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "counts", "stuck", "quarantined", "permanently_unavailable",
            "shares_ok", "throughput",
        }
        # counts groups by status across the ledger.
        assert body["counts"]["stuck"] == 1
        assert body["counts"]["quarantined"] == 1
        assert body["counts"]["permanently-unavailable"] == 1
        assert body["counts"]["imported"] == 1
        # the three issue buckets each carry exactly their one neutral row view.
        assert [i["title"] for i in body["stuck"]] == ["Stuck Album"]
        assert [i["title"] for i in body["quarantined"]] == ["Quarantined Album"]
        assert [i["title"] for i in body["permanently_unavailable"]] == ["Gone Forever"]
        # neutral fields only — no raw *arr record leaks into the view.
        assert set(body["stuck"][0]) == {"app", "id", "title", "reason"}
        # shares_ok defaults True (the startup wiring sets it); throughput is an int.
        assert body["shares_ok"] is True
        assert isinstance(body["throughput"], int)


def test_status_html_lists_item_titles(tmp_path, monkeypatch):
    """GET /status returns 200 text/html and lists the stuck/quarantined/permanent item titles."""
    monkeypatch.setattr(
        main, "settings", Settings(db_path=str(tmp_path / "status_html.sqlite")), raising=True
    )
    with TestClient(main.app) as c:
        _seed_rows(main.app.state.db)
        resp = c.get("/status")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        html = resp.text
        assert "Curator status" in html
        assert "Stuck Album" in html
        assert "Quarantined Album" in html
        assert "Gone Forever" in html


def test_status_html_escapes_malicious_title(tmp_path, monkeypatch):
    """T-05-20: a title carrying markup is rendered ESCAPED — the raw tag is absent, the escaped
    form present. Proves the XSS/HTML-injection defense on /status."""
    monkeypatch.setattr(
        main, "settings", Settings(db_path=str(tmp_path / "status_xss.sqlite")), raising=True
    )
    payload = '<img src=x onerror=alert(1)><script>alert(2)</script>'
    with TestClient(main.app) as c:
        conn = main.app.state.db
        conn.execute(
            "INSERT INTO items (arr_app, arr_id, kind, gap_type, title, artist_or_author, "
            "foreign_id, quality_profile_id, status, discovered_at, last_seen_at, raw_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("lidarr", "99", "album", "missing", payload, "Artist", "mbid-99", 1,
             "stuck", "2020-01-01T00:00:00Z", "2026-05-31T00:00:00Z", "{}"),
        )
        conn.commit()
        resp = c.get("/status")
        assert resp.status_code == 200
        html = resp.text
        # The raw live-markup tags must NOT appear.
        assert "<img src=x onerror=alert(1)>" not in html
        assert "<script>alert(2)</script>" not in html
        # The escaped forms MUST appear.
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
        assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html


def test_render_status_html_pure_function_on_handbuilt_snapshot():
    """render_status_html is a pure transform over a snapshot dict (no app/connection needed)."""
    snapshot = {
        "counts": {"stuck": 2, "imported": 5},
        "stuck": [{"app": "lidarr", "id": "1", "title": "A Title", "reason": "stuck"}],
        "quarantined": [],
        "permanently_unavailable": [
            {"app": "readarr", "id": "7", "title": "Book", "reason": "permanently-unavailable"}
        ],
        "shares_ok": False,
        "throughput": 5,
    }
    html = render_status_html(snapshot)
    assert "Curator status" in html
    assert "A Title" in html
    assert "Book" in html
    # shares_ok False surfaces the attention banner; throughput shows.
    assert "ATTENTION" in html
    assert "Imported in the last 24h: 5" in html
    # a malicious value handed directly to the pure function is escaped too.
    evil = render_status_html(
        {"counts": {}, "stuck": [{"app": "x", "id": "y", "title": "<b>z</b>", "reason": None}],
         "quarantined": [], "permanently_unavailable": [], "shares_ok": True, "throughput": 0}
    )
    assert "<b>z</b>" not in evil
    assert "&lt;b&gt;z&lt;/b&gt;" in evil


def test_status_page_renders_reset_button():
    """The status page carries the manual reset button wired to POST /reset (owner 2026-06)."""
    html = render_status_html(
        {"counts": {}, "stuck": [], "quarantined": [], "permanently_unavailable": [],
         "shares_ok": True, "throughput": 0}
    )
    assert "/reset" in html
    assert "<button" in html
    assert "confirm(" in html, "the reset button must guard with a confirm dialog"


def test_reset_endpoint_rearms_stuck_and_quarantined(tmp_path, monkeypatch):
    """POST /reset re-arms every stuck/quarantined/permanently-unavailable row back to pending with a
    clean attempt slate (same as a teardown rebuild's boot re-arm), and reports the count. An imported
    row is untouched."""
    monkeypatch.setattr(
        main, "settings", Settings(db_path=str(tmp_path / "reset.sqlite")), raising=True
    )
    with TestClient(main.app) as c:
        conn = main.app.state.db
        _seed_rows(conn)  # 1 stuck + 1 quarantined + 1 permanently-unavailable + 1 imported
        # Give them a stale backoff so we can prove it is cleared.
        conn.execute("UPDATE items SET attempt_count = 2, next_attempt_at = '2099-01-01T00:00:00Z'"
                     " WHERE status != 'imported'")
        conn.commit()

        resp = c.post("/reset")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "rearmed": 3}

        pend = conn.execute(
            "SELECT COUNT(*) FROM items WHERE status='pending' AND attempt_count=0"
            " AND next_attempt_at IS NULL"
        ).fetchone()[0]
        assert pend == 3, "all three re-armed to a clean pending slate"
        assert conn.execute("SELECT status FROM items WHERE arr_id='13'").fetchone()[0] == "imported"


# ---------------------------------------------------------------------------
# Task 2 (05-05): a DISTINCT lifecycle test — startup wires the scheduler + shares_ok and reconcile,
# shutdown stops the scheduler cleanly, WITHOUT a live acquisition cycle firing.
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """Offline ArrAdapter: no get_wanted rows, no orphans to verify — reconcile is a clean no-op."""

    app = "lidarr"

    def get_wanted(self):
        return []

    def verify_imported(self, item):  # pragma: no cover - no orphans seeded
        return False


def test_scheduler_lifecycle(tmp_path, monkeypatch):
    """REL-01/REL-02 wiring: TestClient startup builds app.state.scheduler + app.state.shares_ok and
    runs reconcile_on_startup without raising; shutdown stops the scheduler cleanly. The ACQ_ENABLED
    kill-switch is OFF for the test so the daemon's boot cycle short-circuits — this proves the WIRING,
    not a live acquisition (no slskd/*arr is touched). build_adapters is monkeypatched to an offline
    fake so neither reconcile nor any cycle reaches the network."""
    monkeypatch.setattr(
        main, "settings", Settings(db_path=str(tmp_path / "lifecycle.sqlite")), raising=True
    )
    # Kill-switch OFF: the scheduler's boot cycle re-reads Settings.from_env() and skips the cycle,
    # so no live acquisition fires during the test. A long poll interval is a belt-and-braces guard.
    monkeypatch.setenv("ACQ_ENABLED", "false")
    monkeypatch.setenv("ACQ_POLL_INTERVAL_SECONDS", "3600")
    # Offline adapters for reconcile_on_startup (no rows -> clean no-op) and any detect path.
    monkeypatch.setattr(
        "core.gap_detector.build_adapters",
        lambda: ([_FakeAdapter()], []),
        raising=True,
    )

    with TestClient(main.app) as c:
        c.get("/healthz")
        # startup wired the daemon + the shares flag.
        assert main.app.state.scheduler is not None
        assert main.app.state.scheduler._thread.is_alive()
        assert main.app.state.shares_ok is True
        # the existing endpoints still answer.
        assert c.get("/readyz").status_code == 200
        assert c.get("/status.json").status_code == 200

    # after shutdown the scheduler stopped cleanly and the connection closed.
    assert main.app.state.scheduler is None
    assert main.app.state.db is None
