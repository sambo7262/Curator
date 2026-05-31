# Phase 4: Acquisition, Staging & Clean Import - Pattern Map

**Mapped:** 2026-05-31
**Files analyzed:** 11 new/modified files
**Analogs found:** 11 / 11 (all files have close codebase analogs)

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/adapters/slskd.py` | adapter/client | request-response | `app/adapters/lidarr.py` | exact (same httpx pattern, same constructor shape, same defensive `.get()`) |
| `app/adapters/plex.py` | adapter/client | request-response (fire-and-forget) | `app/adapters/readarr.py` _paged swallow→degrade tail | role-match (tiny client; the swallow-all exception pattern is the same) |
| `app/adapters/base.py` | adapter/protocol | n/a (protocol definitions) | itself (existing) | self-extension (implement three declared stubs) |
| `app/adapters/lidarr.py` | adapter/client | request-response (CRUD import) | itself (existing) | self-extension (implement `manual_import_candidates`, `execute_import`, `verify_imported`) |
| `app/adapters/readarr.py` | adapter/client | request-response (swallow→degrade) | itself (existing) | self-extension (same import methods, best-effort fault posture from `_paged`) |
| `app/core/acquire.py` | orchestrator/service | event-driven (linear pipeline) | `app/core/gap_detector.py` (`detect_gaps` + `build_adapters`) | exact (same firewall-clean composition-point shape) |
| `app/core/staging.py` | utility | file-I/O | `app/core/gap_detector.py` (`detect_gaps` pure function) | partial (pure function shape; stdlib only) |
| `app/state/migration_0002.sql` | migration/DDL | batch | `app/state/schema.sql` (migration 0001) | exact (same DDL style, IF NOT EXISTS, inline comments) |
| `app/state/db.py` | config (migration runner) | batch | itself (existing) | self-extension (append one tuple to `MIGRATIONS`) |
| `app/state/repo.py` | model/DAO | CRUD | itself (existing) | self-extension (add `staged_files` DAO + acquisition-state mutators) |
| `app/config.py` | config | n/a | itself (existing) | self-extension (add Phase-4 tunables to `Settings.from_env()`) |

**Test files (new):**

| New Test File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/tests/test_slskd_client.py` | test | request-response | `app/tests/test_lidarr_adapter.py` | exact |
| `app/tests/test_acquire.py` | test | event-driven | `app/tests/test_gap_detector.py` | exact |
| `app/tests/test_staging.py` | test | file-I/O | `app/tests/test_state_repo.py` (tmp_path pattern) | role-match |
| `app/tests/test_plex.py` | test | request-response | `app/tests/test_readarr_adapter.py` (swallow tests) | role-match |
| `app/tests/fixtures/slskd/` | fixture data | n/a | `app/tests/fixtures/lidarr_missing.json` | exact |
| `app/tests/fixtures/manualimport/` | fixture data | n/a | `app/tests/fixtures/lidarr_missing.json` | role-match |

---

## Pattern Assignments

### `app/adapters/slskd.py` (adapter, request-response)

**Analog:** `app/adapters/lidarr.py`

**Imports pattern** (lidarr.py lines 1–14):
```python
import logging
from typing import Optional

import httpx

log = logging.getLogger(__name__)
```

**Constructor + header pattern** (lidarr.py lines 65–73):
```python
class LidarrAdapter:
    app = "lidarr"

    def __init__(self, base_url: str, api_key: str, client: httpx.Client):
        if not api_key:
            raise ValueError("LIDARR_API_KEY is required (music is the primary path)")
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {"X-Api-Key": api_key}   # [VERIFIED: Servarr v1 auth header]
```

**Difference for SlskdClient:** header key is `X-API-Key` (not `X-Api-Key`); base path appends `/api/v0` (not `/api/v1`). Constructor is otherwise identical.

**Defensive GET pattern** (lidarr.py lines 89–112):
```python
r = self._client.get(
    f"{self._base}/api/v1/{path}",
    headers=self._headers,
    params={...},
    timeout=30.0,
)
r.raise_for_status()
body = r.json()
batch = body.get("records", [])   # .get()-defensive, never subscript
```

**Core pattern for slskd.py** — replicate the constructor shape, then four methods following the same GET/POST/DELETE pattern. From RESEARCH.md lines 197–226 (verified endpoint paths):
- `search(text)` → `POST /api/v0/searches`, `json={"searchText": text}`, returns `r.json().get("id")`
- `search_state(sid)` → `GET /api/v0/searches/{id}`, returns `r.json()` dict
- `search_responses(sid)` → `GET /api/v0/searches/{id}/responses`, returns `r.json()` list
- `enqueue(username, files)` → `POST /api/v0/transfers/downloads/{username}`, `json=files`
- `transfer(username, transfer_id)` → `GET /api/v0/transfers/downloads/{username}/{id}`, returns dict with `state`, `bytesTransferred`
- `cancel(username, transfer_id, remove=True)` → `DELETE /api/v0/transfers/downloads/{username}/{id}?remove=true`

**Error handling pattern** — Lidarr is primary (hard fault surfaces via `raise_for_status()`). slskd is a new client; mirror Lidarr's posture for the initial implementation (slskd faults should surface). The `CircuitBreaker` seam already exists if Phase 5 wants to wrap it.

**Key conventions to replicate:**
- `self._base = base_url.rstrip("/") + "/api/v0"` (slskd base path, not `/api/v1`)
- `self._headers = {"X-API-Key": api_key}` (slskd uses capitalized `API`, not `Api`)
- All JSON responses accessed via `.get()`, never subscript (`r.json().get("id")`)
- `timeout=30.0` on reads, `timeout=60.0` on write/command calls (mirrors lidarr `_paged` 30.0 vs `get_manifest` 30.0)
- `raise_for_status()` on every call (primary posture)

---

### `app/adapters/plex.py` (adapter, fire-and-forget)

**Analog:** `app/adapters/readarr.py` (swallow→degrade pattern) + RESEARCH.md Plex snippet

**The entire Plex client is one method; the pattern is the Readarr swallow-all exception block** (readarr.py lines 70–102):
```python
try:
    r = self._client.get(...)
    r.raise_for_status()
    ...
except (httpx.HTTPError, ValueError, TypeError) as e:
    log.warning("readarr _paged(%s) swallowed fault -> []: %s", path, e)
    return []
```

**For plex.py**, the exception is `except Exception` (even broader — D-04 says never warn loudly, never block) and the log level is `debug` not `warning`:
```python
# From RESEARCH.md lines 427-432 (VERIFIED Plex URL command):
def refresh(self, section_id: str, path: str) -> None:
    try:
        r = self._client.get(
            f"{self._base}/library/sections/{section_id}/refresh",
            headers=self._headers,
            params={"path": path, "X-Plex-Token": self._token},
            timeout=10.0,
        )
        r.raise_for_status()
    except Exception as e:          # D-04: never warn loudly, never block on Plex
        log.debug("plex refresh hiccup (ignored): %s", e)
```

**Constructor shape** — mirror lidarr.py but without the hard-fail on missing key (Plex is optional/fire-and-forget; a missing `PLEX_TOKEN` should degrade silently, never gate music):
```python
class PlexClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client):
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client
```

**Key conventions:**
- `log.debug(...)` on hiccup (NOT `log.warning`) — D-04 explicit
- `except Exception` (broadest catch) — Plex is purely decorative
- Never `raise_for_status()` in a way that propagates — wrap the whole call in try/except
- `timeout=10.0` (short — Plex is fire-and-forget; don't hang)

---

### `app/adapters/base.py` — implement three stubbed methods (self-extension)

**Analog:** itself; the stubs to implement are already declared (base.py lines 81–91):
```python
def manual_import_candidates(self, path: str) -> list:
    """Phase 4 — *arr Manual Import API candidates for a staging path. Stubbed in Phase 2."""
    ...

def execute_import(self, decisions: list) -> None:
    """Phase 4 — commit a Manual Import decision set. Stubbed in Phase 2."""
    ...

def verify_imported(self, item: GapItem) -> bool:
    """Phase 4 — confirm the *arr imported the item into the library. Stubbed in Phase 2."""
    ...
```

**Pattern:** the Protocol declares the interface; the concrete signature implemented in `lidarr.py`/`readarr.py` must match exactly. The `base.py` stubs' docstrings can be updated to remove "Stubbed in Phase 2" and add a brief note once both adapters implement them.

---

### `app/adapters/lidarr.py` — implement import methods (self-extension, primary posture)

**Analog:** existing methods in lidarr.py — `get_manifest` (lines 195–240) for the GET pattern; `get_quality_profile` (lines 148–193) for the POST-command pattern. From RESEARCH.md Pattern 3 (lines 248–287).

**`manual_import_candidates` GET pattern** — mirrors `get_manifest` (lidarr.py lines 205–212):
```python
r = self._client.get(
    f"{self._base}/api/v1/manualimport",
    headers=self._headers,
    params={"folder": folder, "downloadId": download_id,
            "filterExistingFiles": "true", "replaceExistingFiles": "true"},
    timeout=60.0,
)
r.raise_for_status()
return r.json()   # list[ManualImportResource]; CALLER filters
```

**`execute_import` POST-command pattern** — new; POST to `/api/v1/command` (RESEARCH.md lines 265–286):
```python
body = {
    "name": "ManualImport",
    "importMode": "Move",   # atomic hardlink within /data (D-09) [ASSUMED: casing — verify live]
    "files": [
        {
            "path": d["path"],
            "artistId": d["artist"]["id"],
            "albumId": d["album"]["id"],
            "albumReleaseId": d["albumReleaseId"],
            "trackIds": [t["id"] for t in d["tracks"]],
            "quality": d["quality"],
            "indexerFlags": d.get("indexerFlags", 0),
            "disableReleaseSwitching": False,
            "downloadId": d.get("downloadId"),
        }
        for d in decisions
    ],
}
r = self._client.post(f"{self._base}/api/v1/command",
                      headers=self._headers, json=body, timeout=60.0)
r.raise_for_status()
```

**`verify_imported` pattern** — re-calls `get_wanted()` (or a targeted album lookup) and checks whether the item left the wanted/missing list. Returns `bool`. Mirrors the existing `get_wanted()` internal loop but for a single ID. **Never treat a completed download as imported** (D-03).

**Key conventions:**
- Lidarr is primary: `raise_for_status()` surfaces hard faults — do NOT swallow
- All *arr field names (`folder`, `downloadId`, `albumReleaseId`, `importMode`, `files[]` keys) stay INSIDE lidarr.py — never cross into `core/acquire.py`
- `timeout=60.0` on import calls (slower operations)

---

### `app/adapters/readarr.py` — implement import methods (self-extension, best-effort posture)

**Analog:** existing Readarr `_paged` swallow pattern (readarr.py lines 61–103) and `get_quality_profile` best-effort (lines 146–182).

**All three import methods must wrap the entire body in the same `try/except`** (readarr.py lines 93–102):
```python
except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
    log.warning("readarr %s degraded -> failed: %s", method_name, e)
    return safe_default   # [] for candidates, None for execute, False for verify
```

**Key conventions (ARR-02, Pitfall 5):**
- `get_quality_profile`-style swallow on EVERY readarr import method — a Readarr 5xx/timeout returns a safe default (not a raised exception)
- `verify_imported` returns `False` on any fault (not `True` — false-negative is safe; false-positive would skip cleanup)
- `manual_import_candidates` returns `[]` on fault (core's filter step sees an empty list → book goes to quarantine-on-failure, music unaffected)
- Book import fields differ from album (`bookId`/`editionId`/`authorId`) — keep them adapter-local (A5 — best-effort; Readarr unmaintained; if wrong, book skipped not music blocked)

---

### `app/core/acquire.py` (orchestrator, linear pipeline)

**Analog:** `app/core/gap_detector.py` — the Phase-2 composition point. The shape is nearly identical: a pure `acquire_item(...)` function (not a class) that calls adapter methods, reads neutral results, and writes to the ledger.

**Top-of-file comment pattern** (gap_detector.py lines 1–12):
```python
# Curator gap detector — the integration point where the *arr seam meets the ledger spine.
# detect_gaps() is the ONLY caller of the adapters and the ONLY orchestrator of the upsert; it is
# the core side of the firewall (PITFALL #6) so it must contain ZERO *arr field names or wire
# vocabulary — it speaks only GapItem + the repo.
```

**For acquire.py:** same firewall contract — it must speak only `GapItem`, `GateResult`, `Candidate`, and neutral progress/result shapes. No `folder`, `downloadId`, `albumReleaseId`, `importMode`, `X-API-Key`, or any other *arr/slskd wire vocabulary. The firewall grep test (test_gate.py lines 235–261) pattern should be extended to cover `core/acquire.py`.

**Imports pattern** (gap_detector.py lines 13–18 — lazy imports of httpx-dependent modules):
```python
import logging
import sqlite3
from typing import Any, Dict, List, Tuple

from adapters.base import ArrAdapter
from state import repo

log = logging.getLogger(__name__)
```

**Composition function signature** — mirrors `detect_gaps(adapters, conn)`:
```python
def acquire_item(
    item: GapItem,
    adapter: ArrAdapter,
    slskd,          # SlskdClient (typed loosely so core doesn't import adapters.slskd directly)
    plex,           # PlexClient | None
    conn: sqlite3.Connection,
    settings,       # Settings for tunables
) -> str:           # returns neutral outcome: "imported" | "quarantined" | "stuck"
    ...
```

**Factory function pattern** — mirrors `build_adapters()` (gap_detector.py lines 42–79):
```python
def build_acquire_clients():
    """Construct slskd + Plex clients. Returns (slskd_client, plex_client, httpx_clients).
    Lazy import of httpx (mirrors gap_detector.build_adapters pattern — offline-parse-safe).
    """
    import httpx
    from adapters.slskd import SlskdClient
    from adapters.plex import PlexClient
    from config import settings
    ...
```

**Key conventions:**
- Zero *arr field names in this file (grep-enforced)
- Adapter methods return neutral shapes before core sees them: `adapter.manual_import_candidates(path)` → list of opaque dicts that core passes straight back to `adapter.execute_import(filtered)` (core never reads the dict keys)
- `time.monotonic()` for stall detection (mirrors `breaker.py` lines 43–44)
- `repo.set_status(conn, ...)` for every lifecycle transition (mirrors gap_detector lines 37–38)

---

### `app/core/staging.py` (utility, file-I/O) — optional but recommended

**Analog:** `app/core/gap_detector.py` pure function shape (no class, no I/O beyond its single concern). From RESEARCH.md Quarantine+TTL snippet (lines 411–420).

**Functions to implement (all pure filesystem, testable with `tmp_path`):**
```python
def staging_path(downloads_root: str, batch_id: str) -> Path:
    """Compute the per-item staging dir inside the shared /data tree.
    Never create the dir — only compute the path (creation is slskd's job via batchId routing).
    """

def assert_under_root(path: Path, root: Path) -> None:
    """Path-traversal guard: resolve path and assert root is in its parents.
    Raises ValueError if path escapes root (security: malicious peer filename defense).
    """

def purge_staging(staging_dir: Path, root: Path) -> None:
    """D-05: rm -rf the staging dir; assert it is strictly under root first."""

def quarantine_staging(staging_dir: Path, quarantine_root: Path, label: str) -> Path:
    """D-06: move staging_dir into quarantine_root/{label}-{timestamp}. Returns new path."""

def purge_expired_quarantine(quarantine_root: Path, ttl_seconds: float) -> int:
    """D-06: remove quarantine subdirs older than ttl_seconds. Returns count purged."""
```

**Key conventions:**
- `shutil.rmtree` / `shutil.move` / `Path.mkdir` — no library beyond stdlib
- Always call `assert_under_root()` before any `rmtree` or `move` (security: T-12 path traversal)
- Never purge `/`, `/data`, or `/data/media` — the guard must refuse shallow paths

---

### `app/state/migration_0002.sql` (migration, DDL)

**Analog:** `app/state/schema.sql` (migration 0001)

**DDL style** (schema.sql lines 1–27):
```sql
-- Curator state ledger — migration 0001: the `items` table (the persistent spine).
-- Phase 2 scope is EXACTLY one table. ...
CREATE TABLE IF NOT EXISTS items (
  id                 INTEGER PRIMARY KEY,
  arr_app            TEXT NOT NULL,
  ...
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','searching','grabbed','downloaded',
                                       'imported','unavailable','blacklisted')),
  ...
  UNIQUE (arr_app, arr_id)
);
CREATE INDEX IF NOT EXISTS idx_items_status   ON items(status);
```

**For migration_0002.sql — what to implement:**
1. Widen the `status` CHECK constraint on `items` to add the new lifecycle states. SQLite does not support `ALTER TABLE ... ALTER COLUMN`, so the standard approach is a table rebuild: rename `items` → `items_old`, recreate with the new CHECK, copy data, drop old. This is all inside a single transaction (the migration runner wraps it in `BEGIN/COMMIT`).
2. Add the `staged_files` table (new in Phase 4):
```sql
-- Migration 0002: extend acquisition lifecycle states + add staged_files table.
-- New status values: 'downloading' | 'importing' | 'quarantined' | 'stuck'
-- (in addition to the existing Phase-2 set).
CREATE TABLE IF NOT EXISTS staged_files (
  id              INTEGER PRIMARY KEY,
  item_id         INTEGER NOT NULL REFERENCES items(id),
  staging_path    TEXT NOT NULL,
  quarantine_path TEXT,
  failure_reason  TEXT,
  quarantined_at  TEXT,
  created_at      TEXT NOT NULL
);
```

**Key conventions:**
- `IF NOT EXISTS` on every CREATE (idempotency — STATE-01)
- Inline `--` comments explaining purpose (same style as schema.sql)
- The migration runner in `db.py` splits on `;` and strips `--` comments before execution (db.py lines 46–62) — do not embed a `;` inside a string literal

---

### `app/state/db.py` — append migration tuple (self-extension)

**Analog:** itself. The extension is a single line added to the `MIGRATIONS` list (db.py lines 14–22):

```python
_SCHEMA_0001 = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
_SCHEMA_0002 = (Path(__file__).parent / "migration_0002.sql").read_text(encoding="utf-8")

MIGRATIONS: List[Tuple[str, str]] = [
    ("0001", _SCHEMA_0001),
    ("0002", _SCHEMA_0002),   # Phase 4: acquisition states + staged_files table
]
```

**Key conventions (db.py lines 19–21 comment):**
- "never reorder or mutate shipped ones" — `("0001", ...)` stays untouched
- The 1-based index IS the user_version — appending at position 2 bumps the DB to version 2

---

### `app/state/repo.py` — add acquisition-state DAOs (self-extension)

**Analog:** existing `set_status`, `get_gap`, `list_by_status` in repo.py (lines 77–96).

**`set_status` pattern** (repo.py lines 77–87) — already exists; Phase 4 just calls it with new enum values after migration_0002 widens the CHECK:
```python
def set_status(conn: sqlite3.Connection, arr_app: str, arr_id: str, status: str) -> None:
    conn.execute(
        "UPDATE items SET status = ? WHERE arr_app = ? AND arr_id = ?",
        (status, arr_app, arr_id),
    )
```

**New DAOs to add** — same `?` placeholder style, same `sqlite3.Connection` first arg:
```python
def record_staged_file(conn, item_id: int, staging_path: str) -> int:
    """Insert a staged_files row when a download starts. Returns the row id."""
    now = _now_iso()
    cur = conn.execute(
        "INSERT INTO staged_files (item_id, staging_path, created_at) VALUES (?, ?, ?)",
        (item_id, staging_path, now),
    )
    return cur.lastrowid

def record_quarantine(conn, staged_file_id: int, quarantine_path: str, reason: str) -> None:
    """D-06: update the staged_files row with quarantine info (path + reason + timestamp)."""
    now = _now_iso()
    conn.execute(
        "UPDATE staged_files SET quarantine_path=?, failure_reason=?, quarantined_at=?"
        " WHERE id=?",
        (quarantine_path, reason, now, staged_file_id),
    )
```

**Key conventions:**
- `?` placeholders only — never f-string into SQL (repo.py line 9 security note)
- `_now_iso()` for all timestamps (already defined at repo.py line 17)
- `list_by_status(conn, "downloading")` already works for free after migration widens the CHECK — no new function needed for simple status queries

---

### `app/config.py` — extend Settings (self-extension)

**Analog:** itself. The extension pattern follows the existing Phase-3 tunables block exactly (config.py lines 29–38 + 57–63).

**Fields to add** — same frozen dataclass fields with static defaults:
```python
# Phase-4 acquisition tunables (SP-4). All env-overridable via from_env(); no rebuild needed.
slskd_url: str = "http://localhost:5030"      # default; prod uses NAS IP via DEPLOY.md
slskd_api_key: Optional[str] = None
acq_search_window_seconds: float = 12.0       # D-07 collection window (Claude's Discretion)
acq_stall_seconds: float = 600.0              # D-01 no-progress stall threshold (~10 min)
acq_poll_seconds: float = 5.0                 # transfer poll interval
staging_root: str = "/data/downloads/soulseek"
quarantine_root: str = "/data/downloads/soulseek/.quarantine"
quarantine_ttl_seconds: float = 604800.0      # D-06 TTL (~7 days)
plex_url: str = "http://plex:32400"
plex_token: Optional[str] = None
plex_section_id: str = ""                     # must be set in env for Plex refresh to fire
```

**`from_env()` extension pattern** (config.py lines 51–64):
```python
return cls(
    ...  # existing fields unchanged
    slskd_url=os.getenv("SLSKD_URL", "http://localhost:5030"),
    slskd_api_key=os.getenv("SLSKD_API_KEY"),
    acq_search_window_seconds=float(os.getenv("ACQ_SEARCH_WINDOW_SECONDS", "12.0")),
    acq_stall_seconds=float(os.getenv("ACQ_STALL_SECONDS", "600.0")),
    acq_poll_seconds=float(os.getenv("ACQ_POLL_SECONDS", "5.0")),
    staging_root=os.getenv("STAGING_ROOT", "/data/downloads/soulseek"),
    quarantine_root=os.getenv("QUARANTINE_ROOT", "/data/downloads/soulseek/.quarantine"),
    quarantine_ttl_seconds=float(os.getenv("QUARANTINE_TTL_SECONDS", "604800.0")),
    plex_url=os.getenv("PLEX_URL", "http://plex:32400"),
    plex_token=os.getenv("PLEX_TOKEN"),
    plex_section_id=os.getenv("PLEX_SECTION_ID", ""),
)
```

**Key conventions:**
- `float(os.getenv(..., "..."))` for all numeric tunables — fails fast with clear `ValueError` at startup (config.py line 49 note)
- `Optional[str] = None` for keys/tokens — never baked in, never logged
- `@dataclass(frozen=True)` remains — no changes to the class decorator

---

## Test File Pattern Assignments

### `app/tests/test_slskd_client.py`

**Analog:** `app/tests/test_lidarr_adapter.py`

**Offline client setup pattern** (test_lidarr_adapter.py lines 141–147):
```python
def _profile_client(profile_json):
    def _handler(request: httpx.Request) -> httpx.Response:
        if "/api/v1/qualityprofile/" in request.url.path:
            return httpx.Response(200, json=profile_json)
        return httpx.Response(404, json={})
    return httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
```

For slskd tests: substitute `base_url="http://test-slskd"`, check `/api/v0/searches`, `/api/v0/transfers/downloads/`, etc. Load canned responses from `fixtures/slskd/`.

**Fixture loading** — `conftest.py` `load_fixture` (conftest.py lines 27–39) loads from `fixtures/`. Phase 4 adds `fixtures/slskd/search_responses.json`, `fixtures/slskd/transfer_completed.json`, `fixtures/slskd/transfer_stalled.json` in the same style as `fixtures/lidarr_missing.json`.

**API key fail-fast test pattern** (test_lidarr_adapter.py lines 89–95):
```python
def test_missing_api_key_fails_fast(httpx_client):
    import pytest
    for bad in (None, ""):
        with pytest.raises(ValueError, match="SLSKD_API_KEY"):
            SlskdClient("http://test-slskd", bad, httpx_client({}))
```

---

### `app/tests/test_acquire.py`

**Analog:** `app/tests/test_gap_detector.py`

**Fake adapter pattern** (test_gap_detector.py lines 57–70):
```python
class FakeAdapter:
    """A minimal ArrAdapter: exposes `app` + get_wanted() returning a fixed GapItem list."""
    def __init__(self, app, items):
        self.app = app
        self._items = items
    def get_wanted(self):
        return list(self._items)
```

**For test_acquire.py:** same pattern — `FakeSlskdClient` (returns canned search responses and transfer states; fake clock for stall detection), `FakeAdapter` extended with `manual_import_candidates`, `execute_import`, `verify_imported`.

**Fake clock for stall detection** — inject `time.monotonic` via a `monkeypatch` or a clock parameter so stall tests don't need `sleep`. Mirror breaker.py's `_now()` injection seam (breaker.py lines 43–44):
```python
def _now(self) -> float:
    return time.monotonic()
```

**ARR-02 test pattern** (test_gap_detector.py lines — Readarr fault isolates music):
```python
def test_readarr_import_fault_does_not_gate_music():
    """A Readarr import fault quarantines only that book; the music item completes normally."""
    ...
```

---

### `app/tests/test_staging.py`

**Analog:** `app/tests/test_state_repo.py` — uses `tmp_db_path`/`tmp_path` for isolated filesystem state.

**tmp_path pattern** (conftest.py lines 17–24):
```python
@pytest.fixture
def tmp_db_path(tmp_path):
    return str(tmp_path / "curator-test.sqlite")
```

For staging tests: use pytest's built-in `tmp_path` fixture directly (no conftest addition needed — it's a standard pytest fixture). Tests call `staging.purge_staging(tmp_path / "staging", root=tmp_path)` etc.

**Path-identity test** — create a file in `tmp_path / "staging"`, hardlink it to `tmp_path / "dest"`, assert `os.path.samefile`. This proves the Move operation within a single filesystem is atomic/hardlink-capable (the IMPORT-01 guarantee).

---

### `app/tests/test_plex.py`

**Analog:** `app/tests/test_readarr_adapter.py` (swallow/degrade tests)

**Swallow-fault pattern** (test_readarr_adapter.py lines 62–68):
```python
def test_5xx_returns_empty():
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "readarr is down"})
    client = httpx.Client(transport=httpx.MockTransport(_handler), base_url="http://test-arr")
    assert _adapter(client).get_wanted() == []
```

For test_plex.py: confirm `plex.refresh(section_id, path)` does NOT raise on a 5xx, timeout, or connection error — it returns `None` quietly. Also confirm `log.debug` (not `log.warning`) is called:
```python
def test_refresh_swallows_fault(caplog):
    import logging
    # 5xx from Plex must not propagate, must log at DEBUG only
    ...
    with caplog.at_level(logging.DEBUG):
        plex.refresh("1", "/data/media/music/Artist")
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
```

---

### Firewall grep extension — `app/tests/test_gate.py`

**Analog:** existing firewall grep (test_gate.py lines 235–261).

**Pattern to replicate for Phase-4 firewall:**
```python
def test_acquire_has_no_arr_field_names():
    """*arr-agnostic firewall: core/acquire.py must contain ZERO *arr/slskd wire vocabulary."""
    ARR_FIELDS = re.compile(
        r"(?:folder|downloadId|albumReleaseId|importMode|X-Api-Key|X-API-Key"
        r"|ManualImport|artistId|albumId|trackIds|searchText|bytesTransferred"
        r"|isComplete|hasFreeUploadSlot)\b"
    )
    acquire_src = (APP_DIR / "core" / "acquire.py").read_text(encoding="utf-8")
    for n, raw in enumerate(acquire_src.splitlines(), start=1):
        code = _strip_comment(raw)
        m = ARR_FIELDS.search(code)
        assert not m, f"core/acquire.py:{n}: arr/slskd wire vocabulary found: {raw.strip()}"
```

---

## Shared Patterns

### Authentication / Header
**Source:** `app/adapters/lidarr.py` lines 69–73
**Apply to:** `app/adapters/slskd.py` (key is `X-API-Key`), `app/adapters/lidarr.py` import methods, `app/adapters/readarr.py` import methods
```python
if not api_key:
    raise ValueError("SLSKD_API_KEY is required")
self._headers = {"X-API-Key": api_key}   # slskd header (capital API)
```
For *arr adapters the existing `self._headers = {"X-Api-Key": api_key}` is unchanged.

### Defensive `.get()` / never subscript untrusted JSON
**Source:** `app/adapters/lidarr.py` lines 104–110; `app/adapters/readarr.py` lines 86–90; `app/core/candidate.py` lines 137–155
**Apply to:** all new adapter methods, `SlskdClient` responses, `PlexClient`
```python
body = r.json()
batch = body.get("records", [])   # .get()-defensive, never body["records"]
item = batch[0] if batch else {}
field = item.get("someField")      # None if absent, not KeyError
```

### Primary (raise) vs Best-Effort (swallow) fault posture
**Source:** `app/adapters/lidarr.py` (raises) vs `app/adapters/readarr.py` (swallows)
**Apply to:**
- `slskd.py` + `lidarr.py` import methods → raise (`raise_for_status()` propagates)
- `readarr.py` import methods → swallow (same `except (httpx.HTTPError, ValueError, TypeError, KeyError)` block)
- `plex.py` → swallow + `log.debug` (not `log.warning`)

### Monotonic clock for time-based checks
**Source:** `app/adapters/breaker.py` lines 43–44
**Apply to:** `app/core/acquire.py` stall detection, `app/core/staging.py` TTL purge
```python
def _now(self) -> float:
    return time.monotonic()
```

### `?` placeholders — never f-string into SQL
**Source:** `app/state/repo.py` lines 36–66 (every conn.execute)
**Apply to:** all new `staged_files` DAO methods in repo.py

### Lazy httpx import for offline-parse safety
**Source:** `app/core/gap_detector.py` lines 53–54
**Apply to:** `app/core/acquire.py` `build_acquire_clients()`
```python
def build_acquire_clients():
    import httpx   # lazy: module parses even where httpx is absent (offline 3.9 sandbox)
    from adapters.slskd import SlskdClient
    ...
```

### Migration append pattern
**Source:** `app/state/db.py` lines 14–22
**Apply to:** `app/state/db.py` (add `_SCHEMA_0002` load + append to `MIGRATIONS`)

---

## No Analog Found

All Phase-4 files have close analogs. No entries in this section.

---

## Metadata

**Analog search scope:** `app/adapters/`, `app/core/`, `app/state/`, `app/config.py`, `app/tests/`
**Files scanned:** 34 Python files + 2 SQL files + 27 fixture/test files
**Analog reads:** 12 files read in full (lidarr.py, readarr.py, base.py, breaker.py, config.py, gap_detector.py, gate.py, candidate.py, db.py, repo.py, schema.sql, conftest.py) + targeted reads of test_lidarr_adapter.py, test_readarr_adapter.py, test_state_repo.py, test_gap_detector.py, test_gate.py
**Pattern extraction date:** 2026-05-31

**Critical open items for planner (from RESEARCH.md assumptions log):**
- A1 (HIGH RISK): ManualImport POST `files[]` element keys + `importMode` casing — MUST be confirmed by a live DevTools capture at D-11 time before production trust. Plan a Wave-0 NAS verification task.
- A2 (MEDIUM): slskd `batchId` settability on enqueue — try it; fallback is the `downloads/{remote-folder}/` route (still works).
- A3 (HIGH RISK): slskd terminal transfer `state` enum exact strings — plan a live probe task after D-11 shares are confirmed.
