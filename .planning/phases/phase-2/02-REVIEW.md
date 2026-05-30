---
phase: phase-2
reviewed: 2026-05-30T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - app/config.py
  - app/state/db.py
  - app/state/repo.py
  - app/state/schema.sql
  - app/adapters/base.py
  - app/adapters/lidarr.py
  - app/adapters/readarr.py
  - app/adapters/breaker.py
  - app/core/gap_detector.py
  - app/main.py
  - app/tests/conftest.py
  - app/tests/test_state_repo.py
  - app/tests/test_lidarr_adapter.py
  - app/tests/test_readarr_adapter.py
  - app/tests/test_adapter_protocol.py
  - app/tests/test_gap_detector.py
  - docker-compose.yml
  - .env.example
findings:
  critical: 2
  blocker: 2
  warning: 6
  info: 4
  total: 12
status: issues_found
---

# Phase 2: Code Review Report

**Reviewed:** 2026-05-30
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

Phase 2 delivers the SQLite-WAL ledger, the *-arr adapter seam, the circuit breaker, and the
gap detector. The load-bearing correctness requirements are largely met and well-tested:

- **STATE-02 dedup / status preservation is CORRECT.** The `ON CONFLICT(arr_app, arr_id) DO UPDATE`
  SET clause in `repo.py` genuinely omits `status` and `discovered_at` (lines 42-49); tests
  `test_upsert_preserves_status` and `test_dedup_preserves_status_end_to_end` prove it.
- **SQL injection: clean.** All data queries are `?`-parameterized; the only string-built SQL is
  the integer `PRAGMA user_version = {i}` bump, which is loop-controlled and never user input.
- **ARR-02 graceful degradation works.** Readarr `_paged` swallows faults to `[]`, `_map` skips bad
  records, and the breaker swallows any exception to `[]`. The detector iterates adapters
  independently. Tests cover empty/garbage/5xx/breaker-open.
- **Secrets hygiene: good.** API keys come from env, are not logged, and `.env` is gitignored.
- **SSRF-via-redirect is mitigated by default** — httpx does NOT follow redirects unless
  `follow_redirects=True`, which is never set.

However, there are two correctness BLOCKERs (an infinite-loop hang on the primary Lidarr path and a
leaked/discarded DB connection at startup that also defeats the intended single-writer model), two
Critical items (None API-key blowup and leaked httpx clients), and several warnings. The test suite
is strong on the happy paths but does not exercise the malformed-pagination or unset-key failure
modes that the BLOCKER/Critical findings concern.

## Critical Issues

### BL-01: Unbounded pagination loop can hang the primary Lidarr detection path

**File:** `app/adapters/lidarr.py:36-55` (and identically `app/adapters/readarr.py:42-61`)
**Issue:** The paging loop terminates only when
`page * body.get("pageSize", 100) >= body.get("totalRecords", 0)`. The loop trusts the server's
self-reported `pageSize`/`totalRecords` and never checks whether the current page actually returned
any records. If a misbehaving or compromised *arr (or a reverse proxy) returns `"pageSize": 0` with
a non-zero `totalRecords`, the termination test is `page * 0 >= totalRecords` → always false → the
loop spins forever, re-requesting page after page and accumulating nothing (or, with a constant
non-empty body, growing `records` without bound). The same shape occurs if `totalRecords` is
reported larger than the data actually paged (server bug) while pages keep returning `[]`.

For Lidarr this is the PRIMARY path and is deliberately NOT breaker-wrapped, so the entire detection
run hangs (or OOMs). For Readarr the loop is inside the try/except, but `except` never fires on a
hang — the breaker only opens on a raised exception, not on a spin.

**Fix:** Break when a page returns no records, and/or bound by a sane max page count:
```python
records, page = [], 1
while True:
    r = self._client.get(...); r.raise_for_status()
    body = r.json()
    batch = body.get("records", [])
    records += batch
    page_size = body.get("pageSize") or 100      # treat 0/None as 100
    if not batch or page * page_size >= body.get("totalRecords", 0):
        break
    page += 1
```

### BL-02: Startup migration leaks its connection and discards it; no app-lifetime connection is kept

**File:** `app/main.py:16-19`
**Issue:** `_startup()` calls `run_migrations(connect(settings.db_path))`. The `connect()` result is
never bound, never stored on `app.state`, and never closed. Consequences:

1. The connection is leaked on every startup (held until GC; with WAL this also orphans `-wal`/`-shm`
   handling expectations).
2. There is no single, long-lived writer connection retained for the application to use — the phase
   context explicitly calls out a "single writer" model under FastAPI, but no connection survives
   startup. Any later request handler would have to open its own connection, undercutting the
   single-writer design and WAL checkpoint expectations.
3. Because the connection is opened only to migrate and then dropped, a WAL checkpoint may not be
   flushed deterministically before the handle is GC'd.

**Fix:** Open one connection, run migrations, and retain it (and close it on shutdown):
```python
@app.on_event("startup")
def _startup() -> None:
    conn = connect(settings.db_path)
    run_migrations(conn)
    app.state.db = conn

@app.on_event("shutdown")
def _shutdown() -> None:
    conn = getattr(app.state, "db", None)
    if conn is not None:
        conn.close()
```
(If a connection-per-request model is intended instead, that should be explicit and documented; as
written it is neither — the connection simply leaks.)

### CR-01: `None` API key produces an invalid header / hard crash when an *arr key is unset

**File:** `app/adapters/lidarr.py:24-27`, `app/adapters/readarr.py:28-31`, `app/config.py:19-21`
**Issue:** `config.Settings` types `lidarr_api_key`/`readarr_api_key` as `Optional[str]` and defaults
them to `os.getenv(...)` → `None` when the env var is unset. The adapter constructors annotate
`api_key: str` but do not validate, and build `self._headers = {"X-Api-Key": api_key}`. When the key
is `None`, httpx will raise a `TypeError`/header-encoding error on the first request. For Lidarr
(primary, not breaker-wrapped) this surfaces as an unhandled exception that aborts the whole
detection run; the failure mode is opaque (header error, not a clear "missing LIDARR_API_KEY").
A frozen-dataclass field defaulting to `None` for a value the constructor declares non-optional is a
type/contract mismatch.

**Fix:** Fail fast with a clear message at construction (or in `build_adapters`):
```python
def __init__(self, base_url: str, api_key: str, client: httpx.Client):
    if not api_key:
        raise ValueError("LIDARR_API_KEY is required")
    ...
```
Readarr may instead degrade to a disabled adapter (return `[]`) rather than raise, to honor ARR-02.

### CR-02: `httpx.Client` instances created in `build_adapters()` are never closed (resource leak)

**File:** `app/core/gap_detector.py:53-57`
**Issue:** `build_adapters()` constructs two `httpx.Client()` objects and hands them to the adapters,
but nothing ever calls `.close()` on them. Each detection invocation that builds adapters leaks a
client (and its connection pool / sockets). The `__main__` one-shot leaks two on every run; if
`build_adapters()` is later called per scheduled cycle (Phase 5), this becomes a steady socket/FD
leak. The phase context explicitly flags "httpx client lifecycle (closed properly)" as a concern.

**Fix:** Own the client lifecycle — e.g. return the clients alongside the adapters and close them in
the caller, or use a context manager:
```python
def build_adapters():
    import httpx
    clients = []
    def _mk():
        c = httpx.Client(); clients.append(c); return c
    ...
    return adapters, clients  # caller closes; or wrap detection in `with`/try-finally
```
And in `__main__`, wrap the run in `try/finally` that closes every client.

## Warnings

### WR-01: Config env vars are captured at import time, not at instantiation

**File:** `app/config.py:18-26`
**Issue:** The `os.getenv(...)` calls are dataclass field defaults, evaluated once when the module is
imported (class definition time), then `settings = Settings()` reuses those captured defaults.
Verified empirically: changing the env var after import does not change `Settings().value`. This is
fragile for tests (cannot `monkeypatch.setenv` then re-read), and any importer that loads `config`
before the environment is fully populated silently bakes in defaults. It also means
`Settings(lidarr_url="...")` overrides work, but the documented "env → typed Settings" contract is
really "env-at-first-import → Settings".

**Fix:** Read env inside a factory or `__post_init__`/classmethod rather than as field defaults:
```python
@classmethod
def from_env(cls) -> "Settings":
    return cls(
        lidarr_url=os.getenv("LIDARR_URL", "http://lidarr:8686"),
        lidarr_api_key=os.getenv("LIDARR_API_KEY"),
        ...
    )
settings = Settings.from_env()
```

### WR-02: Migration schema-apply and `user_version` bump are not atomic

**File:** `app/state/db.py:48-52`
**Issue:** The connection is in autocommit mode (`isolation_level=None`). `executescript()` issues an
implicit COMMIT, and the `PRAGMA user_version = {i}` bump is a separate statement. If the process is
killed between the two, the DDL is applied but `user_version` stays behind, so the next boot re-runs
the migration. For 0001 this is safe because every statement is `IF NOT EXISTS` (idempotent), but the
pattern is unsafe for any future migration containing non-idempotent DML/DDL (e.g. an `ALTER TABLE`
or data backfill), which the comment invites future phases to append.

**Fix:** Wrap each migration + its version bump in an explicit transaction so they commit together:
```python
for i, (_, sql) in enumerate(MIGRATIONS, start=1):
    if i > have:
        conn.execute("BEGIN;")
        conn.executescript(sql)            # note: executescript COMMITs implicitly — see below
        conn.execute(f"PRAGMA user_version = {i};")
        conn.execute("COMMIT;")
```
Note `executescript` auto-commits, defeating a wrapping BEGIN; prefer splitting the .sql into
statements and `conn.execute()`-ing them inside one transaction, or document the idempotency
requirement as a hard rule for every future migration.

### WR-03: Lidarr `_map` will raise `KeyError` on a record missing `id`, aborting the primary run

**File:** `app/adapters/lidarr.py:69`
**Issue:** `arr_id=str(rec["id"])` uses subscript access. A malformed Lidarr record without `id`
raises `KeyError`, and since Lidarr is not breaker-wrapped this propagates and aborts the entire
detection pass (taking Readarr down with it if Lidarr runs first, depending on adapter order). While
Lidarr is "primary" and a hard fault is by-design allowed to surface, a single bad record in an
otherwise-good page should arguably not nuke the whole run. Contrast Readarr, which defensively
skips such records.

**Fix:** At minimum guard `id` (skip + log a malformed record) even on the primary path, or document
that a malformed Lidarr record is intended to be fatal. A defensive `if rec.get("id") is None: skip`
mirrors Readarr and is cheap.

### WR-04: Breaker never recovers — open state is permanent until process restart

**File:** `app/adapters/breaker.py:31-49`
**Issue:** `_open()` returns `True` once `self._failures >= fail_threshold`, and the open branch in
`get_wanted()` returns `[]` WITHOUT attempting the inner call — so `self._failures` can never be
reset while open. There is no half-open / cooldown / time-based reset. Once Readarr trips the
breaker, books are disabled for the entire lifetime of the process even after Readarr fully recovers;
only a container restart re-enables them. The class docstring implies recovery ("a success resets the
failure count") but a success is unreachable once open.

**Fix:** Add a cooldown that permits a trial call after some elapsed time (half-open), e.g. track
`_opened_at` and, when `now - _opened_at > reset_after`, attempt one inner call; success closes the
breaker, failure re-arms the timer.

### WR-05: Readarr `_paged` catches `KeyError` but the loop body has no `[...]` access that raises it

**File:** `app/adapters/readarr.py:62`
**Issue:** Minor robustness/coupling smell: the `except (httpx.HTTPError, ValueError, TypeError,
KeyError)` includes `KeyError`, but the loop body uses only `.get(...)` (which never raises KeyError)
and `r.json()`/`raise_for_status()`. The catch is harmless but signals the author wasn't certain
which exceptions arise. More importantly, `TypeError` here could mask genuine programming bugs (e.g.
a `None` arithmetic on `page * body.get("pageSize")` if `pageSize` came back as a non-numeric) by
silently degrading to `[]` instead of surfacing — acceptable for ARR-02 but worth a comment
narrowing intent.

**Fix:** Either drop `KeyError` or document precisely which call can raise each caught type; consider
catching `Exception` deliberately (as the breaker does) if the intent is truly "any fault → []".

### WR-06: `synchronous=NORMAL` + WAL has a known durability caveat on power loss

**File:** `app/state/db.py:34`
**Issue:** `PRAGMA synchronous=NORMAL` with WAL is the recommended throughput/durability balance, but
it carries a documented risk: the last committed transactions can be lost (though the DB stays
consistent) on an OS crash or power loss, because WAL frames may not be fsync'd at each commit. For a
Synology homelab gap-filler this is an acceptable trade-off, but the inline comment claims "good
durability" without noting the power-loss window. Since the ledger drives "no redundant downloads,"
losing a just-written `status=imported` on a hard power cut could re-trigger acquisition.

**Fix:** Either accept and document the window explicitly, or use `synchronous=FULL` for the writer
connection if redundant-download avoidance is deemed more important than write throughput on this
low-volume workload.

## Info

### IN-01: `unused import` in main.py would be flagged, but `os` is used — verify lint config

**File:** `app/main.py:4-5`
**Issue:** `import os` and `from pathlib import Path` are both used (`os.access`, `os.getenv`,
`Path`). No dead import here — noting only that `healthz` returns `"phase": 1` while the app version
is `0.2.0-phase2`; the hardcoded `1` is stale from Phase 1 and misreports the running phase.
**Fix:** Return `"phase": 2` (or derive from `app.version`) so the health payload is accurate.

### IN-02: `readyz` mounts `/data` read-only in compose but reports `data_readable` only

**File:** `app/main.py:28-35`, `docker-compose.yml:85`
**Issue:** Compose mounts `/volume1/data:/data:ro` (read-only), and `readyz` reports `R_OK`. This is
consistent for Phase 1/2, but Phases 4-6 require WRITE access to `/data` for staging/hardlinks
(per CLAUDE.md "single /data mount, atomic hardlinks"). The `:ro` mount will break those phases.
**Fix:** Track as a known Phase-4 prerequisite — `/data` must become read-write before staging lands.
Not a Phase-2 defect, flagged so it isn't forgotten.

### IN-03: `GapItem.raw` stores the entire *arr record as JSON in every ledger row

**File:** `app/state/repo.py:64`, `app/adapters/lidarr.py:76`
**Issue:** `raw=rec` plus `json.dumps(item.raw)` persists the full *arr payload (including artist
sub-object) into `raw_json` for every item, on every re-detect (the upsert refreshes `raw_json`).
This is intended provenance for later phases, but it is unbounded in size and refreshed on each pass.
Not a correctness bug; flagged as a storage/quality note.
**Fix:** Acceptable for now; consider pruning `raw` to the fields later phases actually mine, or
documenting expected row sizes.

### IN-04: `build_adapters()` lazy imports duplicate the module-level firewall imports

**File:** `app/core/gap_detector.py:46-51`
**Issue:** `httpx`, `CircuitBreaker`, `LidarrAdapter`, `ReadarrAdapter`, and `config.settings` are
imported lazily inside `build_adapters()` to keep the module importable in the offline 3.9 sandbox.
Reasonable, but it means import errors (e.g. missing httpx) surface only at call time rather than at
module load, and static analyzers can't see the dependency graph. The top-of-file
`from adapters.base import ArrAdapter, GapItem  # noqa: F401` imports `GapItem` but the module never
uses it directly (only `ArrAdapter` in the type hint) — `GapItem` is unused here.
**Fix:** Drop the unused `GapItem` from the gap_detector import (keep `ArrAdapter`); the `# noqa`
acknowledges it's unused, which is the smell.

---

_Reviewed: 2026-05-30_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
</content>
</invoke>
