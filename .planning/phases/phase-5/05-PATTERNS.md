# Phase 5: Autonomy, Sharing & Self-Recovery - Pattern Map

**Mapped:** 2026-05-31
**Files analyzed:** 13 (8 new, 5 modified)
**Analogs found:** 13 / 13 (every new file has a strong in-repo analog — Phase 5 is composition over Phases 2-4)

## File Classification

| New/Modified File | New/Mod | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|---------|------|-----------|----------------|---------------|
| `app/core/scheduler.py` | NEW | service (daemon orchestrator) | event-driven (poll loop) + batch dispatch | `app/core/gap_detector.py` (`detect_gaps` composition) + `app/main.py` (`_detect_lock`, on_event lifecycle) + `app/core/acquire.py` (clock/poll seam) | role-match (no existing daemon; pattern fully present across 3 files) |
| `app/core/reconcile.py` | NEW | service (startup recovery) | transform (ledger state reset + verify) | `app/core/gap_detector.py` (`detect_gaps` adapter+ledger loop) + `app/core/acquire.py` `_import_and_verify` (verify_imported + set_status) | role-match |
| `app/core/shares.py` | NEW | service (ensure/self-heal) | request-response (slskd API) | `app/core/acquire.py` (neutral-seam orchestration over a slskd client) | role-match |
| `app/state/migration_0003.sql` | NEW | migration | DDL table-rebuild | `app/state/migration_0002.sql` | **exact** (same table-rebuild + enum-widen technique) |
| `app/state/repo.py` (additions) | MOD | model (DAO) | CRUD + eligibility query | existing `set_status` / `list_by_status` / `record_staged_file` in same file | **exact** (same file, same idioms) |
| `app/adapters/slskd.py` (additions) | MOD | adapter (client) | request-response | existing `SlskdClient` methods (`search`, `transfer`) in same file | **exact** (same class, same `.get()` posture) |
| `app/adapters/lidarr.py` (`get_queue_status`) | MOD | adapter (client) | request-response | `verify_imported` (lidarr.py:330) + `_paged` queue read idiom | **exact** (same file) |
| `app/adapters/readarr.py` (`get_queue_status`) | MOD | adapter (client) | request-response | `app/adapters/lidarr.py` `get_queue_status` (best-effort mirror) | role-match (best-effort degrade) |
| `app/config.py` (Phase-5 tunables) | MOD | config | n/a | existing Phase-4 tunable block + `from_env()` casts | **exact** (same file) |
| `app/state/db.py` (register 0003) | MOD | config (migration registry) | n/a | existing `MIGRATIONS` list + `_SCHEMA_0002` loader | **exact** (same file) |
| `app/main.py` (scheduler lifecycle + `/status` + `/status.json`) | MOD | route + provider | request-response (HTML/JSON) + lifecycle | existing `on_event` startup/shutdown + `POST /detect` 409 guard + `/readyz` JSON | **exact** (same file) |
| `app/core/status_page.py` (optional HTML renderer) | NEW | utility (view) | transform (ledger → HTML) | n/a in repo — stdlib `html.escape` + f-string (RESEARCH Pattern 7) | no analog (see No Analog Found) |
| `app/core/gap_detector.py` (`detect_gaps` batch txn, D-15) | MOD | service | batch (single-txn upserts) | existing `detect_gaps` + `db.py` `run_migrations` BEGIN/COMMIT idiom | **exact** (same loop, wrap in txn) |

---

## Pattern Assignments

### `app/core/scheduler.py` (NEW — service, event-driven poll loop + bounded dispatch)

**Analogs:** `app/main.py` (lifecycle + lock), `app/core/gap_detector.py` (composition + lazy `build_adapters`), `app/core/acquire.py` (injectable clock/poll seam).

**Firewall:** NEW core module — MUST stay *arr/slskd-vocabulary-free (the firewall grep at `app/tests/test_adapter_protocol.py:90` auto-scans all of `core/` via `rglob`, so this file is covered the moment it exists). Speak only neutral types + repo + the adapter/client method surface.

**Single-writer lock pattern — reuse the existing primitive** (`app/main.py:16-20`):
```python
# main.py already owns the writer-serialization lock. The scheduler MUST acquire THE SAME lock
# (or a shared writer lock both /detect and the scheduler take) for every ledger write — never a
# second sqlite connection (D-16, Pitfall 4).
_detect_lock = threading.Lock()
```
The `/detect` handler's non-blocking acquire + 409 (`app/main.py:80-82`) is the established collision guard; the scheduler shares this lock so a manual `/detect` and a cycle can never write concurrently.

**Lazy adapter/client construction + caller-owns-close** (copy from `app/core/gap_detector.py:42-79` and `app/core/acquire.py:326-338`):
```python
# Build live adapters/clients per cycle, close every httpx.Client in finally (CR-02).
# Import httpx lazily so the module parses in the offline 3.9 sandbox.
adapters, clients = build_adapters()         # gap_detector.build_adapters (Lidarr + breaker-Readarr)
slskd, slskd_clients = build_acquire_clients(settings)  # acquire.build_acquire_clients
try:
    ...
finally:
    for c in clients + slskd_clients:
        c.close()
```

**Injectable clock / stop-event seam** (mirror `acquire.py`'s `now`/`poll_hook` seams, lines 220-242, so the 6h loop is offline-provable without sleeping). RESEARCH Pattern 1 is the canonical shape:
```python
# Source: app/main.py _detect_lock + on_event + acquire.py poll_hook seam; RESEARCH 205-241.
class Scheduler:
    def __init__(self, app, settings):
        self._stop = threading.Event()      # interruptible sleep (clean shutdown, no busy-wait)
        self._thread = None
    def start(self):
        self._thread = threading.Thread(target=self._run, name="curator-scheduler", daemon=True)
        self._thread.start()
    def stop(self):
        self._stop.set(); self._thread and self._thread.join(timeout=30)
    def _run(self):
        try:
            run_cycle(self._app, self._settings, first_pass=True)
        except Exception:
            log.exception("scheduler boot cycle failed (will retry)")     # NEVER kill the daemon
        while not self._stop.wait(self._settings.acq_poll_interval_seconds):
            if not self._settings.acq_enabled:        # D-05 kill-switch
                continue
            try:
                run_cycle(self._app, self._settings)
            except Exception:
                log.exception("scheduler cycle failed (loop continues)")   # REL-01 Pitfall 5
```

**Bounded concurrency — `ThreadPoolExecutor(max_workers=MAX_CONCURRENT)`** with a lock-guarding connection proxy so `acquire_item`'s existing `conn.execute(...)` calls are serialized for free (RESEARCH Pattern 2, Shape B; `db.py:35` already sets `check_same_thread=False`):
```python
# Workers do IO only; one connection writes, serialized by the writer lock (Pitfall 4 / D-16).
with ThreadPoolExecutor(max_workers=settings.max_concurrent) as pool:
    futures = {pool.submit(run_one, item, adapter, slskd, LockedConn(conn, writer_lock), settings): item
               for item in batch}
    for fut in as_completed(futures):
        apply_result(conn, writer_lock, futures[fut], fut.result(), settings)
```
**Anti-pattern (forbidden, D-16):** one sqlite connection per worker → `database is locked` + lost attempt-counter updates.

**Neutral-log identity** (copy `acquire.py:209-211` `_identity` — app+id only, never keys/tokens, T-04-16).

---

### `app/core/reconcile.py` (NEW — service, startup state-reset + verify-by-requery)

**Analogs:** `app/core/gap_detector.py:23-39` (adapter+ledger loop), `app/core/acquire.py:189` (`verify_imported` then `set_status`), `app/state/repo.py:90` (`list_by_status`).

**Firewall:** NEW core module — neutral types only (auto-covered by the firewall grep).

**Core pattern — reset orphaned in-flight rows with a no-double-import guard** (RESEARCH 528-552, composing existing `repo.list_by_status` + `adapter.verify_imported` (lidarr.py:330) + `repo.set_status`):
```python
# Source: repo.list_by_status + adapter.verify_imported (lidarr.py:330) + repo.set_status; D-14.
for status in ("downloading", "importing"):
    for row in repo.list_by_status(conn, status):       # the orphaned in-flight rows
        adapter = by_app.get(row["arr_app"])
        item = _gapitem_from_row(row)
        try:
            imported = adapter.verify_imported(item)     # did it actually land while we were down?
        except INFRA_EXC:
            continue                                     # *arr down -> leave as-is, no burn (REL-02)
        with lock:
            if imported:
                repo.set_status(conn, item.arr_app, item.arr_id, "imported")   # don't re-import (Pitfall 3)
            else:
                repo.set_status(conn, item.arr_app, item.arr_id, "pending")    # reset clean, NO attempt++
```
**Key rule (D-14):** an interrupted in-flight item that did NOT import is reset to `pending` WITHOUT incrementing `attempt_count` (the interruption was infra, not a genuine fail). Reuse `acquire.build_acquire_clients` / `gap_detector.build_adapters` for construction + `finally`-close.

**Infra-vs-genuine classifier** (RESEARCH Pattern 5, lines 340-359) — define once, reuse in scheduler + reconcile:
```python
import httpx
INFRA_EXC = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
             httpx.PoolTimeout, httpx.RemoteProtocolError)
```
**OPEN QUESTION A1 (RESEARCH 655, 662):** `acquire.py:315-323` `_safe_call` currently swallows a decision-input fetch failure to `None`→`stuck`. On an *arr outage this would wrongly burn an attempt. The planner must make an INFRA-class fault on the `get_manifest`/`get_quality_profile` fetch distinguishable from a genuine "not found" (a small, contained `_safe_call` adjustment + `test_infra_classify.py`).

---

### `app/core/shares.py` (NEW — service, slskd ensure/self-heal)

**Analog:** `app/core/acquire.py` (neutral orchestration over a slskd client; never reads a wire key in core).

**Firewall:** the `shares.files` wire key + the rescan call live in `app/adapters/slskd.py` (below); `shares.py` consumes only the neutral `int` count + `bool` rescan result.

**Ensure/self-heal cycle** (RESEARCH 437, D-10, SHARE-01/02):
```python
# Source: RESEARCH slskd Shares API §437; D-10. Eventually-consistent ACROSS cycles (Pitfall 6).
def ensure_shares(slskd, app_state) -> bool:
    count = slskd.get_shared_file_count()       # neutral int (adapter reads shares.files)
    if count > 0:
        app_state.shares_ok = True
        return True
    slskd.rescan_shares()                        # PUT /api/v0/shares (204 started / 409 already scanning)
    # do NOT re-read in the same cycle (rescan is async) — surface a "share" issue only if
    # still 0 on a LATER cycle after a rescan window (SHARE-02 "surface if it can't recover").
    app_state.shares_ok = False
    return False
```
**Forbidden (D-10, anti-pattern):** rewriting slskd.yml. Curator only reads the count + triggers a rescan + surfaces.

---

### `app/state/migration_0003.sql` (NEW — migration, table-rebuild)

**Analog:** `app/state/migration_0002.sql` — **exact** technique (rename → rebuild with widened CHECK → `INSERT ... SELECT` → drop → recreate indexes).

**Runner contract (copy verbatim from migration_0002.sql header):** the runner (`db.py:49-65` `_split_statements`) strips `--` comments then splits on top-level `;` — so **NO `;` inside any string literal**. The whole migration runs inside one BEGIN/COMMIT (`db.py:84-90`).

**Column adds + enum widen + indexes** (RESEARCH Pattern 3, lines 274-301). CRITICAL ordering caveat (RESEARCH 302): do ONE rebuild that defines the full column set, and use an **explicit column-list** `INSERT INTO items (col list) SELECT col list FROM items_old` (safer than `SELECT *` once shapes diverge mid-migration — diverges from 0002's positional `SELECT *`):
```sql
-- (1) Rebuild items with the three new columns + the widened status CHECK (adds 'permanently-unavailable').
ALTER TABLE items RENAME TO items_old;
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  arr_app TEXT NOT NULL, arr_id TEXT NOT NULL, kind TEXT NOT NULL, gap_type TEXT NOT NULL,
  title TEXT, artist_or_author TEXT, foreign_id TEXT, quality_profile_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','searching','grabbed','downloaded','imported','unavailable',
                      'blacklisted','downloading','importing','quarantined','stuck',
                      'permanently-unavailable')),
  discovered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, raw_json TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_checked_at TEXT,
  UNIQUE (arr_app, arr_id)
);
INSERT INTO items (id, arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id,
                   quality_profile_id, status, discovered_at, last_seen_at, raw_json)
  SELECT id, arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id,
         quality_profile_id, status, discovered_at, last_seen_at, raw_json FROM items_old;
DROP TABLE items_old;
CREATE INDEX IF NOT EXISTS idx_items_status       ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind     ON items(arr_app, kind);
CREATE INDEX IF NOT EXISTS idx_items_next_attempt ON items(next_attempt_at);
```
**Preservation test (mirror 02-02):** migrate a v0002 DB → all ~1,493 rows survive with new columns defaulted (`attempt_count=0`, `next_attempt_at=NULL`, `last_checked_at=NULL`) and the enum accepts `permanently-unavailable` (`tests/test_migration_0003.py`).

**Registration in `app/state/db.py`** (copy the `_SCHEMA_0002` loader + `MIGRATIONS` append, db.py:18-25):
```python
_SCHEMA_0003 = (Path(__file__).parent / "migration_0003.sql").read_text(encoding="utf-8")
MIGRATIONS: List[Tuple[str, str]] = [
    ("0001", _SCHEMA_0001),
    ("0002", _SCHEMA_0002),
    ("0003", _SCHEMA_0003),   # Phase 5: backoff/attempt/last-checked cols + permanently-unavailable
]
```

---

### `app/state/repo.py` (MOD — DAO additions: eligibility select + backoff mutators + status-counts)

**Analog:** the existing `set_status` / `list_by_status` / `record_staged_file` in the same file — **exact** idiom reuse.

**Firewall + security (repo.py:9-10 header rule):** ALL values bound via `?` placeholders, never f-string. Reuse `_now_iso()` (repo.py:17) for every timestamp — the eligibility SQL relies on its Z-suffixed lexicographic-comparable format.

**Eligibility select** (RESEARCH Pattern 4, lines 316-333; `?`-placeholders per the repo header rule):
```python
# Source: repo.py list_by_status idiom + RESEARCH Pattern 4 (GAP-03 grace + D-08 backoff + D-09 dormant).
def select_eligible(conn, grace_cutoff, now, dormant_cutoff, room) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM items
        WHERE (
            status IN ('pending','stuck','quarantined')
            AND discovered_at <= ?                                  -- GAP-03 grace (now - 3d)
            AND (next_attempt_at IS NULL OR next_attempt_at <= ?)   -- D-08 backoff elapsed
        ) OR (
            status = 'permanently-unavailable'
            AND (last_checked_at IS NULL OR last_checked_at <= ?)   -- D-09 30d dormant re-check
        )
        ORDER BY discovered_at ASC                                  -- oldest gaps first (fair drain)
        LIMIT ?
        """,
        (grace_cutoff, now, dormant_cutoff, room),
    ).fetchall()
```
**OPEN QUESTION 2 (RESEARCH 667):** confirm `stuck`/`quarantined` are retry-eligible (research assumes yes — that IS the backoff mechanism). Planner confirms at plan time.

**Backoff/attempt mutators** (copy the `set_status` UPDATE shape, repo.py:84-87; logic per RESEARCH 304-312):
```python
BACKOFF_SECONDS = [3600, 21600, 86400]   # D-08: attempt 1->1h, 2->6h, 3+->24h (capped)
def record_attempt(conn, arr_app, arr_id, attempt_count, next_attempt_at, status):
    conn.execute(
        "UPDATE items SET attempt_count = ?, next_attempt_at = ?, last_checked_at = ?, status = ?"
        " WHERE arr_app = ? AND arr_id = ?",
        (attempt_count, next_attempt_at, _now_iso(), status, arr_app, arr_id),
    )
```
`apply_result` (in scheduler.py) logic: genuine fail → `attempt_count += 1`, `last_checked_at = now`; if `>= acq_max_attempts (3)` → `status='permanently-unavailable'`, `next_attempt_at = now + 30d` (the dormant anchor); else `next_attempt_at = now + backoff_for(attempt_count)`. `imported` → reset `attempt_count=0`. `infra-skip` → NO write.

**Status-counts + throughput for the status page** (copy `list_by_status` shape):
```python
def status_counts(conn) -> dict:        # {status: count} for the /status header
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM items GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
def imported_recent(conn, since_iso) -> int:   # healthy-throughput signal (imported in last 24h)
    return conn.execute(
        "SELECT COUNT(*) FROM items WHERE status = 'imported' AND last_seen_at >= ?", (since_iso,)
    ).fetchone()[0]
```

---

### `app/adapters/slskd.py` (MOD — add `get_shared_file_count` + `rescan_shares`)

**Analog:** the existing `SlskdClient` methods (`search`, `search_state`, `transfer`) in the same file — **exact** idiom (`self._base`/`self._headers`, `.get()`-defensive, `raise_for_status`, key only in `self._headers` never logged, T-04-07).

**Two new methods** (RESEARCH 417-435, SHARE-01/02; `shares.files` count `[VERIFIED]` via maintained Homepage widget, `PUT /api/v0/shares` `[CITED]` slskd source):
```python
# Source: existing SlskdClient.search/transfer posture (slskd.py:109-191); RESEARCH 417-435.
def get_shared_file_count(self) -> int:
    """SHARE-02: GET /api/v0/application -> body['shares']['files'] (.get()-defensive: absent -> 0)."""
    r = self._client.get(f"{self._base}/application", headers=self._headers, timeout=30.0)
    r.raise_for_status()
    body = r.json() if isinstance(r.json(), dict) else {}
    shares = body.get("shares") if isinstance(body.get("shares"), dict) else {}
    files = shares.get("files")
    return files if isinstance(files, int) else 0

def rescan_shares(self) -> bool:
    """SHARE-02 self-heal: PUT /api/v0/shares. 204 -> True (started); 409 -> False (already scanning)."""
    r = self._client.put(f"{self._base}/shares", headers=self._headers, timeout=30.0)
    if r.status_code == 409:
        return False
    r.raise_for_status()
    return True
```
**Networking note (CLAUDE.md / RESEARCH 693):** these use the SAME `self._base` (gluetun-published `settings.slskd_url`, NEVER a container name) and `self._headers`. **A3 live-confirm checkpoint recommended** (one `curl …/application | jq .shares` on the NAS) before relying on the shape.

---

### `app/adapters/lidarr.py` (MOD — `get_queue_status`) and `app/adapters/readarr.py` (MOD — `get_queue_status`)

**Analog:** `verify_imported` (lidarr.py:330-338, the re-query idiom) + the `_paged` queue-read posture (lidarr.py:79). The Protocol stub already exists at `base.py:77`.

**Lidarr (primary — raise surfaces, classified as infra-skip upstream)** (RESEARCH 556-568, D-02/GAP-03):
```python
# Source: lidarr.py verify_imported re-query + _paged GET; *arr keys stay in the adapter (firewall).
def get_queue_status(self, item: "GapItem") -> bool:
    """GAP-03/D-02: True iff an active/queued Usenet grab exists for this item (skip — Usenet wins)."""
    r = self._client.get(f"{self._base}/api/v1/queue", headers=self._headers,
                         params={"page": 1, "pageSize": 100}, timeout=30.0)
    r.raise_for_status()
    j = r.json()
    records = j.get("records", []) if isinstance(j, dict) else []   # 'records' key stays IN the adapter
    return any(str(rec.get("albumId")) == item.arr_id for rec in records if isinstance(rec, dict))
```
**A2 (RESEARCH 656):** confirm the queue record's match field (`albumId` Lidarr / `bookId` Readarr) against live queue JSON (one `curl`).
**Readarr:** mirror as best-effort — degrade to `False` on fault (never gate music; ARR-02), matching `verify_imported`'s Readarr posture.
**Firewall note:** the wire key `records[` is in the `ARR_FIELD_NAMES` grep (`test_adapter_protocol.py:38`) — it MUST stay inside the adapter; `get_queue_status` returns a neutral `bool`.

---

### `app/config.py` (MOD — Phase-5 env tunables)

**Analog:** the Phase-4 acquisition-tunable block (config.py:40-52) + the `from_env()` casts (config.py:64-89) — **exact** pattern. Numerics cast `float()`/`int()` so a bad operator value fails fast at startup; no secrets (D-13 defers Pushover).

```python
# Add to the frozen dataclass (mirror config.py:46-51 defaults block):
acq_enabled: bool = True                       # D-05 kill-switch
acq_dry_run: bool = False                      # D-05 search+gate+log, ZERO side effects
max_concurrent: int = 3                         # D-04 steady-state cap (owner promotes 1 -> 3)
acq_poll_interval_seconds: float = 21600.0      # D-03 6h cycle cadence
acq_grace_seconds: float = 259200.0             # D-01 3-day Usenet-politeness grace
acq_max_attempts: int = 3                       # D-07 give-up threshold
acq_dormant_seconds: float = 2592000.0          # D-09 30-day dormant re-check TTL
# from_env() (mirror config.py:79-88): bools via a truthy parse, ints/floats cast (fail-fast):
acq_enabled=os.getenv("ACQ_ENABLED", "true").lower() not in ("0", "false", "no"),
acq_dry_run=os.getenv("ACQ_DRY_RUN", "false").lower() in ("1", "true", "yes"),
max_concurrent=int(os.getenv("MAX_CONCURRENT", "3")),
acq_poll_interval_seconds=float(os.getenv("ACQ_POLL_INTERVAL_SECONDS", "21600.0")),
acq_grace_seconds=float(os.getenv("ACQ_GRACE_SECONDS", "259200.0")),
acq_max_attempts=int(os.getenv("ACQ_MAX_ATTEMPTS", "3")),
acq_dormant_seconds=float(os.getenv("ACQ_DORMANT_SECONDS", "2592000.0")),
```
**A4 (RESEARCH 658):** to make `ACQ_ENABLED`/`MAX_CONCURRENT` flippable without a restart, the scheduler re-reads them each cycle (re-read the single env var, or rebuild `Settings.from_env()` per cycle). Planner decides; both work.

---

### `app/main.py` (MOD — scheduler lifecycle + `GET /status` + `GET /status.json`)

**Analog:** existing `on_event` startup/shutdown (main.py:23-43), the `/detect` 409-guard + lazy-import pattern (main.py:62-91), `/readyz` JSON shape (main.py:52-59).

**Lifecycle wiring** (extend the existing `_startup`/`_shutdown`, main.py:23-43; RESEARCH 504-526):
```python
# Source: existing main.py on_event startup/shutdown (23-43).
@app.on_event("startup")
def _startup():
    conn = connect(settings.db_path); run_migrations(conn)   # runs migration_0003
    app.state.db = conn
    app.state.shares_ok = True
    reconcile_on_startup(conn, _detect_lock, build_adapters, settings)  # D-14 (reset orphans, verify-guard)
    app.state.scheduler = Scheduler(app, settings); app.state.scheduler.start()   # REL-01

@app.on_event("shutdown")
def _shutdown():
    sched = getattr(app.state, "scheduler", None)
    if sched: sched.stop()                                   # stop BEFORE closing the conn
    conn = getattr(app.state, "db", None)
    if conn: conn.close()
```
Imports stay lazy inside the handlers (the main.py:72-74 pattern: `from core.scheduler import Scheduler`) so the module parses in the offline 3.9 sandbox and tests can monkeypatch.

**Status routes** (RESEARCH Pattern 7, lines 364-393; REL-03/D-12). JSON is the Phase-6 widget contract; HTML escapes every ledger string:
```python
from fastapi.responses import HTMLResponse
from html import escape   # MANDATORY — titles are untrusted peer/*arr data (XSS/HTML-injection)

@app.get("/status.json")
def status_json():
    conn = app.state.db
    return {"counts": repo.status_counts(conn),
            "stuck": [_row_view(r) for r in repo.list_by_status(conn, "stuck")],
            "quarantined": [_row_view(r) for r in repo.list_by_status(conn, "quarantined")],
            "permanently_unavailable": [_row_view(r) for r in repo.list_by_status(conn, "permanently-unavailable")],
            "shares_ok": app.state.shares_ok}

@app.get("/status", response_class=HTMLResponse)
def status_html():
    d = status_json()
    rows = "".join(f"<tr><td>{escape(i['app'])}:{escape(i['id'])}</td>"
                   f"<td>{escape(i['title'] or '')}</td><td>{escape(i['reason'] or '')}</td></tr>"
                   for i in d["stuck"] + d["quarantined"] + d["permanently_unavailable"])
    return f"<html><body><h1>Curator status</h1><table>{rows}</table></body></html>"
```

---

### `app/core/gap_detector.py` (MOD — `detect_gaps` batch transaction, D-15)

**Analog:** the existing `detect_gaps` loop (gap_detector.py:23-39) + the BEGIN/COMMIT idiom in `db.py:84-90`.

**Pattern:** wrap the per-cycle upserts in ONE explicit transaction (one fsync per pass, not per row), preserving STATE-02 dedup + status-never-clobbered + `synchronous=FULL`:
```python
# Source: existing detect_gaps loop + db.py:84-92 BEGIN/COMMIT/ROLLBACK idiom. D-15.
def detect_gaps(adapters, conn) -> Dict[str, int]:
    counts = {}
    conn.execute("BEGIN;")
    try:
        for adapter in adapters:
            items = adapter.get_wanted()
            for it in items:
                repo.upsert_gap(conn, it)
            counts[adapter.app] = len(items)
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    return counts
```
**Test (D-15):** one detection pass commits in ONE transaction; dedup + status-never-clobbered + `discovered_at` preserved still hold (`tests/test_detect_batch.py`). Note: this runs under the writer lock in the scheduler (the detect happens inside `with _detect_lock:` per RESEARCH 145-147).

---

## Shared Patterns

### Single-writer SQLite + writer lock (D-16, Pitfall 4)
**Source:** `app/main.py:16-20` (`_detect_lock`), `app/state/db.py:28-46` (`connect`, `check_same_thread=False`, `synchronous=FULL`).
**Apply to:** `scheduler.py`, `reconcile.py`, every `repo.py` mutator call from a worker thread.
**Rule:** exactly ONE `app.state.db` connection; every ledger write goes through the shared lock. Workers parallelize IO only; never a second connection. The scheduler reuses `_detect_lock` so `/detect` and a cycle can't collide.

### *arr/slskd firewall (the load-bearing invariant)
**Source:** `app/tests/test_adapter_protocol.py:37-101` (`ARR_FIELD_NAMES` grep auto-scans all of `core/` + `state/` via `rglob`).
**Apply to:** ALL new core files (`scheduler.py`, `reconcile.py`, `shares.py`, `status_page.py`) and the migration SQL.
**Rule:** the queue-record keys (`records[`, `albumId`) stay in `lidarr.py`/`readarr.py`; the `shares.files` key + rescan stay in `slskd.py`. New core modules speak only neutral types + the repo. **No new test wiring needed — the grep already covers new core files automatically.** Extend `ARR_FIELD_NAMES` only if a new wire key (e.g. `"shares"`, `"queue"` JSON keys) needs guarding.

### Lazy httpx import + caller-owns-close (CR-02)
**Source:** `app/core/gap_detector.py:42-79` (`build_adapters`), `app/core/acquire.py:326-338` (`build_acquire_clients`).
**Apply to:** `scheduler.py`, `reconcile.py`, `main.py` handlers.
**Rule:** import `httpx`/adapters lazily inside the function (offline 3.9 sandbox parses the module); construct clients, use, then `for c in clients: c.close()` in `finally`.

### `?`-placeholder SQL + `_now_iso()` timestamps
**Source:** `app/state/repo.py:9-10` (security header), `repo.py:17-19` (`_now_iso`).
**Apply to:** every new query in `repo.py` (eligibility, backoff, counts) and the cutoff computations in `scheduler.py`.
**Rule:** never f-string a value into SQL; compute `:grace_cutoff`/`:dormant_cutoff` in Python as `_now_iso`-style Z-suffixed strings and bind via `?` (lexicographic compare valid).

### Offline-provable seams (injectable clock / stop-event / fakes)
**Source:** `app/core/acquire.py:220-242` (`now`/`poll_hook` injectable seams), `app/tests/conftest.py` (existing fakes), `test_slskd_client.py` (`httpx.MockTransport`).
**Apply to:** `scheduler.py` (stop-event + interval), `shares.py` (FakeSlskd with `application` state), `get_queue_status` (queue fixture).
**Rule:** the dev sandbox is Python 3.9 + no network — every test runs with fakes + an injected clock/stop-event so no test waits 6h and no live slskd/*arr is touched.

### HTML escaping (REL-03/D-12, Pitfall, V5)
**Source:** stdlib `html.escape` (RESEARCH Pattern 7) — no in-repo analog.
**Apply to:** every interpolated ledger string in `GET /status` HTML (album/artist/quarantine-reason are untrusted peer/*arr data).

---

## No Analog Found

| File | Role | Data Flow | Reason | Planner Guidance |
|------|------|-----------|--------|------------------|
| `app/core/status_page.py` (optional) | utility (view) | transform (ledger → HTML) | No server-rendered HTML exists in the repo yet (Phase 1-4 endpoints return JSON only: `/healthz`, `/readyz`, `/detect`) | Use RESEARCH Pattern 7 (lines 364-393): stdlib `html.escape` + f-string, no template engine. May inline in `main.py` instead of a separate module (planner's call — RESEARCH marks it "optional"). |

Everything else has a strong in-repo analog. The scheduler/reconcile/shares "no daemon exists yet" cases are role-matched: the *patterns* (lifecycle, lock, composition, injectable seams, neutral-firewall orchestration) are all present across `main.py` + `gap_detector.py` + `acquire.py`, so the planner copies pattern, not file.

## Metadata

**Analog search scope:** `app/core/`, `app/state/`, `app/adapters/`, `app/main.py`, `app/config.py`, `app/tests/` (firewall test).
**Files scanned (read in full):** `main.py`, `config.py`, `state/db.py`, `state/repo.py`, `state/migration_0002.sql`, `core/acquire.py`, `core/gap_detector.py`, `adapters/slskd.py`, `adapters/base.py`, `adapters/lidarr.py` (targeted), `tests/test_adapter_protocol.py` (firewall).
**Pattern extraction date:** 2026-05-31

**Open questions carried to the planner (from RESEARCH Assumptions/Open Questions):**
- A1 / OQ-1: `_safe_call` (acquire.py:315) must surface INFRA-class faults distinctly so an *arr/slskd outage burns NO attempt (REL-02). Scope as one task + `test_infra_classify.py`.
- A2 / OQ — confirm the *arr queue match field (`albumId`/`bookId`) live (one `curl`).
- A3 — confirm `GET /api/v0/application`.`shares.files` + `PUT /api/v0/shares` status codes live (one `curl`/Swagger).
- OQ-2: confirm `stuck`/`quarantined` are retry-eligible under backoff (research assumes yes).
- OQ-3 / A4: per-cycle item cap (`LIMIT = MAX_CONCURRENT * k`) + per-cycle env re-read for the kill-switch.
