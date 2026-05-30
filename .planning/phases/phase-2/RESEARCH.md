# Phase 2: State Ledger + *arr Adapter + Gap Detection - Research

**Researched:** 2026-05-30
**Domain:** SQLite (WAL) persistence under FastAPI on a Synology bind-mount; a `*-arr`-agnostic adapter over the Servarr v1 REST API (Lidarr music, Readarr books); monitored missing + cutoff-unmet gap detection with dedup on stable *arr identity
**Confidence:** HIGH on the four *arr endpoint routes + query params (verified from Lidarr/Readarr controller source this session), the dedup-key shape, and the SQLite-WAL access pattern. MEDIUM on exact identity field names inside each *arr resource (verified `foreignAlbumId`/`artistId`/`profileId` from `AlbumResource.cs`; Readarr's `BookResource` shape not fully enumerable without the live API). MEDIUM on pyarr coverage of every endpoint (recommend raw-`httpx` as the primary, pyarr optional).

> **Verification status (this session):** The route paths and query params for ALL FOUR endpoints
> were read directly from the Lidarr/Readarr controller source on the `develop` branch:
> `wanted/missing` and `wanted/cutoff` on both apps, all `[V1ApiController(...)]` → `/api/v1/...`,
> all accept `page`/`pageSize`/`sortKey`/`sortDirection` + `monitored` (default true) + an
> `includeArtist`/`includeAuthor` flag. Lidarr `AlbumResource` fields (`foreignAlbumId`,
> `artistId`, `profileId`, `monitored`, `anyReleaseOk`) verified from `AlbumResource.cs`. pyarr
> 6.6.0 requires Python ≥3.12 (matches CLAUDE.md). The dev sandbox is **Python 3.9 + offline**
> (confirmed: `python3 --version` = 3.9.6, `curl pypi.org` = no net) — exactly as Phase 1 flagged,
> so all Python testing is fixture-based and the real test run happens in CI/on the NAS.

---

<user_constraints>
## User Constraints (from CLAUDE.md + PROJECT.md + ROADMAP.md + REQUIREMENTS.md)

> No phase-level CONTEXT.md exists (standalone research before `/gsd:discuss-phase`). Constraints
> below are extracted verbatim from project-level governance docs and are **binding** — research
> does not explore alternatives to these.

### Locked Decisions
- **Build slskd-direct, NOT Soularr** — Curator owns the loop. (CLAUDE.md Key Decisions)
- **v1 = music (Lidarr, primary) + books (Readarr, best-effort), isolated behind a `*-arr`-agnostic adapter** so Readarr's retired/unmaintained status (or a future replacement) can never break the music path. **Music must work end-to-end before the books adapter is layered in.** (REQUIREMENTS.md)
- **State store is SQLite (WAL).** (CLAUDE.md Persistence; STATE-01)
- **Runtime is Python 3.12 / FastAPI.** (CLAUDE.md Key Decisions / Stack)
- **Defer to Lidarr/Readarr quality profiles/cutoffs; never downgrade.** Phase 2 only *reads and stores* the profile/cutoff identity — it does NOT make quality decisions (that is Phase 3). (CLAUDE.md Quality)
- **Persistence on `/volume1` bind-mounts with correct PUID/PGID (1031/65536).** (CLAUDE.md; NAS-RECON.md)
- **Dedup keyed on stable `*arr` identity — never re-track or re-download a satisfied/in-flight item.** (STATE-02)
- **Curator runs on `synobridge`, reaches Lidarr at `http://lidarr:8686` and Readarr at `http://readarr:8787` by container name** (NOT via the VPN tunnel). (CLAUDE.md Networking; INFRA-03; NAS-RECON.md)
- **Auth to the *arr is the `X-Api-Key` header** with the per-app key from `.env` (`LIDARR_API_KEY`, `READARR_API_KEY`). (Phase 1 compose)
- **Secrets never baked into the image; runtime env/`.env` only.** (CLAUDE.md Deploy)
- **Phase 2 builds ON the existing Phase-1 FastAPI codebase** (`app/main.py` health stub, `python:3.12-slim` Dockerfile, `app/` as WORKDIR). Do not restructure away from this. (01-03-PLAN.md)

### Claude's Discretion
- The SQLite access layer (stdlib `sqlite3` vs SQLModel/SQLAlchemy) — recommended below.
- Migration/schema-versioning mechanism — recommended below.
- The internal `GapItem`/adapter `Protocol` shape — recommended below.
- Whether to use `pyarr` or raw `httpx` for the *arr calls — recommended below.
- The exact lifecycle status enum names — recommended below (must cover STATE-01's listed states).

### Deferred Ideas (OUT OF SCOPE for Phase 2 — DO NOT research or plan)
- **Matching / candidate scoring** (MATCH-01/02) → Phase 3.
- **Quality *decisions* / fake-FLAC heuristics** (QUAL-01/02/03) → Phase 3. Phase 2 stores the profile/cutoff but does not act on it.
- **slskd search / download / transfer** (ACQ-01/02/03) → Phase 4. No slskd client in Phase 2.
- **Staging / Manual Import / auto-purge** (IMPORT-01..05) → Phase 4.
- **Daemon scheduling, grace window, Usenet-fallback gate, backoff, do-not-retry** (GAP-03, STATE-03, REL-01/02/03) → Phase 5. Phase 2 gap detection is a *callable function*, not yet a scheduled loop.
- **Status endpoint / Apprise notifications** (OBS-01/02) → Phase 6.
- **Enabling the Readarr branch in production.** The adapter seam + a `ReadarrAdapter` (and its graceful-degradation behavior) ARE in Phase 2 scope; *enabling books to actually run* is gated on a solid music loop. Phase 2 builds and unit-tests the seam; it does not require a live Readarr.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STATE-01 | Persist each tracked item's lifecycle status (pending/searching/grabbed/downloaded/imported/unavailable/blacklisted) in SQLite (WAL) — the spine | Lifecycle State Model + State Schema sections; WAL-on-bind-mount section |
| STATE-02 | Never re-download an item already satisfied or in-flight (dedup keyed on stable *arr identity) | Stable Identity Key section (`UNIQUE(arr_app, arr_id)` upsert); dedup proof in Validation Architecture |
| ARR-01 | One `*-arr`-agnostic adapter interface (wanted/missing, cutoff, records, command/import) so Readarr is pluggable and never couples to the core | Adapter Seam Design (Protocol + LidarrAdapter/ReadarrAdapter); the Phase-2 subset of the interface |
| ARR-02 | Adapter exposes identity + quality profile/cutoff uniformly; defends against missing/empty Readarr metadata (degrade gracefully); books best-effort without gating music | Adapter Seam Design (uniform `GapItem`); Readarr graceful-degradation pattern + circuit-breaker; Pitfall: Readarr metadata gaps |
| GAP-01 | Detect monitored missing items (wanted/missing) via the adapter | Lidarr/Readarr API Reference (`GET /api/v1/wanted/missing`, verified) |
| GAP-02 | Detect monitored cutoff-unmet items (wanted/cutoff) via the adapter | Lidarr/Readarr API Reference (`GET /api/v1/wanted/cutoff`, verified) |
</phase_requirements>

---

## Summary

Phase 2 builds the **persistent spine** and the **integration seam** with zero acquisition. Two
deliverables: (1) a SQLite-WAL ledger that is the source of truth for "should I act on this gap?"
keyed on stable *arr identity, and (2) a `*-arr`-agnostic adapter that reads monitored
**missing** and **cutoff-unmet** items from Lidarr (primary) and Readarr (best-effort, isolated)
and upserts them into the ledger deduped.

The single most load-bearing finding — **verified from controller source this session** — is that
both apps expose an **identical Servarr v1 surface** for gap detection:
`GET /api/v1/wanted/missing` and `GET /api/v1/wanted/cutoff`, each accepting
`page`/`pageSize`/`sortKey`/`sortDirection`, `monitored` (default `true`), and an
`includeArtist` (Lidarr) / `includeAuthor` (Readarr) flag, authed with `X-Api-Key`. Both return a
paged envelope `{ page, pageSize, sortKey, sortDirection, totalRecords, records: [...] }`. Lidarr
`records` are `AlbumResource`s carrying `id`, `foreignAlbumId` (the MusicBrainz release-group ID),
`artistId`, `monitored`, `anyReleaseOk`, and **`profileId`** (the quality-profile id — note it is
`profileId` on the album, not `qualityProfileId`). Readarr `records` are `BookResource`s with the
analogous `id`/foreign-id/`authorId`/`monitored` shape. Because the two surfaces are isomorphic,
**one adapter `Protocol` cleanly covers both** — the only divergence is field names and Readarr's
fragility, both contained behind the adapter.

For persistence: **SQLite in WAL mode, single writer, via stdlib `sqlite3`** is the right call.
The DB file lives on its **own** bind-mount (`/volume1/docker/curator/db/`), **separate from the
shared `/data` tree**, on local Synology ext4/btrfs (not a network share) — WAL is safe there.
The dedup primitive is a `UNIQUE(arr_app, arr_id)` constraint with an `INSERT ... ON CONFLICT DO
UPDATE` upsert: re-running detection on an already-tracked item updates metadata in place and
**never creates a second row** (success criterion 4). Restart durability (criterion 1) is free —
state is a file on a bind-mount; just ensure a clean WAL checkpoint and idempotent schema
migrations on startup.

**Primary recommendation:** Build in this order — (Wave 0) test scaffolding + recorded *arr JSON
fixtures (the sandbox is offline, so fixtures are mandatory); (A) the SQLite layer: schema +
idempotent migration runner + WAL pragma + the dedup upsert repo, unit-tested for
restart-durability and dedup; (B) the adapter seam: `ArrAdapter` Protocol + `GapItem` model +
`LidarrAdapter` (raw `httpx`) mapping missing+cutoff records to `GapItem`s; (C) `ReadarrAdapter`
behind a circuit breaker / try-except shell that maps Readarr records and **degrades gracefully on
missing/empty metadata** (skip the book, log, never raise into the core); (D) wire detection →
upsert and prove dedup + graceful Readarr failure with fixtures. No live *arr is required to ship
Phase 2 — every behavior is provable against recorded fixtures, and a live smoke test against
`lidarr:8686` is the on-NAS confirmation.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Persistent gap ledger (source of truth) | `state/` (SQLite-WAL) | host bind-mount | Single mutable surface; survives restart as a file |
| Dedup on stable *arr identity | `state/repo.py` (UNIQUE constraint + upsert) | SQLite | Enforced at the DB layer, not in app logic — collision-proof |
| Talking to Lidarr/Readarr REST | `adapters/` (the *arr firewall) | `httpx` over synobridge | ONLY place *arr knowledge lives; core never imports *arr details |
| Mapping heterogeneous *arr records → uniform `GapItem` | `adapters/lidarr.py`, `adapters/readarr.py` | — | Each adapter owns its app's field names; core sees one model |
| Readarr fault isolation (best-effort, never gates music) | `adapters/breaker.py` + defensive parsing in `readarr.py` | — | A hung/garbage Readarr trips the breaker → book items parked, music flows |
| Gap detection (missing + cutoff enumeration) | `core/gap_detector.py` | adapter | Calls the adapter, upserts results; a callable function (NOT yet scheduled — that's Phase 5) |
| Reaching the *arr by container name | Curator (plain synobridge member) | synobridge DNS | Curator is NOT in the VPN netns; talks LAN directly (Phase 1 topology) |

---

## Standard Stack

### Core (Phase 2 additions to the existing Phase-1 app)
| Library | Version (verified 2026-05-30) | Purpose | Why Standard |
|---------|------------------------------|---------|--------------|
| Python stdlib `sqlite3` | bundled (Python 3.12) | SQLite-WAL ledger access | Zero deps, full WAL/pragma/transaction control, the right fit for single-writer; SQLite ships in CPython [VERIFIED: stdlib] |
| `httpx` | `0.28.x` (pin exact in Wave 0) | Async/sync HTTP to the *arr REST API | One consistent HTTP stack; the escape hatch the sibling research already standardizes on [CITED: STACK.md; pin-verify in Wave 0] |
| `pydantic` v2 | comes with FastAPI 0.115 (already installed) | `GapItem` / adapter DTOs, defensive parsing | Already in the image via FastAPI; pairs with the existing stub [VERIFIED: app/requirements.txt pins fastapi==0.115.6] |
| FastAPI / uvicorn | `0.115.6` / `0.34.0` (already pinned) | Existing app host; Phase 2 adds startup migration hook | Already shipped in Phase 1 [VERIFIED: app/requirements.txt] |

### Supporting (optional — recommend deferring)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pyarr` | `6.6.0` (requires Python ≥3.12) [VERIFIED: pyarr pyproject.toml `requires-python=">=3.12"`] | Typed Lidarr/Readarr client | OPTIONAL. Covers both apps, but the four Phase-2 endpoints are trivial raw GETs. Recommend **raw `httpx`** for Phase 2 (full control over the paged envelope + Readarr defensive parsing); adopt pyarr later only if its coverage clearly beats hand-rolled. If used, pin `6.6.0` and keep `httpx` fallbacks. |
| (no `slskd-api`, no `apscheduler`, no `apprise` in Phase 2) | — | — | Those belong to Phases 4/5/6. Do NOT add them now. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| **stdlib `sqlite3` (RECOMMENDED)** | SQLModel / SQLAlchemy 2.x | ORM adds a dependency + a Pydantic-v2 coupling and hides the WAL/pragma/transaction details you actually want explicit at this layer. For ~5 tables and a single writer, the ORM earns nothing in Phase 2. STACK.md *mentions* SQLModel, but for a hand-off-correctness spine, raw `sqlite3` with a thin `repo.py` is simpler to reason about and test. **If** the planner prefers typed models, SQLModel 0.0.22+ is acceptable — but keep the dedup as a DB-level `UNIQUE` constraint either way. |
| raw `httpx` for *arr | `pyarr` 6.6.0 | pyarr is fine and covers both apps, but the Phase-2 calls are four GETs against a stable, verified surface; raw `httpx` avoids a dependency whose Readarr coverage may lag and gives direct control over graceful degradation. |
| Circuit breaker library | hand-rolled try/except + a simple open/half-open counter in `breaker.py` | At single-process homelab scale a ~30-line breaker is clearer than a dependency; the *requirement* is "degrade gracefully," not "production-grade breaker." Either is acceptable. |

**Installation (Phase 2 additions — append to `app/requirements.txt`, pin in Wave 0):**
```bash
# httpx is the only new runtime dep; sqlite3 + pydantic are already present.
httpx==0.28.1        # verify latest 0.28.x at execute time
# dev/test only (already partly present from Phase 1):
pytest               # already used in Phase 1
respx==0.22.0        # httpx mock transport for fixture-based adapter tests (verify version)
```

**Version verification (run in Wave 0 when network is available — CI or NAS):**
```bash
pip index versions httpx        # confirm 0.28.x latest
pip index versions respx        # confirm latest; respx pins to an httpx range — check compat
pip index versions pyarr        # only if adopting pyarr; confirm 6.6.0 + requires-python
```

---

## Package Legitimacy Audit

> slopcheck could not be installed (sandbox is offline — `pip install` has no network; confirmed
> `curl pypi.org` returns no connection). Per protocol, because slopcheck did not run, the new
> packages are tagged `[ASSUMED]` and the planner SHOULD gate the `httpx`/`respx` install behind a
> `checkpoint:human-verify` (or simply confirm on first CI build, where the network exists).
> Mitigating facts: `httpx` is already referenced by the project's own sibling research (STACK.md)
> and is an extremely high-trust, widely-used package; `pyarr` and `slskd-api` versions were
> confirmed via web search against PyPI this session.

| Package | Registry | Notes | slopcheck | Disposition |
|---------|----------|-------|-----------|-------------|
| `httpx` | PyPI | encode/httpx — first-party, ubiquitous async HTTP client; 0.28.x current | not run (offline) | Approved — verify version on first CI build |
| `respx` | PyPI | lundberg/respx — standard httpx mock library; dev/test only | not run (offline) | Approved (dev-only) — verify version + httpx compat on first CI build |
| `pyarr` | PyPI | totaldebug/pyarr 6.6.0, `requires-python>=3.12` [VERIFIED via pyproject.toml + PyPI search] | not run (offline) | OPTIONAL — only if adopted; otherwise omit |
| `sqlite3`, `pydantic` | stdlib / existing | already in the image | n/a | Approved |

**Packages removed (SLOP):** none. **Flagged (SUS):** none. **Wave 0 gate:** confirm `httpx`/`respx`
versions on the first CI build (network present there); do not add `pyarr`/`slskd-api`/`apscheduler`/`apprise` in Phase 2.

---

## Architecture Patterns

### System Architecture Diagram (Phase 2 data flow)

```
                       synobridge (Curator is a plain member — NOT in the VPN netns)
   ┌───────────┐   X-Api-Key (LIDARR_API_KEY)    ┌────────────────────────────────────────────┐
   │  Lidarr   │◄────────────────────────────────│  curator (FastAPI process, Python 3.12)     │
   │  :8686    │   GET /api/v1/wanted/missing      │                                            │
   └───────────┘   GET /api/v1/wanted/cutoff       │  ┌──────────────────────────────────────┐  │
                   (paged: page/pageSize)          │  │ core/gap_detector.py                  │  │
   ┌───────────┐   X-Api-Key (READARR_API_KEY)     │  │  for adapter in [lidarr, readarr]:     │  │
   │  Readarr  │◄────────────────────────────────│  │    for item in adapter.get_wanted():   │  │
   │  :8787    │   (same routes; books)            │  │       repo.upsert_gap(item)  ───────┐  │  │
   │ (best-eff)│                                   │  └──────────────────────────────────│──┘  │
   └───────────┘                                   │         ▲ (Protocol calls only)      │     │
                                                   │  ┌──────┴───────────────────────┐    ▼     │
                                                   │  │ adapters/ (the *arr firewall) │  ┌─────────────┐
   record envelope:                                │  │  base.py  ArrAdapter Protocol │  │ state/repo.py│
   { page, pageSize, totalRecords,                 │  │  lidarr.py  LidarrAdapter     │  │  upsert via  │
     records:[ AlbumResource | BookResource ] }    │  │  readarr.py ReadarrAdapter ───┼──┤  ON CONFLICT │
                                                   │  │  breaker.py (Readarr isolate) │  │  (arr_app,   │
   each record → mapped to a uniform GapItem ──────┼──┘                               │  │   arr_id)    │
   (arr_app, arr_id, kind, title, foreign_id,      │                                  │  └──────┬──────┘
    profile_id, gap_type∈{missing,cutoff}, raw)    │                                  ▼         │
                                                   │                       ┌─────────────────────▼────────┐
                                                   └───────────────────────│ SQLite WAL: curator.sqlite   │
                                                                           │  /volume1/docker/curator/db/ │
                                                                           │  (OWN mount, NOT under /data)│
                                                                           │  UNIQUE(arr_app, arr_id)     │
                                                                           └──────────────────────────────┘
```

Data-flow notes:
- Curator → *arr is plain LAN over synobridge by container name; **never** through the VPN tunnel (Phase 1 topology — only slskd is in the netns).
- `gap_detector` is the only caller of the adapters; adapters are the only importers of *arr field knowledge; `repo.upsert_gap` is the only writer of the ledger.
- A Readarr failure is caught inside `readarr.py` / `breaker.py` and surfaces as "0 book items this run" — the Lidarr loop is a separate iteration and is never affected.

### Recommended Project Structure (extends the existing `app/`)

> Phase 1 placed the app at `app/` with `app/` as the Docker WORKDIR and `main.py` importable as
> `main` (see `pyproject.toml` `pythonpath=["app"]`). Phase 2 adds packages **under `app/`** so the
> import root stays `app/`. Do NOT introduce a second top-level `curator/` package — it would break
> the existing `pythonpath` and Dockerfile `COPY app/ .`.

```
app/
├── main.py                 # existing stub; Phase 2 adds a startup hook: run_migrations() before serving
├── config.py               # NEW: pydantic-settings or os.getenv reads (LIDARR_URL/KEY, READARR_URL/KEY, DB_PATH)
├── adapters/               # THE *-ARR-AGNOSTIC SEAM (only place *arr knowledge lives)
│   ├── __init__.py
│   ├── base.py             # ArrAdapter Protocol + GapItem / QualityRef dataclasses (the uniform model)
│   ├── lidarr.py           # LidarrAdapter (music, primary) — httpx; maps AlbumResource → GapItem
│   ├── readarr.py          # ReadarrAdapter (books, best-effort) — defensive parsing of BookResource
│   └── breaker.py          # tiny circuit breaker wrapping ReadarrAdapter (Readarr fault isolation)
├── core/
│   └── gap_detector.py     # NEW: detect_gaps(adapters, repo) → upserts missing+cutoff GapItems
├── state/
│   ├── __init__.py
│   ├── db.py               # NEW: connect() w/ WAL pragma; run_migrations() (idempotent, versioned)
│   ├── schema.sql          # NEW: items table + indexes (or migrations/ dir of versioned .sql)
│   └── repo.py             # NEW: upsert_gap(), get_gap(), set_status(), list_by_status()
├── requirements.txt        # + httpx (pin)
└── tests/
    ├── test_health.py      # existing
    ├── fixtures/           # NEW: recorded *arr JSON (lidarr_missing.json, lidarr_cutoff.json,
    │                       #      readarr_missing.json, readarr_empty.json, readarr_garbage.json)
    ├── test_state_repo.py  # NEW: dedup + restart-durability + status transitions
    ├── test_lidarr_adapter.py   # NEW: fixture → GapItem mapping, paging
    └── test_readarr_adapter.py  # NEW: graceful degradation on empty/garbage metadata
```

### Pattern 1: `ArrAdapter` Protocol — the uniform seam (ARR-01, ARR-02)
**What:** Both apps implement one `Protocol`; the core only ever sees `GapItem`s. Phase 2 needs
ONLY the read/detection subset of the interface; the import/command methods are declared but may be
`...`-stubbed (Phase 4 implements them) so the seam shape is locked now.
**When to use:** Always — this is the requirement.
**Example:**
```python
# app/adapters/base.py
from dataclasses import dataclass, field
from typing import Protocol, Literal, Any

GapType = Literal["missing", "cutoff"]

@dataclass(frozen=True)
class GapItem:
    arr_app: Literal["lidarr", "readarr"]   # which adapter produced it
    arr_id: str                             # the *arr's own record id (stable per instance)
    kind: Literal["album", "book"]
    gap_type: GapType                        # missing | cutoff
    title: str | None
    artist_or_author: str | None
    foreign_id: str | None                   # MBID release-group (Lidarr) / foreign book id (Readarr)
    quality_profile_id: int | None           # AlbumResource.profileId (NOTE: 'profileId', not 'qualityProfileId')
    raw: dict[str, Any] = field(default_factory=dict)   # original record (provenance; later phases mine it)

class ArrAdapter(Protocol):
    app: str
    def get_wanted(self) -> list[GapItem]: ...           # Phase 2: missing + cutoff merged
    # --- declared now, IMPLEMENTED in later phases (keep the seam stable) ---
    def get_quality_profile(self, profile_id: int) -> dict: ...   # Phase 3
    def get_queue_status(self, item: GapItem) -> Any: ...         # Phase 5 (fallback-only race check)
    def manual_import_candidates(self, path: str) -> list: ...    # Phase 4
    def execute_import(self, decisions: list) -> None: ...        # Phase 4
    def verify_imported(self, item: GapItem) -> bool: ...         # Phase 4
```

### Pattern 2: Verified Servarr v1 paged GET (GAP-01, GAP-02)
**What:** Enumerate missing + cutoff with the verified routes + paging envelope.
**Example:**
```python
# app/adapters/lidarr.py  — raw httpx; Readarr is identical except field names + the include flag
import httpx

class LidarrAdapter:
    app = "lidarr"
    def __init__(self, base_url: str, api_key: str, client: httpx.Client):
        self._base = base_url.rstrip("/")
        self._client = client
        self._headers = {"X-Api-Key": api_key}     # [VERIFIED: Servarr v1 auth header]

    def _paged(self, path: str) -> list[dict]:
        # [VERIFIED from MissingController.cs / CutoffController.cs: page, pageSize, sortKey,
        #  sortDirection, monitored(default true), includeArtist(default false)]
        records, page = [], 1
        while True:
            r = self._client.get(
                f"{self._base}/api/v1/{path}",
                headers=self._headers,
                params={"page": page, "pageSize": 100,
                        "sortKey": "releaseDate", "sortDirection": "ascending",
                        "monitored": "true", "includeArtist": "true"},
                timeout=30.0,
            )
            r.raise_for_status()
            body = r.json()                          # { page, pageSize, totalRecords, records:[...] }
            records += body.get("records", [])
            if page * body.get("pageSize", 100) >= body.get("totalRecords", 0):
                break
            page += 1
        return records

    def get_wanted(self) -> list["GapItem"]:
        missing = [self._map(rec, "missing") for rec in self._paged("wanted/missing")]
        cutoff  = [self._map(rec, "cutoff")  for rec in self._paged("wanted/cutoff")]
        return missing + cutoff

    def _map(self, rec: dict, gap_type: str) -> "GapItem":
        # [VERIFIED AlbumResource fields: id, foreignAlbumId, artistId, title, monitored, profileId]
        artist = rec.get("artist") or {}
        return GapItem(
            arr_app="lidarr", arr_id=str(rec["id"]), kind="album", gap_type=gap_type,
            title=rec.get("title"),
            artist_or_author=artist.get("artistName"),
            foreign_id=rec.get("foreignAlbumId"),
            quality_profile_id=rec.get("profileId"),   # album-level profile id
            raw=rec,
        )
```

### Pattern 3: Dedup-on-identity upsert (STATE-02) — the success-criterion-4 primitive
**What:** Detection re-runs constantly; the ledger must never grow a duplicate row for the same
*arr item. Enforce at the DB layer with a `UNIQUE` constraint + `ON CONFLICT DO UPDATE`.
**Example:**
```python
# app/state/repo.py
def upsert_gap(conn, item: GapItem) -> None:
    conn.execute(
        """
        INSERT INTO items (arr_app, arr_id, kind, gap_type, title, artist_or_author,
                           foreign_id, quality_profile_id, status, discovered_at, last_seen_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', :now, :now, ?)
        ON CONFLICT(arr_app, arr_id) DO UPDATE SET
            gap_type           = excluded.gap_type,
            title              = excluded.title,
            artist_or_author   = excluded.artist_or_author,
            foreign_id         = excluded.foreign_id,
            quality_profile_id = excluded.quality_profile_id,
            last_seen_at       = excluded.last_seen_at,
            raw_json           = excluded.raw_json
        -- NOTE: do NOT overwrite `status` on conflict — a 'grabbed'/'imported'/'blacklisted'
        --       item that still shows in wanted/cutoff must keep its lifecycle status (STATE-02).
        """,
        (item.arr_app, item.arr_id, item.kind, item.gap_type, item.title,
         item.artist_or_author, item.foreign_id, item.quality_profile_id, json.dumps(item.raw)),
    )
```
**Critical subtlety:** the `ON CONFLICT` clause must **NOT** reset `status`. An item that has been
acted on (e.g. `imported`, or in-flight `searching`) will frequently still appear in `wanted/missing`
or `wanted/cutoff` (the *arr only clears it after *its* import). If detection overwrote `status` to
`pending`, Curator would re-act on a satisfied/in-flight item — the exact STATE-02 violation. Only
metadata + `last_seen_at` are refreshed on conflict.

### Pattern 4: Readarr graceful degradation (ARR-02) — books never gate music
**What:** A Readarr metadata gap, empty record, 5xx, or timeout must skip the offending book (log it)
and never raise into the core; a fully-down Readarr trips a breaker so the run doesn't even attempt it.
**Example:**
```python
# app/adapters/readarr.py — defensive mapping
def _map(self, rec: dict, gap_type: str) -> "GapItem | None":
    try:
        arr_id = rec.get("id")
        if arr_id is None:                       # garbage/empty record → skip, don't crash
            log.warning("readarr record missing id; skipping: %r", rec)
            return None
        author = rec.get("author") or {}
        return GapItem(
            arr_app="readarr", arr_id=str(arr_id), kind="book", gap_type=gap_type,
            title=rec.get("title"),
            artist_or_author=author.get("authorName"),
            foreign_id=rec.get("foreignBookId"),   # confirm exact key against live BookResource (A-R1)
            quality_profile_id=rec.get("qualityProfileId") or rec.get("profileId"),
            raw=rec,
        )
    except (KeyError, TypeError, ValueError) as e:
        log.warning("skipping malformed readarr record: %s", e)
        return None

def get_wanted(self) -> list["GapItem"]:
    out = []
    for gap_type in ("missing", "cutoff"):
        for rec in self._paged(f"wanted/{gap_type}"):   # _paged catches httpx errors → returns []
            mapped = self._map(rec, gap_type)
            if mapped is not None:
                out.append(mapped)
    return out

# app/adapters/breaker.py — wrap the ReadarrAdapter so a hard-down Readarr can't stall the loop
class CircuitBreaker:
    def __init__(self, inner, on_open=None, fail_threshold=3): ...
    def get_wanted(self):
        if self._open():            # too many recent failures → skip Readarr entirely this run
            return []
        try:
            return self._inner.get_wanted()
        except Exception as e:       # last-resort: a Readarr fault NEVER propagates to the core
            self._record_failure(e)
            return []
```
**Core loop stays trivially safe** because it iterates adapters independently:
```python
# app/core/gap_detector.py
def detect_gaps(adapters, repo) -> dict:
    counts = {}
    for adapter in adapters:                # [lidarr (primary), readarr (breaker-wrapped)]
        items = adapter.get_wanted()        # readarr returns [] on any fault — music unaffected
        for it in items:
            repo.upsert_gap(it)
        counts[adapter.app] = len(items)
    return counts
```

### Anti-Patterns to Avoid
- **Deriving "should I act?" from the *arr wanted list alone** — re-imports prior pain point #1 (redundant downloads). The SQLite ledger is the source of truth; the *arr list is just an input. [VERIFIED: PITFALLS #1]
- **Overwriting `status` on the dedup upsert** — silently re-acts on satisfied/in-flight items (STATE-02 violation). Refresh metadata only.
- **Keying dedup on free-text title** — titles mutate (editions, scene tags). Key on `(arr_app, arr_id)`; carry `foreign_id` (MBID/foreign book id) as the cross-instance/stable secondary. [VERIFIED: PITFALLS #1 "NOT free-text title which mutates"]
- **Putting the SQLite file under the shared `/data` tree** — `/data` is for media/staging/hardlinks; the DB belongs on its own mount (`/volume1/docker/curator/db/`). Mixing risks the DB being touched by *arr scans and complicates the hardlink invariant.
- **Calling Lidarr/Readarr through the same un-isolated code** — a Readarr fault would stall music. One Protocol, Readarr behind a breaker. [VERIFIED: ARCHITECTURE Anti-Pattern 6]
- **Using `pyarr`/`slskd-api`/`apscheduler` now** — scope creep into Phases 3-6. Phase 2 is persistence + adapter + detection only.
- **An async writer pool / multiple SQLite write connections** — single-writer is the design; concurrent writers invite `database is locked`. One connection writes; WAL lets reads run concurrently.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Dedup logic | App-side "have I seen this?" set/scan | DB `UNIQUE(arr_app, arr_id)` + `ON CONFLICT DO UPDATE` | Collision-proof, atomic, survives restart; app-side dedup races the writer |
| SQLite concurrency | A custom lock/queue | WAL mode + single writer connection | WAL gives concurrent readers + one writer for free; the design is single-writer anyway |
| *arr paging | Guessing offsets | The verified `{page,pageSize,totalRecords,records}` envelope loop | Servarr paging is standard and verified from source this session |
| *arr auth | Custom token scheme | `X-Api-Key` header | The Servarr v1 standard; already in Phase-1 `.env` |
| Config loading | Scattered `os.getenv` | One `config.py` (pydantic-settings or a typed dataclass) | Validated, single declarative place (PITFALLS #4 "one declarative place") |
| Readarr robustness | Assuming clean metadata | Defensive parsing returning `None` on bad records + a breaker | Readarr is unmaintained; ARR-02 demands graceful degradation [VERIFIED: PITFALLS #17] |
| Schema migrations | Ad-hoc `ALTER`s at runtime | A tiny versioned, idempotent migration runner (PRAGMA user_version) | Idempotent on container recreate; ARCHITECTURE specifies "idempotent SQL safe on recreate" |

**Key insight:** Phase 2's correctness lives at the DB boundary (the `UNIQUE` constraint and the
`ON CONFLICT` rule) and at the adapter boundary (defensive Readarr parsing). Both are
configuration/constraint-driven, not algorithmic — let SQLite and a thin Protocol do the work.

---

## Lifecycle State Model (STATE-01)

STATE-01 lists the required statuses. Recommended enum + transitions (Phase 2 only *creates* items
in `pending` and supports manual transition for tests; later phases drive the rest):

```
pending      -- discovered as a gap, not yet acted on (Phase 2 sets this on insert)
searching    -- an slskd search is in flight (Phase 4)
grabbed       -- a candidate chosen & download issued (Phase 4)
downloaded    -- transfer complete in staging, not yet imported (Phase 4)
imported      -- *arr confirmed import into /volume1 (Phase 4)
unavailable   -- exhausted/no source; dormant long-TTL re-check (Phase 5)
blacklisted   -- permanently skip (bad source / user denylist) (Phase 5)
```
Transitions (Phase 2 implements only the bold ones; the rest are declared for the schema):
```
(none) ──upsert──► **pending**
pending ──► searching ──► grabbed ──► downloaded ──► imported        (happy path; Phase 4)
   any non-terminal ──► unavailable | blacklisted                    (Phase 5)
```
**Phase-2 scope:** the `items.status` column with a `CHECK` constraint over this enum, default
`pending` on insert, and a `repo.set_status()` that the unit tests exercise to prove the column
persists and round-trips across a reconnect. Phase 2 does NOT implement the search→import
transitions (Phases 4-5) — but the column + enum exist now so later phases just write to it.

> **Note on STATE-01's terms vs. ARCHITECTURE.md's earlier enum:** ARCHITECTURE.md sketched
> `DISCOVERED/ELIGIBLE/SEARCHING/.../FILLED`. STATE-01 (the binding requirement) lists
> `pending/searching/grabbed/downloaded/imported/unavailable/blacklisted`. **Use the STATE-01
> terms** — they are the requirement. Map ARCHITECTURE's `DISCOVERED`→`pending`, `IMPORTED`→`imported`.
> The grace/eligible/backoff states (`ELIGIBLE`, `PAUSED`, `EXHAUSTED`) are Phase 5 concerns; do not
> add them to the Phase-2 enum.

---

## State Schema (SQLite + WAL) — Phase 2 scope

Phase 2 needs **one** table (`items`). The richer multi-table schema in ARCHITECTURE.md (`attempts`,
`staged_files`, `peers`, `share_stats`, `events`) belongs to Phases 4-6 — **do not build them now**;
the migration runner makes adding them later trivial.

```sql
-- migrations/0001_items.sql  (idempotent; gated by PRAGMA user_version)
CREATE TABLE IF NOT EXISTS items (
  id                 INTEGER PRIMARY KEY,
  arr_app            TEXT NOT NULL,                 -- 'lidarr' | 'readarr'
  arr_id             TEXT NOT NULL,                 -- the *arr's own record id (albumId / bookId)
  kind               TEXT NOT NULL,                 -- 'album' | 'book'
  gap_type           TEXT NOT NULL,                 -- 'missing' | 'cutoff'
  title              TEXT,
  artist_or_author   TEXT,
  foreign_id         TEXT,                          -- MBID release-group (Lidarr) / foreign book id (Readarr)
  quality_profile_id INTEGER,                       -- AlbumResource.profileId; stored, NOT acted on in Phase 2
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','searching','grabbed','downloaded',
                                       'imported','unavailable','blacklisted')),
  discovered_at      TEXT NOT NULL,                 -- ISO8601, first seen
  last_seen_at       TEXT NOT NULL,                 -- ISO8601, refreshed each detection run
  raw_json           TEXT,                          -- original *arr record (provenance for later phases)
  UNIQUE (arr_app, arr_id)                          -- THE dedup primitive (STATE-02)
);
CREATE INDEX IF NOT EXISTS idx_items_status   ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind ON items(arr_app, kind);
```

**Connection / WAL setup (`state/db.py`):**
```python
import sqlite3
def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")      # concurrent readers + 1 writer
    conn.execute("PRAGMA synchronous=NORMAL;")    # safe with WAL; good durability/throughput balance
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")     # wait, don't instantly error, on contention
    conn.row_factory = sqlite3.Row
    return conn
```

**Migration runner (idempotent, versioned via `PRAGMA user_version`):**
```python
MIGRATIONS = [ ("0001", _SQL_0001_ITEMS) ]   # ordered list of (version, sql)
def run_migrations(conn) -> None:
    have = conn.execute("PRAGMA user_version;").fetchone()[0]
    for i, (_, sql) in enumerate(MIGRATIONS, start=1):
        if i > have:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i};")
```
Call `run_migrations(connect(DB_PATH))` from `main.py`'s FastAPI startup hook so a recreated
container reconciles the schema on boot (criterion 1).

---

## SQLite WAL on a Synology bind-mount — gotchas (STATE-01, criterion 1)

| Concern | Finding | Action |
|---------|---------|--------|
| WAL on a **network** filesystem (NFS/CIFS) | WAL relies on `mmap`/shared-memory (`-shm`) and POSIX locking that network filesystems break — corruption risk. [CITED: SQLite docs "WAL does not work over a network filesystem"] | Keep the DB on a **local** Synology volume (`/volume1/docker/curator/db/` — ext4/btrfs on the NAS's own disks). It is NOT a network share. **Safe.** Verify in Wave 0 that `/volume1/docker/curator/db` is on local storage (it is — same volume as Phase 1's gluetun/slskd config dirs). |
| Bind-mount on its own, NOT under `/data` | The shared `/data` tree is for media + hardlink staging; the DB must not be intermingled. | Mount `/volume1/docker/curator/db:/db` separately (ARCHITECTURE.md already specifies this: "SQLite state (its own mount, NOT under /data)"). DB_PATH = `/db/curator.sqlite`. |
| `-wal` / `-shm` sidecar files | WAL creates `curator.sqlite-wal` + `curator.sqlite-shm` next to the DB. | Ensure the dir is writable by PUID/PGID `1031:65536`; pre-create it. Don't `.gitignore`-relevant — it's runtime only. |
| Durability across container restart | A clean process exit checkpoints WAL; an unclean kill leaves a `-wal` that SQLite replays on next open (no data loss for committed txns). | Commit per upsert (or per detection batch in one txn). On startup, simply `connect()` + `run_migrations()` — SQLite auto-recovers the WAL. Optionally `PRAGMA wal_checkpoint(TRUNCATE)` on graceful shutdown. |
| Single writer under FastAPI | FastAPI may serve requests on threads; uncontrolled concurrent writes → `database is locked`. | **Single writer connection** for all writes (the detection path). `check_same_thread=False` + a module-level write lock, or route all writes through one place. Phase 2 has exactly one writer (gap_detector); the status API is read-only and arrives in Phase 6. `busy_timeout` covers incidental contention. |
| Sync vs async | gap_detector is a periodic batch job, not a hot request path. | Use **synchronous** `sqlite3` + synchronous `httpx.Client` for the adapter. Don't force async over SQLite (no async benefit for a single-writer batch; `aiosqlite` adds complexity for nothing here). FastAPI can call the sync detection in a threadpool when Phase 5 wires the scheduler. |

---

## Stable Identity Key for Dedup (STATE-02)

**Recommendation — primary dedup key: `(arr_app, arr_id)`.**
- `arr_app` ∈ {`lidarr`,`readarr`} namespaces the id so a Lidarr `albumId=42` never collides with a
  Readarr `bookId=42` (the music-vs-books collision the requirement calls out).
- `arr_id` is the *arr's own record id (`AlbumResource.id` / `BookResource.id`) — stable for the life
  of that *arr instance and what every other *arr endpoint (command, manualimport, queue) keys on, so
  it's the natural join key for later phases.
- Enforced as `UNIQUE(arr_app, arr_id)` at the DB layer → dedup is structural, not algorithmic.

**Secondary / cross-instance identity — store `foreign_id`:** `foreignAlbumId` (Lidarr — the
MusicBrainz release-group id) [VERIFIED: AlbumResource.cs] and Readarr's foreign book id
(`foreignBookId`/`foreignEditionId` — exact key to confirm against the live BookResource, A-R1). The
`foreign_id` is the *truly* stable cross-system identity (survives an *arr DB rebuild) and is what
Phase 3 matching will query MusicBrainz/metadata against. **Phase 2 stores it but dedups on
`(arr_app, arr_id)`** — `arr_id` is sufficient and simpler for within-instance dedup; `foreign_id`
can be `NULL` (especially for sparse Readarr metadata) so it's unsuitable as the *primary* key.

> Do NOT dedup on title/artist strings — they mutate (editions, "(Deluxe)", scene tags) and would
> both miss true dupes and create false ones. [VERIFIED: PITFALLS #1]

---

## Adapter Seam Design (ARR-01 / ARR-02) — summary of decisions

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| Interface mechanism | `typing.Protocol` (`ArrAdapter`) in `adapters/base.py` | Structural typing; no inheritance coupling; Lidarr/Readarr each satisfy it independently |
| Uniform model | `GapItem` frozen dataclass (fields above) | Core never sees *arr field names; `raw` preserves the original for later phases |
| Phase-2 interface subset | `get_wanted()` only is *implemented*; import/command/profile methods declared but stubbed | Locks the seam shape now without building Phase 4/5 behavior |
| HTTP client | raw `httpx.Client` per adapter, injected (testability) | Full control over the verified paged envelope + Readarr defensive parsing; mockable with `respx` |
| Music-vs-books gating boundary | `gap_detector` iterates `[lidarr, readarr]` independently; Readarr wrapped in `breaker.py` | A Readarr fault yields `[]` for books; the Lidarr iteration is untouched → music never gated |
| Readarr degradation | `_map()` returns `None` on bad/empty records (skip+log); `_paged()` catches httpx errors → `[]`; breaker opens after N consecutive failures | Satisfies ARR-02 "degrade gracefully, never crash the loop" |
| Where *arr knowledge lives | ONLY in `adapters/lidarr.py` + `adapters/readarr.py` | The firewall; core/state import zero *arr details |

---

## Lidarr / Readarr API Reference (verified this session)

> Auth: `X-Api-Key: <key>` header on every request. Base path `/api/v1`. Curator reaches
> `http://lidarr:8686` and `http://readarr:8787` by container name over synobridge (NAS-RECON.md).

| Need | Lidarr | Readarr | Verified |
|------|--------|---------|----------|
| Monitored **missing** | `GET /api/v1/wanted/missing` | `GET /api/v1/wanted/missing` | [VERIFIED: `MissingController.cs` `[V1ApiController("wanted/missing")]` on both apps] |
| Monitored **cutoff-unmet** | `GET /api/v1/wanted/cutoff` | `GET /api/v1/wanted/cutoff` | [VERIFIED: `CutoffController.cs` `[V1ApiController("wanted/cutoff")]` on both apps] |
| Query params (all four) | `page`, `pageSize`, `sortKey`, `sortDirection`, `monitored` (default `true`), `includeArtist` (default `false`) | same, but `includeAuthor` instead of `includeArtist` | [VERIFIED: controller source — `PagingRequestResource` + the bool params] |
| Response envelope | `{ page, pageSize, sortKey, sortDirection, totalRecords, records: [AlbumResource] }` | `{ ...records: [BookResource] }` | [VERIFIED: `PagingResource<AlbumResource>` / `PagingResource<BookResource>`] |
| Quality profile (Phase 3, NOT now) | `GET /api/v1/qualityprofile` | `GET /api/v1/qualityprofile` | [CITED: STACK.md; not needed Phase 2] |

**Lidarr `AlbumResource` identity fields** [VERIFIED: `AlbumResource.cs`]:
`id`, `foreignAlbumId` (MBID release-group), `artistId`, `title`, `releaseDate`, `monitored`,
`anyReleaseOk`, **`profileId`** (the album's quality-profile id — note the name is `profileId`, NOT
`qualityProfileId`), nested `artist` (an `ArtistResource`; artist MBID lives there, returned only
when `includeArtist=true`), `releases[]`, `media[]`.

**Readarr `BookResource` identity fields** [MEDIUM — shape not fully enumerable from source this
session]: analogous `id`, `authorId`, `title`, `monitored`, a foreign book id, and a quality profile
id. The defensive `_map()` (Pattern 4) tolerates whichever exact keys exist. **Confirm the exact key
names against a live Readarr `/swagger` or a recorded response (A-R1) — but the graceful-degradation
design means a wrong guess skips a book, it does not crash.**

**Sort-key note:** the missing controller defaults to `sortKey="releaseDate"` descending; only
`releaseDate` is documented in-controller. Detection doesn't care about order — page through all
records. Don't pass an unknown `sortKey`.

---

## Readarr risk (ARR-02 driver)

- Readarr upstream development **halted in 2024**; it still runs but is unmaintained. [VERIFIED: PROJECT.md context] Its API surface is therefore *frozen-stable* (good for a non-moving target) but its **metadata server can degrade/return empty editions** and bugs won't be fixed. [VERIFIED: PITFALLS #17]
- This is exactly why ARR-02 demands graceful degradation and ARR-01 demands isolation. The Phase-2 design contains the risk entirely behind the adapter + breaker: a Readarr 5xx/timeout/garbage record yields fewer/zero book `GapItem`s, logged, with the Lidarr path unaffected.
- **Books are best-effort and must not gate music** — success criterion 3 is a Phase-2 *test*: feeding the `ReadarrAdapter` garbage/empty metadata yields a skipped+logged book and a clean Lidarr run in the same `detect_gaps` call.

---

## Common Pitfalls

### Pitfall 1: Dedup upsert clobbers lifecycle status (THE STATE-02 trap)
**What goes wrong:** An item already `imported`/`searching` still appears in `wanted/cutoff`; the
detection upsert resets its `status` to `pending`; Curator re-acts on a satisfied/in-flight item.
**Why:** The *arr only drops an item from wanted after *its own* import; Curator's ledger leads the
*arr's view. Naive `ON CONFLICT DO UPDATE SET status='pending'` undoes Curator's memory.
**How to avoid:** `ON CONFLICT` refreshes metadata + `last_seen_at` ONLY; never `status`. (Pattern 3.)
**Warning sign:** the dedup test (success criterion 4) re-running detection flips a `set_status`-marked
row back to `pending`.

### Pitfall 2: WAL on the wrong filesystem
**What goes wrong:** DB corruption / `database disk image is malformed` if the SQLite file lands on a
network share. [CITED: SQLite WAL docs]
**How to avoid:** DB on `/volume1/docker/curator/db/` (local NAS volume, its own mount, NOT under
`/data`, NOT a CIFS/NFS share). Verify in Wave 0. [VERIFIED: NAS-RECON.md `/volume1` is local]
**Warning sign:** intermittent malformed-image errors; `-shm` lock failures.

### Pitfall 3: `database is locked` from multiple writers
**What goes wrong:** FastAPI threadpool + multiple write connections contend.
**How to avoid:** single writer connection; `PRAGMA busy_timeout=5000`; WAL. Phase 2 has one writer
(gap_detector). Keep it that way.
**Warning sign:** sporadic `sqlite3.OperationalError: database is locked` under load.

### Pitfall 4: Readarr fault crashes the whole detection run
**What goes wrong:** an unhandled Readarr 500/timeout/`KeyError` bubbles up and aborts `detect_gaps`,
so **music** detection also fails — books gating music (ARR-02 violation).
**How to avoid:** defensive `_map()` (return `None`), `_paged()` swallows httpx errors → `[]`, breaker
wraps the adapter, and `gap_detector` iterates adapters independently. (Pattern 4.)
**Warning sign:** the "garbage Readarr metadata" test shows zero Lidarr items or a raised exception.

### Pitfall 5: Building Phase 3-6 in Phase 2
**What goes wrong:** adding slskd clients, matching, scheduling, or the status endpoint now — scope
creep, untestable without the later substrate, and contradicts the horizontal-layer roadmap.
**How to avoid:** Phase 2 = persistence + adapter + detection ONLY. `get_wanted()` is a callable
function; it is NOT scheduled (Phase 5) and triggers NO downloads (Phase 4).
**Warning sign:** `apscheduler`/`slskd-api`/`apprise` in `requirements.txt`; a `/status` route; any
slskd URL use.

### Pitfall 6: Importing the *arr surface outside the adapter
**What goes wrong:** `core/` or `state/` directly parses *arr JSON → the firewall leaks, Readarr
quirks reach the core, and ARR-01's "single agnostic interface" is violated.
**How to avoid:** the ONLY modules that know `foreignAlbumId`/`profileId`/`records[]` are
`adapters/lidarr.py` and `adapters/readarr.py`. Core sees `GapItem` only.
**Warning sign:** `rec["records"]` or `X-Api-Key` appearing anywhere under `core/` or `state/`.

---

## Runtime State Inventory

> Phase 2 is a **greenfield additive** phase (new tables + new modules on top of the Phase-1 stub).
> It is not a rename/refactor/migration. This section is included only to explicitly clear the
> rename-style categories, since Phase 2 *introduces* the first stored state.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | NEW: `curator.sqlite` (`items` table) created by Phase 2 at `/volume1/docker/curator/db/`. No pre-existing Curator data to migrate (Phase 1 stored none). | Create dir with PUID/PGID 1031:65536 + the `/db` bind-mount before first run. |
| Live service config | None changed. Curator only *reads* Lidarr/Readarr; it writes nothing to them in Phase 2. | None. |
| OS-registered state | None — no Task Scheduler / pm2 / systemd registration; Curator is a container started by compose. | None. |
| Secrets / env vars | NEW reads: `READARR_API_KEY`, `READARR_URL` (Lidarr ones already in Phase-1 `.env`). `DB_PATH` (new, default `/db/curator.sqlite`). All via `.env`, never baked. | Add `READARR_*` + `DB_PATH` to `.env`/`.env.example`; add the `/db` volume to the curator service in compose. |
| Build artifacts | None stale — `app/` packages are added, image rebuilt by CI as normal. | Rebuild image via CI (existing pipeline). |

**Verified-nothing categories:** OS-registered state and Live-service-config — None (Curator is read-only against the *arr in Phase 2 and runs purely as a compose-managed container).

---

## Environment Availability

| Dependency | Required By | Available (dev sandbox) | Version | Fallback |
|------------|------------|-------------------------|---------|----------|
| Python 3.12 | runtime + pyarr (if used) | ✗ (sandbox is 3.9.6) | 3.9.6 local | Image is `python:3.12-slim`; tests run in CI / on NAS. Write 3.12-compatible code; do NOT rely on local `pytest` |
| Network / PyPI | install httpx/respx; verify versions | ✗ (offline — `curl pypi.org` fails) | — | Versions verified via WebSearch this session; final pin confirmed on first CI build (network present) |
| Live Lidarr (`lidarr:8686`) | GAP-01/02 live smoke | ✗ (not on dev host) | — | **Recorded JSON fixtures** drive all unit tests; live smoke is the on-NAS confirmation only |
| Live Readarr (`readarr:8787`) | ARR-02 live smoke | ✗ | — | Fixtures (`readarr_empty.json`, `readarr_garbage.json`) prove graceful degradation deterministically |
| SQLite | the ledger | ✓ (bundled in any Python) | 3.x | none needed |

**Blocking with no fallback:** none. Phase 2 is fully shippable from fixtures; the only thing that
needs the NAS is the optional live smoke against the real *arr (a confirmation, not a gate).

**Critical consequence (matches Phase 1):** the dev sandbox is **Python 3.9 + offline**, so
`pytest` cannot run here and `pip install` has no network. The planner MUST treat the test run as a
CI/NAS activity and the local environment as fixture-authoring only. Recorded *arr fixtures are
therefore **mandatory** (not optional) for deterministic adapter/detection tests.

---

## Validation Architecture

> `nyquist_validation: true` in config.json → this section is REQUIRED. Phase 2 is pure
> engineering (no infra smoke), so the suite is `pytest` over fixtures + a single optional on-NAS
> live-smoke check. Tests run in CI / on the NAS (the dev sandbox is Python 3.9 + offline).

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` (already configured in Phase 1 `pyproject.toml`: `pythonpath=["app"]`, `testpaths=["app/tests"]`) |
| Config file | `pyproject.toml` (exists) — extend `testpaths` is already `app/tests` |
| HTTP mocking | `respx` (mock `httpx` transport) OR plain `httpx.MockTransport` — no live *arr needed |
| Quick run command | `python -m pytest app/tests -x -q` |
| Full suite command | `python -m pytest app/tests -q` (run in CI; locally only on Python 3.12) |
| Live smoke (NAS, optional) | `curl -s -H "X-Api-Key: $LIDARR_API_KEY" "http://lidarr:8686/api/v1/wanted/missing?pageSize=1"` returns the paged envelope |

### Phase Requirements → Test Map
| Req ID | Behavior (observable signal) | Test Type | Automated Command | File |
|--------|------------------------------|-----------|-------------------|------|
| STATE-01 | A `pending` item written, then DB reconnected, still present with status intact (restart-durability) | unit | `pytest app/tests/test_state_repo.py::test_persists_across_reconnect -x` | ❌ Wave 0 |
| STATE-01 | `set_status()` round-trips every enum value; bad value rejected by CHECK | unit | `pytest app/tests/test_state_repo.py::test_status_enum -x` | ❌ Wave 0 |
| STATE-02 | Upserting the same `(arr_app,arr_id)` twice yields exactly ONE row (dedup) | unit | `pytest app/tests/test_state_repo.py::test_dedup_no_duplicate -x` | ❌ Wave 0 |
| STATE-02 | Upsert does NOT reset an already-`imported`/`searching` status to `pending` | unit | `pytest app/tests/test_state_repo.py::test_upsert_preserves_status -x` | ❌ Wave 0 |
| GAP-01 | `LidarrAdapter.get_wanted()` over the recorded `wanted/missing` fixture yields N `GapItem(gap_type='missing')` with correct `arr_id`/`foreign_id`/`profile_id` | unit | `pytest app/tests/test_lidarr_adapter.py::test_missing_mapping -x` | ❌ Wave 0 |
| GAP-02 | Same over `wanted/cutoff` fixture → `gap_type='cutoff'`; multi-page fixture paginates fully | unit | `pytest app/tests/test_lidarr_adapter.py::test_cutoff_and_paging -x` | ❌ Wave 0 |
| ARR-01 | Both `LidarrAdapter` and `ReadarrAdapter` satisfy the `ArrAdapter` Protocol; core imports zero *arr field names | unit + grep | `pytest app/tests/test_adapter_protocol.py -x` + `! grep -rE 'records\[|X-Api-Key|foreignAlbumId' app/core app/state` | ❌ Wave 0 |
| ARR-02 | Feeding `ReadarrAdapter` empty + garbage fixtures → bad books skipped+logged, no exception; a Readarr 500 (mock) → breaker returns `[]`; `detect_gaps([lidarr,readarr])` still upserts all Lidarr items | unit | `pytest app/tests/test_readarr_adapter.py -x` + `pytest app/tests/test_gap_detector.py::test_readarr_fault_does_not_gate_music -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest app/tests -x` (fast; pure unit + fixtures).
- **Per wave merge:** full `python -m pytest app/tests -q` in CI.
- **Phase gate:** full suite green in CI + (optional) on-NAS live smoke that `wanted/missing` and
  `wanted/cutoff` return the expected envelope from the real Lidarr, AND a real `detect_gaps` run
  shows >0 `items` rows and a repeated run adds zero duplicates (dedup proven against live data).

### Wave 0 Gaps
- [ ] `app/tests/fixtures/lidarr_missing.json`, `lidarr_cutoff.json` (multi-page variant), `readarr_missing.json`, `readarr_empty.json`, `readarr_garbage.json` — recorded/representative *arr responses
- [ ] `app/tests/test_state_repo.py` — dedup, status-preservation, restart-durability, enum CHECK
- [ ] `app/tests/test_lidarr_adapter.py` — missing+cutoff mapping, pagination (via `respx`)
- [ ] `app/tests/test_readarr_adapter.py` — graceful degradation on empty/garbage/5xx
- [ ] `app/tests/test_gap_detector.py` — Readarr fault does not gate music; end-to-end upsert counts
- [ ] `app/tests/test_adapter_protocol.py` — both adapters are `ArrAdapter`; firewall grep
- [ ] `respx` added to dev deps (verify version + httpx compat on first CI build)

---

## Security Domain

> `security_enforcement` absent in config.json → treated as enabled.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | `X-Api-Key` to the *arr from `.env`; never logged, never baked into the image |
| V3 Session Management | no | No sessions in Phase 2 (no new endpoints) |
| V4 Access Control | minimal | Curator is LAN/Tailscale-only (Phase 1); no new exposure in Phase 2 |
| V5 Input Validation | yes | Defensive parsing of *arr responses (pydantic/`_map()` tolerating missing/garbage fields — Readarr) |
| V6 Cryptography | no | No crypto in Phase 2 |
| V14 Config | yes | New secrets (`READARR_API_KEY`) via `.env` only; pinned deps; no secrets in CI logs/image |

### Known Threat Patterns for {Python adapter over REST + SQLite}
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection into the ledger | Tampering | Parameterized `sqlite3` queries ONLY (`?` placeholders — Pattern 3); never f-string SQL |
| API key leak (logs / image / git) | Information Disclosure | Keys from `.env`; `.dockerignore` excludes `.env` (Phase 1); never log headers; scan image (Phase-1 `docker history` check still applies) |
| Malformed/hostile *arr response crashes loop | Denial of Service | Defensive parsing + breaker (ARR-02); a bad record is skipped, not fatal |
| DB file world-writable / wrong owner | Tampering / Elevation | DB dir owned by PUID/PGID 1031:65536; not under `/data`; container runs non-root |
| Untrusted dependency (slop) | Tampering | Only `httpx`/`respx` added; verify on first CI build (slopcheck unavailable offline) |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A-R1 | Readarr `BookResource` exposes a foreign book id key named `foreignBookId` (and a profile id) | API Reference, Pattern 4 | LOW — defensive `_map()` skips a book on a wrong key, never crashes; confirm against live Readarr `/swagger` or a recorded response. Books are best-effort. |
| A-R2 | Readarr's quality-profile id field is `qualityProfileId` (vs Lidarr's album-level `profileId`) | Pattern 4 | LOW — stored-only in Phase 2 (not acted on until Phase 3); `_map()` tries both keys. Confirm in Phase 3. |
| A-H1 | `httpx` latest is `0.28.x` and `respx` is compatible with it | Standard Stack | LOW — pin + verify on first CI build (network present); both are mature, widely-used. |
| A-P1 | `pyarr` 6.6.0 requires Python ≥3.12 and covers both Lidarr+Readarr wanted endpoints | Supporting stack | LOW — pyarr is OPTIONAL; recommendation is raw `httpx`. requires-python verified via pyproject this session. |
| A-S1 | SQLite WAL on `/volume1/docker/curator/db` (local NAS volume) is corruption-safe | WAL gotchas | LOW — `/volume1` is local ext4/btrfs (NAS-RECON.md), not a network share; WAL is safe on local FS. Verify the dir is on local storage in Wave 0 (it is — same volume as Phase-1 config dirs). |
| A-AR1 | The four endpoint routes + paging params are identical on the Lidarr/Readarr *versions the owner runs* (verified on `develop`) | API Reference | LOW-MED — verified from `develop` source; Servarr v1 paging is long-stable. Live smoke on the NAS confirms against the owner's actual versions. |

**These are all LOW/LOW-MED risk** because the design's graceful-degradation and DB-level dedup
contain the blast radius of any wrong guess, and a single on-NAS live-smoke resolves A-R1/A-AR1.

---

## Open Questions (RESOLVED)

1. **Exact Readarr `BookResource` identity field names** (A-R1/A-R2).
   - What we know: routes + paging + `id`/`authorId`/`monitored` shape are confirmed; a foreign id + profile id exist.
   - What's unclear: the precise key names (`foreignBookId` vs `foreignEditionId`; `qualityProfileId` vs `profileId`).
   - **RESOLVED:** deferred to the on-NAS live smoke (LOW risk) — the defensive `ReadarrAdapter._map()` tolerates both profile-key spellings and skips+logs on a missing `id`, so a wrong guess is non-fatal by design (ARR-02). Plan 02-03 implements it; 02-03 Task 3 + 02-04 prove the degradation offline.

2. **pyarr vs raw httpx** (Claude's discretion).
   - **RESOLVED:** raw `httpx` for Phase 2 (four trivial GETs, full control over Readarr degradation) — adopted in plan 02-03. Revisit pyarr only if a later phase needs many endpoints.

3. **SQLite access layer** (Claude's discretion).
   - **RESOLVED:** stdlib `sqlite3` + thin `state/repo.py` — adopted in plan 02-02. Dedup kept as a DB `UNIQUE(arr_app, arr_id)` constraint.

4. **Should Phase 2 expose a manual trigger for detection?**
   - **RESOLVED:** yes — a one-shot `python -m core.gap_detector` (`__main__`) entry, adopted in plan 02-04 Task 1, as a test/UAT affordance only. The scheduled daemon remains Phase 5 (NOT built here).

---

## State of the Art

| Old Approach | Current Approach | When | Impact |
|--------------|------------------|------|--------|
| Derive "act?" from *arr wanted list (Soularr) | Curator-owned SQLite ledger as source of truth | this project | Kills redundant downloads (prior pain #1); STATE-01/02 |
| Title-string dedup | `UNIQUE(arr_app, arr_id)` + `foreign_id` provenance | this project | Collision-proof, edition-stable |
| Rollback journal | SQLite **WAL** (concurrent readers + 1 writer) | SQLite default-able since 3.7 | Lets the Phase-6 status API read while detection writes |
| Lidarr+Readarr called inline | One `ArrAdapter` Protocol; Readarr breaker-isolated | this project | Readarr (retired 2024) can't gate music; ARR-01/02 |

**Deprecated/outdated:**
- Readarr itself: upstream halted 2024 — treat as frozen + fragile, isolate behind the adapter.
- `AlbumResource.profileId` (note: not `qualityProfileId`) — store the right field name now.

---

## Sources

### Primary (HIGH confidence — verified this session)
- Lidarr `MissingController.cs` (`develop`) — `[V1ApiController("wanted/missing")]`, `pageSize/sortKey/sortDirection`, `monitored` (default true), `includeArtist` (default false): https://github.com/Lidarr/Lidarr (src/Lidarr.Api.V1/Wanted/MissingController.cs)
- Lidarr `CutoffController.cs` — `[V1ApiController("wanted/cutoff")]`, `GetCutoffUnmetAlbums` (`AlbumsWhereCutoffUnmet`), same paging params: https://github.com/Lidarr/Lidarr (src/Lidarr.Api.V1/Wanted/CutoffController.cs)
- Lidarr `AlbumResource.cs` — `id`, `foreignAlbumId`, `artistId`, `title`, `releaseDate`, `monitored`, `anyReleaseOk`, `profileId`, nested `artist`: https://github.com/Lidarr/Lidarr (src/Lidarr.Api.V1/Albums/AlbumResource.cs)
- Readarr `MissingController.cs` — `[V1ApiController("wanted/missing")]`, `PagingResource<BookResource>`, `includeAuthor`, `monitored`: https://github.com/Readarr/Readarr (src/Readarr.Api.V1/Wanted/MissingController.cs)
- Readarr `CutoffController.cs` — `[V1ApiController("wanted/cutoff")]`, `GetCutoffUnmetBooks`, `includeAuthor`, `monitored`: https://github.com/Readarr/Readarr (src/Readarr.Api.V1/Wanted/CutoffController.cs)
- pyarr `pyproject.toml` — version 6.6.0, `requires-python=">=3.12"`: https://github.com/totaldebug/pyarr/blob/main/pyproject.toml

### Secondary (MEDIUM — verified against PyPI/docs)
- pyarr on PyPI (6.6.0): https://pypi.org/project/pyarr/
- slskd-api on PyPI (0.2.4; Python ≥3.11) — for later phases, NOT Phase 2: https://pypi.org/project/slskd-api/
- Servarr Wiki — Lidarr wanted/cutoff semantics; `X-Api-Key`; v1 surface: https://wiki.servarr.com/lidarr
- SQLite WAL documentation — WAL requires shared memory / local FS; "does not work over a network filesystem": https://www.sqlite.org/wal.html

### Tertiary / project-internal (HIGH — prior live-verified research, reused)
- `.planning/research/STACK.md`, `ARCHITECTURE.md`, `PITFALLS.md` (live-verified 2026-05-29) — adapter/breaker design, dedup-on-identity, WAL fit, *arr endpoint table, Readarr risk
- `.planning/phases/phase-1/RESEARCH.md` + `NAS-RECON.md` — synobridge topology, Curator→*arr by container name, PUID/PGID 1031/65536, `/volume1` local, `lidarr:8686`/`readarr:8787`
- `.planning/phases/phase-1/01-03-PLAN.md` + existing `app/` — the FastAPI stub, `pyproject.toml` (`pythonpath=["app"]`), Dockerfile that Phase 2 extends

---

## Metadata

**Confidence breakdown:**
- *arr endpoint routes + query params + paging envelope: HIGH — read from Lidarr/Readarr controller source this session (both apps, both endpoints).
- Lidarr identity fields (`foreignAlbumId`/`artistId`/`profileId`): HIGH — `AlbumResource.cs`.
- Readarr exact field names: MEDIUM — shape confirmed, precise keys to confirm on live API (A-R1); contained by graceful degradation.
- SQLite-WAL access pattern + dedup primitive: HIGH — standard SQLite + DB-level UNIQUE; WAL-on-local-FS is the documented safe configuration.
- Adapter seam design (Protocol + breaker): HIGH — matches sibling ARCHITECTURE.md and the binding ARR-01/02 requirements.
- Package legitimacy: MEDIUM — slopcheck unavailable offline; `httpx`/`respx` are high-trust, versions verified via search; confirm on first CI build.

**Research date:** 2026-05-30
**Valid until:** ~2026-06-29 (30 days). Re-confirm `httpx`/`respx`/`pyarr` versions and resolve the
Readarr `BookResource` field names (A-R1) against the owner's live Readarr when available.
