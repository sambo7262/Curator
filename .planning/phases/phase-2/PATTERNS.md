# Phase 2: State Ledger + *arr Adapter + Gap Detection - Pattern Map

**Mapped:** 2026-05-30
**Files analyzed:** 16 new/modified (extracted from RESEARCH.md "Recommended Project Structure")
**Analogs found:** 6 with usable analogs / 16 (10 are genuinely new patterns — see "No Analog Found")

> The Phase-1 codebase is a 3-file FastAPI stub (`app/main.py`, `app/requirements.txt`,
> `app/tests/test_health.py`) plus container/config files. It establishes **conventions** (import
> style, env access, test layout, dependency pinning, the `app/`-as-import-root rule) but contains
> **no** SQLite, adapter, or HTTP-client code. So most Phase-2 modules have a strong *convention*
> analog but no *behavioral* analog — those are flagged explicitly with a recommended convention
> consistent with the existing style.

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `app/main.py` (MODIFY: add startup migration hook) | config / app entrypoint | request-response | `app/main.py` (self — existing stub) | exact (extend in place) |
| `app/config.py` (NEW) | config | transform (env→typed) | `app/main.py` env access (`os.getenv`) + `.env.example` + compose `environment:` | role-match (convention only) |
| `app/state/db.py` (NEW) | persistence (connection + migrations) | file-I/O | none (no SQLite in codebase) | no analog — new pattern |
| `app/state/schema.sql` / `migrations/*.sql` (NEW) | migration | file-I/O | none | no analog — new pattern |
| `app/state/repo.py` (NEW) | repository / DAO | CRUD (upsert) | none | no analog — new pattern |
| `app/state/__init__.py` (NEW) | package marker | — | none (no packages exist yet) | no analog — new convention |
| `app/adapters/base.py` (NEW) | model + interface (Protocol + GapItem) | transform | `app/main.py` type-hint + docstring style | partial (convention only) |
| `app/adapters/lidarr.py` (NEW) | adapter / service | request-response (paged GET) | none (no httpx use) | no analog — new pattern |
| `app/adapters/readarr.py` (NEW) | adapter / service | request-response (defensive) | none | no analog — new pattern |
| `app/adapters/breaker.py` (NEW) | utility (fault isolation) | event-driven (failure counting) | none | no analog — new pattern |
| `app/adapters/__init__.py` (NEW) | package marker | — | none | no analog — new convention |
| `app/core/gap_detector.py` (NEW) | core orchestrator | batch (iterate→upsert) | none | no analog — new pattern |
| `app/requirements.txt` (MODIFY: + httpx) | config | — | `app/requirements.txt` (self) | exact |
| `app/tests/conftest.py` (NEW) | test fixtures | — | none (no conftest yet) | no analog — new convention |
| `app/tests/fixtures/*.json` (NEW) | test data | — | none | no analog — new convention |
| `app/tests/test_*.py` (NEW: repo/lidarr/readarr/gap_detector/protocol) | test | — | `app/tests/test_health.py` | role-match (strong convention analog) |

---

## Pattern Assignments

### `app/main.py` (MODIFY — config / app entrypoint)

**Analog:** the file itself (extend in place; do NOT restructure — RESEARCH "Recommended Project Structure" note).

**Module-header + import + app-construction convention to preserve** (`app/main.py:1-10`):
```python
# Curator — Phase 1 health/status stub.        <- top-of-file purpose comment is the house style
import os
from pathlib import Path

from fastapi import FastAPI

app = FastAPI(title="Curator", version="0.1.0-phase1")
```
Conventions to replicate: a leading `#`-comment banner stating the file's purpose; stdlib imports
first, blank line, then third-party; module-level singletons (`app`, `DATA`) defined right after
imports. **Bump `version="0.2.0-phase2"`** when extending.

**Phase-2 change:** add a FastAPI startup hook that runs migrations before serving (RESEARCH
"Migration runner" + line 562-563). Keep the existing `/healthz` and `/readyz` handlers untouched.
Pattern to add (consistent with the existing handler style — small, typed, returns a dict):
```python
from config import settings          # NEW typed config (see app/config.py)
from state.db import connect, run_migrations

@app.on_event("startup")
def _startup() -> None:
    """Reconcile the SQLite schema on boot so a recreated container is self-healing (STATE-01, criterion 1)."""
    run_migrations(connect(settings.db_path))
```
> Note: imports are bare module names (`from config import ...`, `from state.db import ...`) NOT
> `from app.config import ...` — because `pyproject.toml` sets `pythonpath=["app"]` and the Dockerfile
> does `COPY app/ .` with `WORKDIR /app`, so `app/` IS the import root. This is the single most
> important codebase convention for Phase 2. See "Shared Patterns → Import Root" below.

---

### `app/config.py` (NEW — config, env→typed transform)

**Analog (convention only):** `app/main.py:4,25` (`import os` / `os.getenv("SLSKD_URL")`) +
`.env.example` + `docker-compose.yml` `curator.environment:` block.

**Existing env-access convention** (`app/main.py:25`):
```python
"slskd_url": os.getenv("SLSKD_URL"),
```
Phase 1 reads env inline with `os.getenv` and tolerates `None`. RESEARCH ("Don't Hand-Roll" row
"Config loading", PITFALLS #4) recommends consolidating into **one declarative `config.py`** rather
than scattering `os.getenv`. The env var NAMES are already fixed by the compose file — reuse them
verbatim (do not invent new names):

**Env vars already surfaced into the container** (`docker-compose.yml` curator service):
```yaml
LIDARR_URL: "http://lidarr:8686"
LIDARR_API_KEY: "${LIDARR_API_KEY}"
READARR_URL: "http://readarr:8787"
READARR_API_KEY: "${READARR_API_KEY}"
```
**New for Phase 2** (must be added to `.env.example` + the compose `curator.environment:` block):
`DB_PATH` (default `/db/curator.sqlite` per RESEARCH line 715) and a new `/db` bind-mount.

**Recommended shape** (RESEARCH discretion item 3 + "Don't Hand-Roll"): a typed config object.
`pydantic` v2 is already in the image (via FastAPI 0.115.6 — `requirements.txt:1`), so
`pydantic-settings` or a frozen dataclass both fit. Keep it a single module-level `settings`
singleton, mirroring how `main.py` defines module-level singletons:
```python
# app/config.py — one declarative place for env (PITFALLS #4). pydantic is already in the image.
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    lidarr_url: str = os.getenv("LIDARR_URL", "http://lidarr:8686")
    lidarr_api_key: str | None = os.getenv("LIDARR_API_KEY")
    readarr_url: str = os.getenv("READARR_URL", "http://readarr:8787")
    readarr_api_key: str | None = os.getenv("READARR_API_KEY")
    db_path: str = os.getenv("DB_PATH", "/db/curator.sqlite")

settings = Settings()
```
**Security (RESEARCH "Security Domain" V2/V14):** API keys come from env only, never logged, never
baked into the image. `.env` is already gitignored (Phase 1).

---

### `app/state/db.py` (NEW — persistence: connection + idempotent migrations)

**Analog:** NONE. No SQLite, no `sqlite3`, no migration code exists in the codebase. **This is a new
pattern.** Recommended convention (consistent with `main.py`'s typed, small-function, purpose-comment
style) — take it verbatim from RESEARCH:

**WAL connection** (RESEARCH lines 540-549):
```python
import sqlite3
def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    return conn
```
**Idempotent versioned migration runner** (RESEARCH lines 552-561 — gated on `PRAGMA user_version`):
```python
MIGRATIONS = [ ("0001", _SQL_0001_ITEMS) ]   # ordered (version, sql)
def run_migrations(conn) -> None:
    have = conn.execute("PRAGMA user_version;").fetchone()[0]
    for i, (_, sql) in enumerate(MIGRATIONS, start=1):
        if i > have:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i};")
```
**Constraints (RESEARCH "WAL gotchas" + PITFALLS #2/#3):** single writer connection;
DB on `/db` (its own mount, NOT under `/data`); WAL safe only on the local `/volume1` FS; `f"PRAGMA
user_version = {i}"` is the ONLY acceptable f-string-into-SQL (it's a loop-controlled int, never
user input — all data queries MUST use `?` placeholders, see Security V5).

---

### `app/state/schema.sql` (NEW — migration DDL)

**Analog:** NONE. New pattern. Use the verified Phase-2 schema verbatim (RESEARCH lines 515-537) —
**one** `items` table only (do NOT build the `attempts`/`staged_files`/`events` tables — those are
Phases 4-6):
```sql
CREATE TABLE IF NOT EXISTS items (
  id                 INTEGER PRIMARY KEY,
  arr_app            TEXT NOT NULL,
  arr_id             TEXT NOT NULL,
  kind               TEXT NOT NULL,
  gap_type           TEXT NOT NULL,
  title              TEXT,
  artist_or_author   TEXT,
  foreign_id         TEXT,
  quality_profile_id INTEGER,
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','searching','grabbed','downloaded',
                                       'imported','unavailable','blacklisted')),
  discovered_at      TEXT NOT NULL,
  last_seen_at       TEXT NOT NULL,
  raw_json           TEXT,
  UNIQUE (arr_app, arr_id)                          -- THE dedup primitive (STATE-02)
);
CREATE INDEX IF NOT EXISTS idx_items_status   ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind ON items(arr_app, kind);
```
The `CHECK` enum is the authoritative STATE-01 lifecycle set; `UNIQUE(arr_app, arr_id)` is the
authoritative STATE-02 dedup key.

---

### `app/state/repo.py` (NEW — repository / DAO, CRUD-upsert)

**Analog:** NONE. New pattern. This is the load-bearing correctness module. Use the verified upsert
(RESEARCH lines 360-379) — the `ON CONFLICT` clause **must NOT reset `status`** (the STATE-02 trap,
PITFALL #1):
```python
def upsert_gap(conn, item: GapItem) -> None:
    conn.execute(
        """
        INSERT INTO items (arr_app, arr_id, kind, gap_type, title, artist_or_author,
                           foreign_id, quality_profile_id, status, discovered_at, last_seen_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', :now, :now, ?)
        ON CONFLICT(arr_app, arr_id) DO UPDATE SET
            gap_type=excluded.gap_type, title=excluded.title,
            artist_or_author=excluded.artist_or_author, foreign_id=excluded.foreign_id,
            quality_profile_id=excluded.quality_profile_id,
            last_seen_at=excluded.last_seen_at, raw_json=excluded.raw_json
        -- NEVER overwrite `status` on conflict (STATE-02 / PITFALL #1)
        """,
        (...),   # parameterized — `?` placeholders ONLY, never f-strings (Security V5)
    )
```
**Also expose** (RESEARCH line 254 + Lifecycle State Model line 495): `get_gap()`, `set_status()`
(round-trips the enum; tests exercise it), `list_by_status()`. `set_status` is the only lifecycle
mutator Phase 2 implements; the search→import transitions are declared-only (Phases 4-5).

---

### `app/adapters/base.py` (NEW — Protocol + GapItem model)

**Analog (convention only):** `app/main.py` type-hints + docstring style. No Protocol/dataclass
exists yet. **The interface shape itself is a new pattern** — use the locked seam from RESEARCH
lines 271-299 (the `GapItem` frozen dataclass + `ArrAdapter` Protocol). Phase 2 *implements* only
`get_wanted()`; the import/command/profile methods are declared-and-`...`-stubbed so the seam is
locked now (RESEARCH "Adapter Seam Design" line 609).

Key field-name correctness to bake in: `quality_profile_id` maps from Lidarr's `profileId` (NOT
`qualityProfileId`) — RESEARCH line 288, 632. `arr_id` is stringified. `raw` preserves the original
record for later phases.

---

### `app/adapters/lidarr.py` (NEW — adapter / service, paged request-response)

**Analog:** NONE (no `httpx`, no REST client in the codebase). New pattern. Use the verified paged
GET (RESEARCH lines 305-351):
- Injected `httpx.Client` (testability via `respx`); `X-Api-Key` header (Servarr v1 auth);
  base path `/api/v1`; `timeout=30.0`.
- Paging loop over `{page, pageSize, totalRecords, records}` envelope; `params` include
  `page/pageSize/sortKey=releaseDate/sortDirection/monitored=true/includeArtist=true`.
- `get_wanted()` merges `wanted/missing` (→ `gap_type="missing"`) + `wanted/cutoff`
  (→ `gap_type="cutoff"`).
- `_map(rec)` → `GapItem`: `arr_id=str(rec["id"])`, `foreign_id=rec.get("foreignAlbumId")`,
  `quality_profile_id=rec.get("profileId")`, `artist_or_author=rec.get("artist",{}).get("artistName")`.

**Error handling:** `r.raise_for_status()` inside the paging loop (Lidarr is primary — a hard fault
is allowed to surface; it is NOT wrapped in a breaker, unlike Readarr).

---

### `app/adapters/readarr.py` (NEW — adapter / service, defensive request-response)

**Analog:** NONE. New pattern. Structurally identical to `lidarr.py` (same paged GET) **except**:
`includeAuthor` instead of `includeArtist`; `kind="book"`; defensive `_map()` that returns `None`
on a missing `id`/garbage record (skip + `log.warning`, never raise) — RESEARCH lines 392-419. The
`_paged()` here **swallows httpx errors → returns `[]`** so a Readarr fault never propagates. Field
guesses (`foreignBookId`, `qualityProfileId` or `profileId`) are best-effort (A-R1/A-R2 — a wrong
guess skips a book, never crashes).

---

### `app/adapters/breaker.py` (NEW — utility, fault isolation)

**Analog:** NONE. New pattern. ~30-line hand-rolled circuit breaker wrapping the `ReadarrAdapter`
(RESEARCH lines 421-432 + "Alternatives Considered" — a library is unnecessary at homelab scale).
`get_wanted()` returns `[]` when open or on any exception; counts consecutive failures
(`fail_threshold=3`). This is what makes "books never gate music" structural rather than hoped-for.

---

### `app/core/gap_detector.py` (NEW — core orchestrator, batch)

**Analog:** NONE. New pattern. Trivial loop (RESEARCH lines 435-443): iterate
`[lidarr, readarr_breaker]` independently, `repo.upsert_gap(it)` per item, return per-app counts.
**Firewall rule (PITFALL #6):** this module imports `GapItem` and the adapter Protocol ONLY — it
must contain ZERO *arr field names (`records[`, `X-Api-Key`, `foreignAlbumId`). Enforced by the
ARR-01 grep test (RESEARCH line 767). Optionally exposes a `python -m` one-shot entrypoint for
manual UAT (RESEARCH Open Question 4) — NOT a scheduled loop (that's Phase 5).

---

### `app/tests/*.py` (NEW — tests; strong convention analog)

**Analog:** `app/tests/test_health.py` (the only existing test — copy its conventions exactly).

**Test-file conventions to replicate** (`app/tests/test_health.py:1-14`):
```python
"""Phase-1 unit coverage for the Curator FastAPI stub."""   # module docstring stating scope
import os
from fastapi.testclient import TestClient
from main import app                  # FLAT import (pythonpath=["app"]) — NOT `from app.main import`

client = TestClient(app)              # module-level fixtures/clients

def test_healthz_returns_ok():        # test_<behavior> naming, no class, plain asserts
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "phase": 1}
```
**Env-override convention** (`test_health.py:28` — use this for *arr URL/key in adapter tests):
```python
def test_readyz_reflects_slskd_url_env(monkeypatch):
    monkeypatch.setenv("SLSKD_URL", "http://gluetun:5030")
```
Replicate: module docstring; flat `from <module> import` (e.g. `from state.repo import upsert_gap`,
`from adapters.lidarr import LidarrAdapter`); module-level client/fixtures; snake-case
`test_<behavior>` functions; plain `assert`; `monkeypatch` for env; `set(body) == {...}` for
dict-shape checks. **New for Phase 2:** mock httpx with `respx` (no live *arr — sandbox is offline);
SQLite tests use a `tmp_path` DB file and reconnect to prove restart-durability. Map every test to
the RESEARCH "Phase Requirements → Test Map" (lines 759-768).

---

## Shared Patterns

### Import Root (`app/` IS the package root — the #1 Phase-2 convention)
**Source:** `pyproject.toml` (`pythonpath=["app"]`), `Dockerfile` (`WORKDIR /app`, `COPY app/ .`),
`app/tests/test_health.py:6` (`from main import app`).
**Apply to:** ALL new modules and tests.
```python
# CORRECT (matches Dockerfile + pyproject pythonpath):
from config import settings
from state.repo import upsert_gap
from adapters.base import GapItem, ArrAdapter
# WRONG — breaks the image layout:
from app.config import settings
```
New packages (`app/adapters/`, `app/state/`, `app/core/`) each need an `__init__.py` (none exist
yet — this is a new convention). Do NOT add a second top-level `curator/` package (RESEARCH line 236).

### Module house style
**Source:** `app/main.py:1-9`.
**Apply to:** all new modules.
- A top-of-file `#` purpose comment.
- stdlib imports → blank line → third-party imports.
- Full type hints on signatures + return types (`def healthz():` returns a typed dict;
  Phase-2 funcs use `-> sqlite3.Connection`, `-> list[GapItem]`, `-> None`).
- Module-level singletons after imports (`app`, `DATA` → `settings`, `MIGRATIONS`).
- Short docstrings on handlers/functions stating the *why*.

### Env / secrets surfacing
**Source:** `docker-compose.yml` `curator.environment:` + `.env.example` + `app/main.py:25`.
**Apply to:** `config.py`, both adapters, `main.py` startup hook.
- Reuse the EXISTING env var names verbatim: `LIDARR_URL`, `LIDARR_API_KEY`, `READARR_URL`,
  `READARR_API_KEY` (already in compose + `.env.example`).
- ADD `DB_PATH` (default `/db/curator.sqlite`) to `.env.example` AND the compose `curator.environment:`
  block, AND add a `/volume1/docker/curator/db:/db` bind-mount (its own mount, NOT under `/data`).
- Secrets are env-only, never logged, never baked (`.env` gitignored, Phase 1).

### Dependency pinning
**Source:** `app/requirements.txt` (`fastapi==0.115.6`, `uvicorn[standard]==0.34.0`).
**Apply to:** the `requirements.txt` edit.
- Exact `==` pins, one per line. Append `httpx==0.28.1` (verify the exact 0.28.x on first CI build —
  RESEARCH "Standard Stack" + Package Audit). `respx` is dev/test only — keep out of the runtime
  `requirements.txt` (or a separate dev list); `pytest` is already used in Phase 1.
- Do NOT add `pyarr`/`slskd-api`/`apscheduler`/`apprise` (scope creep — PITFALL #5).

### Parameterized SQL (security)
**Source:** RESEARCH "Security Domain" V5 + PITFALL `SQL injection` row.
**Apply to:** `repo.py`, `db.py`.
- All data queries use `?` / named placeholders — NEVER f-string interpolation. The ONLY permitted
  f-string-into-SQL is `PRAGMA user_version = {i}` in the migration runner (loop-controlled int).

---

## No Analog Found

These have no behavioral analog in the Phase-1 codebase — the planner should follow the RESEARCH
patterns (cited inline above) rather than forcing a poor analog. They DO follow the codebase's
*conventions* (import root, type hints, module house style, test layout).

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `app/state/db.py` | persistence | file-I/O | No SQLite/`sqlite3` anywhere in the codebase |
| `app/state/schema.sql` | migration | file-I/O | No DB / DDL exists |
| `app/state/repo.py` | DAO | CRUD | No persistence layer exists |
| `app/adapters/base.py` | model/interface | transform | No `Protocol`/dataclass exists (stub is plain dicts) |
| `app/adapters/lidarr.py` | adapter | request-response | No `httpx` / outbound HTTP client exists |
| `app/adapters/readarr.py` | adapter | request-response | No HTTP client / defensive-parsing pattern exists |
| `app/adapters/breaker.py` | utility | event-driven | No fault-isolation pattern exists |
| `app/core/gap_detector.py` | orchestrator | batch | No core orchestration exists |
| `app/tests/conftest.py` | test fixtures | — | No `conftest.py` exists yet (tests are self-contained) |
| `app/tests/fixtures/*.json` | test data | — | No fixture directory / recorded responses exist |

**Recommended conventions for the no-analog files are specified inline in each Pattern Assignment
above, drawn from RESEARCH's verified patterns and kept consistent with the Phase-1 module/test style.**

---

## Metadata

**Analog search scope:** entire repo — `app/` (3 files), `pyproject.toml`, `Dockerfile`,
`docker-compose.yml`, `.env.example`. No `.claude/skills/` directory exists.
**Files scanned:** 7 (the complete non-planning source surface).
**Pattern extraction date:** 2026-05-30
