# Curator status page renderer — the REL-03 (D-12) server-rendered HTML view.
#
# This is the core side of the firewall (PITFALL #6): ZERO *arr/slskd wire vocabulary. It is a PURE
# function — `render_status_html(snapshot) -> str` — over the NEUTRAL ledger snapshot dict the
# /status.json route builds (counts + the three issue buckets + shares_ok + throughput). It speaks
# only that neutral dict + stdlib `html.escape`; it never touches an *arr/slskd JSON key, a live
# connection, or a network client. Being pure makes it offline-unit-testable with a hand-built dict.
#
# XSS / HTML-INJECTION DEFENSE (T-05-20, the load-bearing security rule): every ledger string an item
# carries — its title and its reason — ORIGINATES from untrusted peer/*arr data (a Soulseek filename,
# an *arr album/artist name). A malicious or merely odd title like `<img src=x onerror=alert(1)>`
# rendered raw into the page would execute as live markup. EVERY interpolated string therefore passes
# through `html.escape` (which covers & < > " '), exactly as the repo layer binds every value via a `?`
# placeholder. This is the status-page analogue of the SQL-placeholder rule.
#
# INFO-DISCLOSURE NOTE (T-05-21): the page lists the owner's library gaps + titles, so it MUST stay on
# the existing LAN/Tailscale-only firewalled port (:8674). No new exposure, no auth added (it matches
# the existing /healthz//detect posture). The route docstring (main.py) reiterates this.
from html import escape
from typing import Any, Dict, List

# The issue buckets rendered in the table, in display order, with a human label each. The keys MUST
# match the /status.json contract (the Phase-6 widget reads the same JSON).
_BUCKETS = (
    ("stuck", "Stuck"),
    ("quarantined", "Quarantined"),
    ("permanently_unavailable", "Permanently unavailable"),
)


def _e(value: Any) -> str:
    """Escape any value as HTML-safe text (None -> empty). The single XSS chokepoint (T-05-20)."""
    if value is None:
        return ""
    return escape(str(value))


def render_status_html(snapshot: Dict[str, Any]) -> str:
    """Render the neutral ledger snapshot dict into a bare server-rendered HTML status page (REL-03).

    `snapshot` is the /status.json shape:
        {
          "counts": {status: int, ...},
          "stuck":   [{"app","id","title","reason"}, ...],
          "quarantined": [...],
          "permanently_unavailable": [...],
          "shares_ok": bool,
          "throughput": int,    # imported in the last 24h
        }

    A pure transform — no app, no connection, no network. EVERY interpolated ledger string (title,
    reason, app, id) passes through `html.escape` (T-05-20). No JS, no template engine (stdlib only).
    """
    counts: Dict[str, Any] = snapshot.get("counts") or {}
    shares_ok = bool(snapshot.get("shares_ok"))
    throughput = snapshot.get("throughput") or 0

    # Header: the per-status counts + the shares health + the healthy-throughput number.
    counts_cells = "".join(
        f"<li>{_e(status)}: {_e(count)}</li>" for status, count in sorted(counts.items())
    )
    shares_label = "OK" if shares_ok else "ATTENTION — shared-file count is 0"

    # Issue table: stuck -> quarantined -> permanently-unavailable, each row escaped.
    body_rows: List[str] = []
    for key, label in _BUCKETS:
        items = snapshot.get(key) or []
        for item in items:
            app = _e(item.get("app"))
            ident = _e(item.get("id"))
            title = _e(item.get("title"))
            reason = _e(item.get("reason"))
            body_rows.append(
                f"<tr><td>{_e(label)}</td><td>{app}:{ident}</td>"
                f"<td>{title}</td><td>{reason}</td></tr>"
            )
    if body_rows:
        table = (
            "<table border=\"1\" cellpadding=\"4\">"
            "<thead><tr><th>State</th><th>Item</th><th>Title</th><th>Reason</th></tr></thead>"
            "<tbody>" + "".join(body_rows) + "</tbody></table>"
        )
    else:
        table = "<p>No stuck, quarantined, or permanently-unavailable items.</p>"

    return (
        "<!DOCTYPE html>"
        "<html><head><meta charset=\"utf-8\"><title>Curator status</title></head>"
        "<body>"
        "<h1>Curator status</h1>"
        f"<p>Shares: {_e(shares_label)}</p>"
        f"<p>Imported in the last 24h: {_e(throughput)}</p>"
        f"<h2>Counts</h2><ul>{counts_cells}</ul>"
        "<h2>Items needing attention</h2>"
        f"{table}"
        "</body></html>"
    )
