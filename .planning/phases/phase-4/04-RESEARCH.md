# Phase 4: Acquisition, Staging & Clean Import - Research

**Researched:** 2026-05-31
**Domain:** slskd REST client (NEW) + *arr Manual Import command API + per-item staging/quarantine lifecycle + SQLite acquisition state machine
**Confidence:** HIGH (slskd endpoints + Lidarr ManualImport contract verified from source/wrappers; Plex confirmed). MEDIUM on exact slskd transfer-state string enum and the precise ManualImport POST envelope (assembled from verified source field names, not a live capture). LOW on Readarr ManualImport deltas (Readarr unmaintained, best-effort by design).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Declare a download dead via **no-progress stall detection** (no bytes for N minutes, configurable, default ~10 min) — NOT a fixed wall-clock deadline. Tolerant of slow but legitimate Soulseek peers.
- **D-02:** On death (stall, partial, or hard fail): **fall to the next-best gate-accepted candidate** from the same search. If candidates are exhausted, **surface the gap as stuck and back off** (do not loop). Never hold a slot forever.
- **D-03:** The bar for DONE is **`*arr`-confirmed import into `/volume1`** — re-query the adapter and confirm the item left the wanted/missing list. "Downloaded" never counts as "imported."
- **D-04:** The **Plex scan is fire-and-forget**: trigger it, never block completion or the staging purge on it, and do **not warn loudly** on a Plex hiccup — a quiet debug-level log only. (`*arr` remains the source of truth.)
- **D-05:** **Verified import → purge the per-item staging dir immediately.**
- **D-06:** **Terminal or ambiguous failure → move the staging dir into a quarantine area, record the failure reason, surface it, and auto-purge the quarantine after a TTL (or on the next run).** Do NOT keep failed staging indefinitely; do NOT blind-purge on failure.
- **D-07:** Issue the slskd search, **wait a fixed collection window** (configurable, default ~8–15 s) for results to trickle in, build `Candidate` objects, then run `gate.evaluate` **once** over the full set. A fixed window (not first-match short-circuit) is required so Phase 3's rec-gap can compare the runner-up.
- **D-08:** If nothing passes the gate, **retry once with a relaxed query** (drop year/edition noise), re-score; if still nothing, surface the gap as stuck.
- **D-09:** Drive the import the same way the Lidarr/Readarr "Manual Import" UI button does, via the command API: `GET` the *arr's proposed file→track mapping for the staging folder + downloadId, **filter to the wanted files**, then `POST` the `ManualImport` command listing exactly those files with their resolved release/track IDs and **`importMode=Move`** (atomic rename/hardlink within the shared `/data` tree — no cross-FS copy). Explicit, never a blind drop-folder rescan.
- **D-10:** The import action flows through the **`*arr`-agnostic adapter** (Phase 2/3 firewall), so music (Lidarr, primary) and books (Readarr, best-effort) share one import path; Readarr faults degrade and never block music.
- **D-11:** Before the **first live download test**, basic slskd sharing must be configured **manually in slskd.yml** (share `/data/media/music` + `/data/media/books`, the clean library — NOT the download/staging tree) and verified `shared file count > 0`. Phase 4 **code does not configure shares**; it verifies/assumes them.

### Claude's Discretion
- Exact default values for the tunables (stall threshold, search window, quarantine TTL) — pick sensible defaults, all config-overridable via `Settings.from_env()` (SP-4).
- The slskd REST client shape (new in this phase) and how acquisition state is tracked in the SQLite ledger (downloading / importing / imported / quarantined / stuck) — reuse the existing state-repo + circuit-breaker patterns.
- Whether Phase 4 processes strictly one item at a time or a small bounded set — single-item end-to-end is sufficient; true concurrency/scheduling is Phase 5.

### Deferred Ideas (OUT OF SCOPE)
- Autonomous scheduling / daemon loop, grace-window + Usenet-race, exponential backoff + permanent-unavailable memory — **Phase 5** (GAP-03, STATE-03, REL-01/02/03).
- Programmatic share self-healing (verify count > 0, re-scan, survive restarts) — **Phase 5** (SHARE-01/02). Phase 4 only *assumes/verifies* manual shares for the live test.
- Detection batch-fsync perf optimization — **Phase 5**.
- Status endpoint / Apprise notifications — **Phase 6** (OBS-01/02). Phase 4 records stuck/quarantined state in the ledger; surfacing it richly is Phase 6.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ACQ-01 | Trigger slskd searches via REST for eligible gaps | `POST /api/v0/searches` + poll `GET /api/v0/searches/{id}` / `GET /api/v0/searches/{id}/responses`; collection-window pattern (D-07); response→`Candidate.from_slskd` already exists |
| ACQ-02 | Initiate chosen candidate's download into an isolated per-item staging dir + watch to completion | `POST /api/v0/transfers/downloads/{username}` (body = the file dicts from search responses); poll `GET /api/v0/transfers/downloads/{username}/{id}`; **batchId routing makes slskd write to `downloads/{batchId}/`** — the per-item isolation primitive |
| ACQ-03 | Handle partial/failed/stalled downloads (timeout, cancel, mark, back off); never hold a slot forever | `bytesTransferred` delta → stall detection (D-01); `DELETE /api/v0/transfers/downloads/{username}/{id}?remove=true` to cancel; next-candidate fallback (D-02) |
| IMPORT-01 | Completed downloads land in an isolated per-item staging dir on shared `/data`, path-identical across slskd/Curator/*arr, hardlink-capable | slskd `directories.downloads` is INSIDE `/data`; the staging dir is `downloads/{batchId}/{remote-folder}/...`; same absolute path string is what *arr's GET manualimport `folder` param receives — identity proven by the Phase-1 hardlink test |
| IMPORT-02 | Import ONLY the wanted files via *arr Manual Import / command API (never a blind rescan) | `GET /api/v1/manualimport?folder=&downloadId=` returns `ManualImportResource[]`; filter; `POST /api/v1/command` `{name:"ManualImport", importMode:"Move", files:[...]}` — verified field names from Lidarr source |
| IMPORT-03 | Verify the item imported into `/volume1` (re-query *arr; "downloaded" ≠ "imported") | re-call `get_wanted()` / a targeted album lookup and confirm the gap left the wanted/missing list (D-03) |
| IMPORT-04 | Confirm Plex reflects new media (trigger a scan) | `GET http://plex:32400/library/sections/{id}/refresh?path=&X-Plex-Token=` fire-and-forget (D-04) |
| IMPORT-05 | Auto-purge the per-item staging/quarantine dir; unresolved failures reconciled/surfaced | purge-on-success (D-05); move-to-quarantine + TTL purge on failure (D-06); both reconciled in the ledger |
</phase_requirements>

## Summary

Phase 4 closes the single-item acquisition loop. The one genuinely new surface is a **Curator→slskd REST client** — there is no prior art in the codebase for it, but it slots cleanly into the existing pattern: an injected `httpx.Client`, `X-API-Key` header, `.get()`-defensive parsing, optional circuit-breaker wrap. **No new runtime dependency is required** — slskd's API is plain JSON over HTTP and the project already pins `httpx==0.28.1`. The slskd Python wrapper (`slskd-api`) is useful as an endpoint reference but should NOT be added as a dependency: it would break the "thin hand-owned client behind the firewall" posture, and the loop is small.

The slskd API (`http://<NAS-IP>:5030/api/v0`, header `X-API-Key`) gives: `POST /searches` (submit) → poll `GET /searches/{id}` + `GET /searches/{id}/responses` (collection window, D-07) → `POST /transfers/downloads/{username}` (enqueue, body = the file dicts straight from the search response) → poll `GET /transfers/downloads/{username}/{id}` for `state` + `bytesTransferred` (stall detection, D-01) → `DELETE …?remove=true` (cancel). The **single most important slskd finding for staging isolation: slskd routes a download with a `batchId` into `downloads/{batchId}/…` (verified from `DownloadService.cs`), and otherwise into `downloads/{remote-folder}/…` with NO username subdir.** That batchId routing is the clean per-item-staging primitive — set a per-acquisition batch id and slskd's own writes land in an isolated, predictable subdir on the shared `/data` tree.

The import side is the explicit Manual Import the owner has always wanted and Soularr never did: **Soularr uses `DownloadedAlbumsScan` (a blind drop-folder rescan) — exactly the D-09-forbidden anti-pattern.** Curator instead calls `GET /api/v1/manualimport?folder={staging}&downloadId={id}` (returns `ManualImportResource[]` with verified fields: `path, name, artist, album, albumReleaseId, tracks, quality, rejections, …`), filters to the wanted files, and `POST /api/v1/command` with `{name:"ManualImport", importMode:"Move", files:[…]}`. `importMode:"Move"` triggers the atomic hardlink within `/data`. DONE is `*arr`-confirmed (re-query, D-03), Plex is a fire-and-forget `…/refresh` (D-04), then purge-on-success / quarantine-with-TTL on failure (D-05/D-06). All of this flows through the `*arr`-agnostic adapter (D-10) by implementing the already-stubbed `manual_import_candidates` / `execute_import` / `verify_imported` methods on `base.ArrAdapter`.

**Primary recommendation:** Build a thin hand-owned `SlskdClient` (httpx, `X-API-Key`, defensive, breaker-wrappable) — NO new package; route each download into a per-item `batchId` staging subdir under slskd's `directories.downloads` (which lives inside `/data`); orchestrate search→gate→download→watch→import→verify→Plex→purge in a new `core/acquire.py` (the Phase-4 composition point, mirroring `gap_detector.detect_gaps`/`gate.evaluate`); implement the stubbed import methods on the adapters with `importMode:"Move"`; add a `staged_files`/acquisition-state extension to the SQLite ledger via a new migration `0002`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| slskd search submit + poll | New `adapters/slskd.py` client | — | All wire vocabulary (searchText, responses, files) stays in the adapter tier; firewall keeps it out of core |
| Build `Candidate`s from slskd results | `core/candidate.py` (`from_slskd`, EXISTS) | slskd client supplies raw dicts | The Phase-3 contract already owns this seam; Phase 4 just feeds it live JSON |
| Gate decision (which candidate) | `core/gate.py` (`evaluate`, EXISTS) | — | Consumed unchanged — Phase 4 does NOT re-judge match/quality |
| Download enqueue + transfer watch / stall | New `adapters/slskd.py` client | `core/acquire.py` reads neutral progress | Transfer `state`/`bytesTransferred` are slskd vocabulary → normalize to a neutral progress shape before core sees it |
| Per-item staging path | Filesystem on shared `/data` | slskd `directories.downloads` + batchId | Path identity across containers is a hard correctness constraint (IMPORT-01) |
| Manual Import (map→filter→command) | `*arr`-agnostic adapter (`adapters/`) | — | D-10: all *arr import vocabulary (folder, downloadId, files[], albumReleaseId) stays adapter-local |
| Import verification | `*arr`-agnostic adapter | `core/acquire.py` reads bool | D-03: re-query is an adapter call; core only sees "imported: yes/no" |
| Plex scan | New small Plex client (`adapters/plex.py`) | `core/acquire.py` fire-and-forget | D-04: Plex is not an *arr; trusted/quiet downstream view |
| Acquisition state transitions | `state/repo.py` + new migration | `core/acquire.py` orchestrates | Lifecycle ledger is the spine; core drives the transitions |
| Orchestration (the loop) | NEW `core/acquire.py` | all of the above | Single composition point, *arr-field-free (firewall) — mirrors `gap_detector`/`gate` |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| httpx | 0.28.1 (already pinned) | The slskd + *arr + Plex REST client transport | Already the project's REST transport (`lidarr.py`/`readarr.py`); injected `Client` makes it offline-testable with respx; **no new dependency needed** [VERIFIED: app/requirements.txt] |
| (stdlib) sqlite3 | Python 3.12 | Acquisition-state ledger extension | Existing spine (`state/db.py`, `state/repo.py`); add migration `0002` [VERIFIED: codebase] |
| (stdlib) pathlib / shutil / os | Python 3.12 | Staging dir create / purge / quarantine move | `shutil.rmtree`, `shutil.move`, `Path.mkdir` — no library needed for staging lifecycle [ASSUMED] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| respx | 0.22.0 (already pinned, dev) | Mock httpx for slskd/*arr/Plex in tests | All adapter-level unit tests; keeps Phase 4 offline-provable [VERIFIED: app/requirements-dev.txt] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-owned `SlskdClient` on httpx | `slskd-api` PyPI wrapper | The wrapper is an excellent *reference* for endpoint paths/field names, but adding it: (a) introduces a new runtime dependency to slopcheck/pin; (b) breaks the "thin client behind the firewall, breaker-wrappable, injected httpx for offline tests" posture every other adapter follows; (c) is API-v0/unstable (semver not yet honored). Use it as documentation, not a dependency. |
| `importMode:"Move"` | `importMode:"Copy"` | Copy is a cross-FS-safe fallback but VIOLATES the atomic-hardlink contract (CLAUDE.md #1 import-failure cause) and leaves the staging copy to purge anyway. Move is correct given identical `/data` paths. |
| Explicit `ManualImport` command | `DownloadedAlbumsScan` (what Soularr does) | `DownloadedAlbumsScan` is a blind drop-folder rescan — **the exact D-09-forbidden anti-pattern and a documented Soularr weakness.** It imports whatever Lidarr decides, can trigger a full rescan (Lidarr issue #3450), and gives no per-file control. Rejected. |

**Installation:**
```bash
# No new runtime packages. Phase 4 reuses the already-pinned httpx + stdlib.
# (If the planner chooses to add the slskd-api wrapper despite the recommendation above,
#  it MUST go through a checkpoint:human-verify package-legitimacy gate first — see audit.)
```

**Version verification:**
- `httpx==0.28.1` — already pinned and in use (Phase 2). No change. [VERIFIED: app/requirements.txt]
- slskd server: **0.25.1** (May 2026), API base `/api/v0` unchanged, header `X-API-Key`. [CITED: github.com/slskd/slskd/releases]

## Package Legitimacy Audit

> Phase 4 adds **no new runtime package**. The audit below covers only the optional `slskd-api` wrapper, which this research recommends NOT adopting.

| Package | Registry | Age | Downloads | Source Repo | slopcheck | Disposition |
|---------|----------|-----|-----------|-------------|-----------|-------------|
| httpx | PyPI | mature (years) | very high | github.com/encode/httpx | (unavailable) | Already approved (Phase 2 checkpoint) — no re-gate |
| respx | PyPI | mature | high | github.com/lundberg/respx | (unavailable) | Already approved (Phase 2 checkpoint) — dev-only |
| slskd-api | PyPI | ~1 yr (wrapper Apr 2026 push) | low | github.com/bigoulours/slskd-python-api | (unavailable) | **NOT RECOMMENDED** — use as reference only; if adopted, gate behind checkpoint:human-verify [ASSUMED] |

**Packages removed due to slopcheck [SLOP] verdict:** none (slopcheck unavailable this session)
**Packages flagged as suspicious [SUS]:** none

*slopcheck could not be installed in this session (no network / sandbox). Per protocol, the only candidate new package (`slskd-api`) is tagged `[ASSUMED]`; the recommendation is to NOT add it. If the planner overrides and adopts it, it MUST be gated behind a `checkpoint:human-verify` task before install (the established Phase-2/Phase-3 package-legitimacy precedent).*

## Architecture Patterns

### System Architecture Diagram

```
                          eligible gap (from ledger, status='pending')
                                       │
                                       ▼
                         ┌──────────────────────────┐
                         │  core/acquire.py          │  ← NEW Phase-4 composition point
                         │  (the single loop; *arr-  │     (mirrors gap_detector.detect_gaps
                         │   field-free, firewall)   │      + gate.evaluate)
                         └──────────────────────────┘
        ┌──────────────┬───────────────┼────────────────┬───────────────┐
        ▼              ▼               ▼                ▼               ▼
  ┌───────────┐  ┌───────────┐  ┌────────────┐  ┌─────────────┐  ┌──────────┐
  │ SlskdClient│  │core/gate  │  │  staging   │  │ *arr adapter│  │PlexClient│
  │ (adapters) │  │ .evaluate │  │  (fs)      │  │ (import +   │  │(fire-and-│
  │            │  │ (EXISTS)  │  │            │  │  verify)    │  │ forget)  │
  └───────────┘  └───────────┘  └────────────┘  └─────────────┘  └──────────┘
        │              ▲               ▲                │
        │  search      │ Candidates    │ batchId dir    │ GET manualimport →
        │  POST /searches              │ downloads/{bid}│ filter → POST command
        │  poll /searches/{id}/responses               │ (importMode=Move)
        │              │               │                │ then re-query (verify)
        ▼              │               │                ▼
   slskd (via gluetun published port :5030, X-API-Key)   Lidarr/Readarr (synobridge name)

  LIFECYCLE (status column on items + new staged_files rows):
   pending ─search→ searching ─gate.accept+enqueue→ downloading
        ├─ bytes flowing ───────────────► (watch)
        ├─ no bytes N min (D-01) ─cancel→ (next candidate, D-02) ─exhausted→ stuck
        ▼ transfer Completed
   downloading ─GET manualimport→ importing ─POST ManualImport(Move)→ (verify re-query)
        ├─ left wanted list (D-03) ─purge staging (D-05)→ imported ─Plex refresh (D-04, f&f)
        └─ import rejected/ambiguous ─move to quarantine + record (D-06)→ quarantined ──TTL──► purged
```

### Recommended Project Structure
```
app/
├── adapters/
│   ├── slskd.py          # NEW: SlskdClient (search/enqueue/transfer-watch/cancel); httpx+X-API-Key, defensive
│   ├── plex.py           # NEW: tiny PlexClient.refresh(section_id, path) — fire-and-forget (D-04)
│   ├── base.py           # IMPLEMENT the stubbed manual_import_candidates/execute_import/verify_imported
│   ├── lidarr.py         # IMPLEMENT import methods (primary, raise_for_status)
│   └── readarr.py        # IMPLEMENT import methods (best-effort, swallow→degrade; books never gate music)
├── core/
│   ├── acquire.py        # NEW: the Phase-4 composition loop (firewall-clean; consumes gate.evaluate)
│   └── staging.py        # NEW (optional): pure staging-path + purge/quarantine helpers (testable w/ tmp_path)
├── state/
│   ├── schema.sql        # leave 0001 untouched
│   ├── migration_0002.sql# NEW: staged_files table + extend status enum (downloading/importing/imported/quarantined/stuck)
│   ├── db.py             # append ("0002", _SCHEMA_0002) to MIGRATIONS
│   └── repo.py           # NEW acquisition-state mutators (set_status already exists; add staged_files DAO)
└── config.py             # extend Settings.from_env() with Phase-4 tunables (SP-4)
```

### Pattern 1: Thin defensive slskd client mirroring the *arr adapters
**What:** A `SlskdClient(base_url, api_key, client: httpx.Client)` with `X-API-Key` header, `.get()`-defensive JSON parsing, and the four operations (search submit/poll, enqueue, watch, cancel). Optionally breaker-wrappable like Readarr if slskd flakiness should not burn an attempt (REL-02 is Phase 5, but the seam can be shaped now).
**When to use:** All Curator→slskd traffic.
**Example:**
```python
# Source: pattern mirrors app/adapters/lidarr.py (VERIFIED in codebase) +
#         endpoint paths VERIFIED from slskd-python-api source (bigoulours/slskd-python-api).
class SlskdClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.Client):
        if not api_key:
            raise ValueError("SLSKD_API_KEY is required")
        self._base = base_url.rstrip("/") + "/api/v0"   # slskd base path
        self._client = client
        self._headers = {"X-API-Key": api_key}          # [VERIFIED: slskd auth header]

    def search(self, text: str) -> str:
        # POST /searches  body: {"searchText", optional: fileLimit, searchTimeout,
        #   minimumResponseFileCount, minimumPeerUploadSpeed, maximumPeerQueueLength, responseLimit}
        # returns a search object carrying its "id" (a GUID). [VERIFIED: searches.py]
        r = self._client.post(f"{self._base}/searches",
                              headers=self._headers, json={"searchText": text}, timeout=30.0)
        r.raise_for_status()
        return r.json().get("id")

    def search_state(self, sid: str) -> dict:
        # GET /searches/{id}  -> {id, state, isComplete, responseCount, fileCount, ...}
        ...

    def search_responses(self, sid: str) -> list:
        # GET /searches/{id}/responses -> [SearchResponseItem]; each has username, hasFreeUploadSlot,
        #   uploadSpeed, queueLength, files:[{filename,size,extension,bitRate,bitDepth,length,sampleRate}]
        # NOTE: this maps DIRECTLY onto build_candidate()'s expected shape (folder from filename path,
        #   freeUploadSlots from hasFreeUploadSlot, uploadSpeed, the files[] attrs). [VERIFIED: searches.py]
        ...

    def enqueue(self, username: str, files: list[dict]) -> None:
        # POST /transfers/downloads/{username}  body = the file dicts (each {"filename","size"})
        #   taken STRAIGHT from a search response. [VERIFIED: transfers.py enqueue()]
        ...

    def transfer(self, username: str, transfer_id: str) -> dict:
        # GET /transfers/downloads/{username}/{id} -> {state, bytesTransferred, percentComplete,
        #   averageSpeed, size, bytesRemaining, ...} — the stall-detection signal. [VERIFIED: TransferedFile fields]
        ...

    def cancel(self, username: str, transfer_id: str, remove: bool = True) -> None:
        # DELETE /transfers/downloads/{username}/{id}?remove=true. [VERIFIED: transfers.py cancel_download]
        ...
```

### Pattern 2: Per-item staging via slskd batchId (the path-identity primitive)
**What:** slskd computes a completed download's destination as `downloads/{batchId}/{filename}` when a `batchId` is set, else `downloads/{remote-folder}/{filename}` (NO username subdir). Set a deterministic per-acquisition batch id so slskd's OWN write lands in an isolated, predictable subdir.
**When to use:** Every download, to satisfy IMPORT-01 (isolated per-item staging) without Curator moving files around behind slskd's back.
**Example:**
```
# Source: VERIFIED from slskd DownloadService.cs path-building logic.
#   batchId branch:    FileSafety.CombineSafely(Directories.Downloads, batchId)  -> downloads/{batchId}/...
#   no-batch (legacy): Filename.ToLocalFilename(Downloads) -> downloads/{remote-folder}/file.ext  (no username dir)
#
# Curator's staging dir for item (lidarr,1234):  <downloads-root>/curator-lidarr-1234/
#   - slskd's downloads-root is configured INSIDE /data (e.g. /data/downloads/soulseek)
#   - so the absolute path /data/downloads/soulseek/curator-lidarr-1234 is IDENTICAL in
#     slskd, curator, AND the *arr container (single /data mount) -> hardlink-capable Move works.
#   - the *arr GET manualimport `folder` param is EXACTLY this absolute path string.
```
**Open verification (see Open Questions):** confirm against the live slskd 0.25.x how `batchId` is supplied on `POST /transfers/downloads/{username}` (per-file field vs a download-batch concept) — if batchId is not directly settable via the enqueue body, fall back to: let slskd download into `downloads/{remote-folder}/…`, then point the *arr GET manualimport `folder` at that resolved subdir (still inside `/data`, still hardlink-capable, still explicit ManualImport). Either way IMPORT-01/02 hold; the batchId route is just cleaner isolation.

### Pattern 3: Explicit ManualImport via the *arr-agnostic adapter (NOT a blind rescan)
**What:** Implement the three already-stubbed `base.ArrAdapter` methods. `manual_import_candidates(folder)` → `GET /api/v1/manualimport`; core filters to wanted files; `execute_import(decisions)` → `POST /api/v1/command` ManualImport Move; `verify_imported(item)` → re-query.
**When to use:** The import step for both Lidarr (primary) and Readarr (best-effort) — one path (D-10).
**Example:**
```python
# Source: field names VERIFIED from Lidarr source —
#   ManualImportController.GetMediaFiles(folder, downloadId, artistId,
#       filterExistingFiles=true, replaceExistingFiles=true)
#   ManualImportResource: { Id, Path, Name, Size, Artist, Album, AlbumReleaseId,
#       Tracks[], Quality, ReleaseGroup, QualityWeight, DownloadId, IndexerFlags,
#       Rejections[], AudioTags, AdditionalFile, ReplaceExistingFiles, DisableReleaseSwitching }
def manual_import_candidates(self, folder: str, download_id: str | None = None) -> list:
    r = self._client.get(f"{self._base}/api/v1/manualimport", headers=self._headers,
        params={"folder": folder, "downloadId": download_id,
                "filterExistingFiles": "true", "replaceExistingFiles": "true"}, timeout=60.0)
    r.raise_for_status()
    return r.json()   # list[ManualImportResource]; CALLER filters to wanted (empty rejections, real Tracks)

def execute_import(self, decisions: list) -> None:
    # POST /api/v1/command  (returns 201 Created)
    body = {
        "name": "ManualImport",
        "importMode": "move",           # atomic hardlink within /data (D-09); slskd downloads stay until purge
        "files": [
            {
                "path": d["path"],                       # the file's absolute staging path (inside /data)
                "artistId": d["artist"]["id"],           # from the GET mapping
                "albumId": d["album"]["id"],
                "albumReleaseId": d["albumReleaseId"],   # which release/edition to attach to
                "trackIds": [t["id"] for t in d["tracks"]],
                "quality": d["quality"],                 # echo the resolved QualityModel back verbatim
                "indexerFlags": d.get("indexerFlags", 0),
                "disableReleaseSwitching": False,
                "downloadId": d.get("downloadId"),
            }
            for d in decisions
        ],
    }
    r = self._client.post(f"{self._base}/api/v1/command", headers=self._headers, json=body, timeout=60.0)
    r.raise_for_status()
```
> **CONFIDENCE NOTE:** the `files[]` element keys (`path, artistId, albumId, albumReleaseId, trackIds, quality, disableReleaseSwitching, indexerFlags`) are assembled from VERIFIED Lidarr `ManualImportResource`/`ManualImportFile` source property names, NOT from a captured live POST. Lidarr's deserializer is camelCase-lenient, but the planner MUST include a Wave-0 / live-NAS verification task that captures one real browser-DevTools ManualImport POST (gated by D-11's live precondition) to confirm the exact envelope before the loop is trusted in production. Treat `importMode` case ("move" vs "Move") and whether `quality` must be the full echoed `QualityModel` as **[ASSUMED]** until that capture.

### Anti-Patterns to Avoid
- **`DownloadedAlbumsScan` / blind drop-folder rescan (the Soularr way):** forbidden by D-09. It imports whatever Lidarr guesses, can kick off a full library rescan (Lidarr #3450), and gives zero per-file control. Use the explicit ManualImport command.
- **`importMode:"Copy"`:** leaves a cross-FS copy and defeats the atomic-hardlink contract. Use Move (paths are identical across `/data`).
- ***arr field names leaking into `core/acquire.py`:** the firewall (grep-tested over all core modules) forbids `folder`/`downloadId`/`albumReleaseId`/`importMode`/`X-Api-Key`/`X-API-Key` as quoted JSON keys in core. Keep ALL of it in `adapters/`. `core/acquire.py` speaks only `GapItem`/`Candidate`/`GateResult` + neutral progress/result shapes.
- **Reading `bytesTransferred`/`state` in core:** normalize slskd transfer vocabulary to a neutral progress shape in the client; core sees only "made progress: yes/no" + "terminal: completed/failed".
- **Blocking the purge on Plex (D-04):** Plex refresh is fire-and-forget; never await it before purging staging or marking imported.
- **Treating "Completed" transfer state as "imported" (D-03):** a finished download is NOT a done item — only the *arr re-query confirms DONE.
- **Holding a slot on a stalled peer (D-01/ACQ-03):** always cancel (`DELETE …?remove=true`) before falling to the next candidate.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| File→track mapping for import | A custom MusicBrainz/tag matcher to decide which track each file is | `GET /api/v1/manualimport` (Lidarr does the mapping) | Lidarr already resolves files→tracks+quality+release; re-implementing it is the entire class of bugs Manual Import exists to avoid |
| Atomic move into the library | `shutil.move` from Curator into `/data/media/music` | `importMode:"Move"` on the ManualImport command | Lidarr owns the library layout, naming, hardlink, and DB record; a Curator-side move would orphan the *arr DB |
| slskd transfer state machine | A custom percent/byte poller with hand-rolled state strings | slskd's `state` + `bytesTransferred`/`percentComplete` fields | slskd already exposes transfer state + bytes; just diff `bytesTransferred` across polls for the D-01 stall check |
| HTTP retry/backoff for flaky slskd | A bespoke retry loop in the client | The existing `CircuitBreaker` pattern (wrap the client) + per-call timeouts | Already built and battle-tested for Readarr; reuse the seam (full backoff/do-not-retry is Phase 5) |
| Search-result → Candidate normalization | A new parser | `Candidate.from_slskd` / `build_candidate` (EXISTS, Phase 3) | The factory is already `.get()`-defensive and feeds the gate; slskd `SearchFile` fields map 1:1 onto it |

**Key insight:** Phase 4 is overwhelmingly *integration glue*, not new algorithms. Every hard decision (which album, which quality, which file→track) is already owned by Phase 3's gate or by Lidarr's Manual Import. The net-new code is a thin slskd client, a staging lifecycle, and one orchestration loop — keep all three boring and defensive.

## Runtime State Inventory

> Phase 4 is a greenfield mechanism (new code), not a rename/refactor. This section is included only to record the one stateful surface it MODIFIES.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | SQLite ledger `items.status` enum currently `(pending,searching,grabbed,downloaded,imported,unavailable,blacklisted)` — lacks `downloading/importing/quarantined/stuck` | Migration `0002`: widen the CHECK enum (SQLite needs table-rebuild or a relaxed CHECK) + add a `staged_files` table |
| Live service config | slskd `directories.downloads` must resolve inside `/data` (e.g. `/data/downloads/soulseek`) for path identity | Verify (not configure) at live-test time; document the required slskd.yml value |
| OS-registered state | None — no OS scheduler/registration in Phase 4 (daemon is Phase 5) | None — verified by scope (CONTEXT.md deferred section) |
| Secrets/env vars | `SLSKD_API_KEY` (exists, Phase 1), `SLSKD_URL` (exists), need a `PLEX_TOKEN` + `PLEX_URL` + per-section id env for D-04 | Add `PLEX_*` to `Settings.from_env()`; SLSKD_* already present |
| Build artifacts | None — pure code addition; no package rename | None |

**Nothing found in category (OS-registered state):** None — verified by Phase 4 scope (no daemon, no scheduler; that is Phase 5).

## Common Pitfalls

### Pitfall 1: Path-identity mismatch → ManualImport rejects every file
**What goes wrong:** `GET /api/v1/manualimport?folder=…` returns rejections like "file not found" or imports nothing, because the `folder` path Curator passed is not the same absolute path the *arr container can see, or slskd wrote the file somewhere else.
**Why it happens:** slskd's `directories.downloads` is outside `/data`, or Curator guessed the per-item subdir wrong (remember: no username subdir; it's `downloads/{batchId or remote-folder}/…`), or a host-path (`/volume1/data/…`) was passed instead of the container path (`/data/…`).
**How to avoid:** slskd downloads-root MUST be inside `/data`; always address the staging dir by its `/data/...` container path (the Phase-1 hardlink test proves identity); after the transfer completes, LIST the staging dir (or read slskd's reported local path) to discover the actual subdir before calling manualimport, rather than assuming it.
**Warning signs:** ManualImport mapping returns `[]` or every item has a non-empty `rejections`; import command 201s but the item never leaves the wanted list.

### Pitfall 2: `importMode:"Copy"` (or omitting it) leaves junk + breaks hardlink
**What goes wrong:** files get COPIED into the library (slow cross-FS, double disk) and the staging copy lingers, defeating the clean-import goal.
**Why it happens:** the default import mode is Auto/Copy in some flows; Move must be explicit.
**How to avoid:** always send `importMode:"Move"`; verify with the Phase-1 hardlink proof that Move within `/data` is an atomic rename.
**Warning signs:** disk usage doubles per import; staging dir non-empty after a "successful" import.

### Pitfall 3: Calling manualimport before the transfer is fully Completed
**What goes wrong:** Lidarr maps a partial/incomplete file, imports a truncated track, or sees the `.incomplete` temp.
**Why it happens:** slskd writes to `directories.incomplete` first and only moves to `downloads/` on completion; polling for `percentComplete==100` is not the same as the file being in its final location.
**How to avoid:** gate the import on the transfer `state` being the terminal Completed value (not just bytes==size); then verify the file exists at its final `downloads/...` path.
**Warning signs:** imported tracks shorter than expected; `.incomplete` artifacts in staging.

### Pitfall 4: Stall detection false-positives on slow-but-legitimate peers
**What goes wrong:** a genuinely slow Soulseek peer gets cancelled because a fixed deadline fired (the thing D-01 explicitly rejects).
**Why it happens:** using wall-clock elapsed instead of byte-progress.
**How to avoid:** D-01 — track `bytesTransferred` across polls; only declare dead if it has not increased for N minutes (configurable, default ~10). Reset the no-progress timer on ANY byte increase. Use `time.monotonic()` (the breaker already models this).
**Warning signs:** cancellations of transfers that were slowly progressing; legitimate large FLAC albums never finishing because they were killed early.

### Pitfall 5: Readarr import fault gating music (ARR-02 regression)
**What goes wrong:** a Readarr ManualImport 5xx/timeout raises out of the import path and stalls/aborts the loop for a music item.
**Why it happens:** copying Lidarr's `raise_for_status` posture into Readarr's import methods.
**How to avoid:** Readarr's import methods MUST swallow→degrade (mirror `readarr.py`'s existing `_paged`/`get_quality_profile` pattern); a book import fault returns a "failed, quarantine" result for that book only, never raises. The *arr-agnostic adapter keeps one import path but two fault postures (primary vs best-effort).
**Warning signs:** a Readarr outage shows up as a music-item failure; the loop stops processing albums.

### Pitfall 6: Re-detect upsert clobbering an in-flight acquisition status
**What goes wrong:** a Phase-2 detection re-run resets a `downloading`/`importing` item back to `pending`, causing a re-download.
**Why it happens:** the upsert's ON CONFLICT must not touch `status` — this is already guaranteed (`repo.upsert_gap` omits `status`), but the NEW acquisition states must be added to the CHECK enum so `set_status` can write them without an IntegrityError.
**How to avoid:** migration `0002` widens the enum FIRST; the existing status-preserving upsert then protects the new states for free.
**Warning signs:** `sqlite3.IntegrityError` on `set_status('downloading', …)`; items re-acquired after a detection pass.

### Pitfall 7: slskd reached by the wrong host
**What goes wrong:** Curator can't reach slskd because it tried `http://slskd` (container name).
**Why it happens:** slskd runs `network_mode: service:gluetun`, so it has NO synobridge identity; it is reachable ONLY via gluetun's published port.
**How to avoid:** use `SLSKD_URL=http://<NAS-IP>:5030` (DEPLOY.md default `192.168.86.37:5030`), never a container name. Lidarr/Readarr/Plex, by contrast, ARE reached by container name on synobridge. [VERIFIED: CLAUDE.md, DEPLOY.md]
**Warning signs:** connection refused / DNS failure to slskd while *arr calls succeed.

## Code Examples

### Collection-window search (D-07)
```python
# Source: pattern derived from slskd searches API (VERIFIED endpoints) + D-07 fixed-window rule.
sid = slskd.search(query)
deadline = time.monotonic() + settings.acq_search_window_seconds   # default ~12s
while time.monotonic() < deadline:
    st = slskd.search_state(sid)
    if st.get("isComplete"):
        break
    time.sleep(1.0)
responses = slskd.search_responses(sid)            # accumulated trickle
candidates = [Candidate.from_slskd(_flatten(r)) for r in responses]  # EXISTS (Phase 3)
result = gate.evaluate(candidates, manifest, profile)   # ONCE over the full set (EXISTS)
```

### No-progress stall watch (D-01)
```python
# Source: D-01 byte-delta rule + time.monotonic (mirrors breaker.py clock discipline).
last_bytes, last_progress_at = -1, time.monotonic()
while True:
    t = slskd.transfer(username, transfer_id)
    state = t.get("state", "")
    if "Completed" in state and "Succeeded" in state:   # terminal success (verify exact enum, see Open Q)
        return "completed"
    if "Failed" in state or "Errored" in state or "Cancelled" in state:
        return "failed"
    b = t.get("bytesTransferred") or 0
    now = time.monotonic()
    if b > last_bytes:
        last_bytes, last_progress_at = b, now
    elif now - last_progress_at > settings.acq_stall_seconds:   # default ~600s
        slskd.cancel(username, transfer_id, remove=True)
        return "stalled"
    time.sleep(settings.acq_poll_seconds)   # default ~5s
```

### Quarantine + TTL purge (D-06)
```python
# Source: D-06 quarantine-with-TTL; stdlib shutil/pathlib (no library).
def quarantine(staging_dir: Path, reason: str, repo, conn, item) -> None:
    dest = Path(settings.quarantine_dir) / f"{item.arr_app}-{item.arr_id}-{int(time.time())}"
    shutil.move(str(staging_dir), str(dest))
    repo.record_quarantine(conn, item, str(dest), reason)   # NEW DAO; ledger row, surfaced later (Phase 6)
    repo.set_status(conn, item.arr_app, item.arr_id, "quarantined")

def purge_expired_quarantine() -> None:
    cutoff = time.time() - settings.quarantine_ttl_seconds   # default ~7 days
    for d in Path(settings.quarantine_dir).iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
```

### Plex fire-and-forget refresh (D-04)
```python
# Source: VERIFIED Plex URL command — GET /library/sections/{id}/refresh?path=&X-Plex-Token=
def plex_refresh(section_id: str, path: str) -> None:
    try:
        httpx.get(f"{settings.plex_url}/library/sections/{section_id}/refresh",
                  params={"path": path, "X-Plex-Token": settings.plex_token}, timeout=10.0)
    except Exception as e:                      # D-04: never warn loudly, never block
        log.debug("plex refresh hiccup (ignored): %s", e)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Soularr `DownloadedAlbumsScan` blind rescan | Explicit `ManualImport` command with per-file `files[]` + Move | This project's core thesis | Per-file control, no full-rescan side effects, the D-09 contract |
| slskd legacy download layout `downloads/{remote-folder}/…` | `batchId` routing → `downloads/{batchId}/…` | slskd recent versions (`DownloadService.cs` batch branch) | Clean per-item staging isolation if batchId is settable on enqueue (verify on live 0.25.x) |
| slskd API instability | API still `/api/v0`, header `X-API-Key`, server 0.25.1 (May 2026) | current | Endpoints stable enough to target; semver not yet honored (pin behavior expectations to 0.25.x) |

**Deprecated/outdated:**
- Reaching slskd by container name (`http://slskd`): wrong — it shares gluetun's netns and is reachable only via the published port.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The ManualImport POST `files[]` element keys are `{path, artistId, albumId, albumReleaseId, trackIds, quality, indexerFlags, disableReleaseSwitching, downloadId}` and `importMode` accepts `"move"` | Pattern 3 / Code Examples | Import command 201s but imports nothing / wrong release — **MUST be confirmed by a live DevTools capture (gated by D-11)** before production trust |
| A2 | slskd `batchId` is settable on the enqueue request so a download lands in `downloads/{batchId}/…` | Pattern 2 | Per-item isolation falls back to the `downloads/{remote-folder}/…` route (still works, less clean) — low risk, has a documented fallback |
| A3 | The terminal-success transfer `state` string contains both "Completed" and "Succeeded" (slskd uses a compound flag-style state) | Stall watch code | Wrong terminal check → import fired early or never — verify the exact enum on live slskd |
| A4 | `SearchResponseItem.files[]` maps onto `build_candidate` with a `folder` derivable from the file path | Pattern 1 | `from_slskd` may need a small flatten/adapt shim (the factory IS already defensive, low risk) |
| A5 | Readarr exposes the same `/api/v1/manualimport` + ManualImport command shape (book deltas: bookId/editionId/authorId) | Pattern 3 / IMPORT-02 | Books-only; Readarr is best-effort and degrades — never gates music (ARR-02), so wrong → book skipped, not a music failure |
| A6 | Plex music library is one section reachable at `PLEX_URL` by container name on synobridge with a static section id | D-04 | Plex refresh no-ops quietly (D-04 fire-and-forget) — *arr remains source of truth, so low impact |
| A7 | `slskd-api` PyPI wrapper is legitimate (used only as a doc reference, not adopted) | Package audit | None if not adopted; if adopted, gate behind checkpoint:human-verify |

**Empty?** No — these seven assumptions are the planner's confirmation surface. A1 and A3 are the load-bearing ones and SHOULD become explicit live-NAS verification tasks (gated by the D-11 precondition).

## Open Questions

1. **Exact slskd terminal transfer-state enum (A3)**
   - What we know: `TransferedFile` exposes a `state` field; slskd historically uses compound states like `Completed, Succeeded` / `Completed, Errored` / `Completed, Cancelled`.
   - What's unclear: the precise string(s) for success vs each failure on slskd 0.25.x.
   - Recommendation: a Wave-0 live-NAS probe task (after D-11 shares are up) that submits one real search+download and records the observed `state` strings; encode them as named constants in the client.

2. **slskd `batchId` settability on enqueue (A2)**
   - What we know: `DownloadService.cs` routes a download with a `batchId` into `downloads/{batchId}/…`.
   - What's unclear: whether the `POST /transfers/downloads/{username}` body accepts a batch id, or whether batching is a separate concept.
   - Recommendation: try the batchId route; if not directly settable, use the documented fallback (resolve the actual `downloads/{remote-folder}/…` subdir post-completion and point manualimport there). Either path satisfies IMPORT-01.

3. **Exact ManualImport POST envelope + importMode casing (A1)**
   - What we know: verified Lidarr `ManualImportResource`/controller field names; `DownloadedAlbumsScan` is the WRONG approach.
   - What's unclear: the exact JSON the UI POSTs (key casing, whether `quality` must be the full echoed `QualityModel`, whether `albumReleaseId` is required per-file).
   - Recommendation: capture one real ManualImport POST via browser DevTools on the live Lidarr (a 5-minute manual step the owner can do at D-11 live-test time) and pin the client to that exact shape. This is the single highest-value verification in the phase.

4. **Readarr ManualImport deltas (A5)**
   - What we know: Readarr mirrors Servarr v1; book identity is bookId/editionId/authorId rather than album/track.
   - What's unclear: exact resource/command field names (Readarr is unmaintained; less reliable docs).
   - Recommendation: implement best-effort with swallow→degrade; do NOT block the phase on perfecting it (ARR-02 — books never gate music). A book import fault → quarantine that book only.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| httpx | slskd/*arr/Plex client | ✓ (pinned) | 0.28.1 | — |
| respx | offline adapter tests | ✓ (dev) | 0.22.0 | — |
| slskd server | live download test | ✓ on NAS (Phase 1) | 0.25.x | tests use respx fakes; live test gated by D-11 |
| Lidarr | import + verify | ✓ on NAS | v1 | respx fakes for unit/integration |
| Readarr | books (best-effort) | partial (keyless/empty on NAS per Phase-2 UAT) | — | degrade→skip (ARR-02); books optional |
| Plex | scan trigger | assumed on NAS | — | fire-and-forget; no-op if absent (D-04) |
| slskd shares configured | live download test ONLY | ✗ until manually set (D-11) | — | **BLOCKING for live test only** — see D-11 precondition; fakes don't need it |

**Missing dependencies with no fallback (live test only):**
- slskd shares (`shared file count > 0`) — manual slskd.yml config + verify before the first live download (D-11 / RESEARCH-SEED.md). Does NOT block code/tests.

**Missing dependencies with fallback:**
- Readarr (degrade→skip), Plex (fire-and-forget no-op). Neither blocks the music path.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (+ respx for httpx mocking) [VERIFIED: pyproject.toml, requirements-dev.txt] |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (pythonpath=["app"], testpaths=["app/tests"]) |
| Quick run command | `pytest app/tests/test_slskd_client.py app/tests/test_acquire.py -x -q` |
| Full suite command | `pytest -q` (currently 128 passing; Phase 4 adds to it) |

> NOTE: the local dev sandbox is Python 3.9 + offline, so the suite runs in CI/NAS (3.12). Phase 4 code that imports httpx must stay import-clean in 3.9 (lazy imports, mirroring `gap_detector.build_adapters`). The slskd client + acquire loop should be unit-testable with respx fakes WITHOUT a live slskd.

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ACQ-01 | search submit + collection-window poll builds Candidates | unit (respx) | `pytest app/tests/test_slskd_client.py::test_search_collection_window -x` | ❌ Wave 0 |
| ACQ-02 | enqueue + transfer-watch to completion; staging path computed | unit (respx) | `pytest app/tests/test_slskd_client.py::test_enqueue_and_watch -x` | ❌ Wave 0 |
| ACQ-03 | no-byte-progress for N → cancel + fall to next candidate; exhausted → stuck | unit (fake clock) | `pytest app/tests/test_acquire.py::test_stall_falls_to_next_then_stuck -x` | ❌ Wave 0 |
| IMPORT-01 | staging dir is per-item, inside /data, path-identical (string equality + tmp_path hardlink) | unit | `pytest app/tests/test_staging.py::test_per_item_path_identity -x` | ❌ Wave 0 |
| IMPORT-02 | GET mapping → filter wanted → POST ManualImport Move (not DownloadedAlbumsScan) | unit (respx) | `pytest app/tests/test_lidarr_adapter.py::test_manual_import_move -x` | ❌ Wave 0 |
| IMPORT-03 | re-query confirms item left wanted list = DONE; "downloaded" ≠ "imported" | unit (respx) | `pytest app/tests/test_lidarr_adapter.py::test_verify_imported -x` | ❌ Wave 0 |
| IMPORT-04 | Plex refresh fired fire-and-forget; a Plex 5xx does NOT fail the loop | unit (respx) | `pytest app/tests/test_plex.py::test_refresh_swallows_fault -x` | ❌ Wave 0 |
| IMPORT-05 | success→purge staging; failure→quarantine+record; TTL purge | unit (tmp_path) | `pytest app/tests/test_acquire.py::test_purge_and_quarantine -x` | ❌ Wave 0 |
| (firewall) | no *arr field names in core/acquire.py | grep test | extend the existing `ARR_FIELD_NAMES` grep in test_gate.py / a new test | ⚠️ extend |
| (ARR-02) | Readarr import fault → that book quarantined, music unaffected | unit | `pytest app/tests/test_acquire.py::test_readarr_import_fault_does_not_gate_music -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest app/tests/test_slskd_client.py app/tests/test_acquire.py app/tests/test_staging.py -x -q` (the Phase-4 fast slice)
- **Per wave merge:** `pytest -q` (full suite green, incl. the firewall grep)
- **Phase gate:** full suite green + the live-NAS UAT (gated by D-11) before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `app/tests/test_slskd_client.py` — covers ACQ-01/02/03 (respx fakes of `/searches`, `/transfers/downloads`)
- [ ] `app/tests/test_acquire.py` — covers ACQ-03 + IMPORT-03/05 + ARR-02 (the orchestration loop with fake clients + fake clock)
- [ ] `app/tests/test_staging.py` — covers IMPORT-01 (tmp_path path-identity + hardlink/Move semantics)
- [ ] `app/tests/test_plex.py` — covers IMPORT-04 (fire-and-forget, swallow fault)
- [ ] Extend `app/tests/test_lidarr_adapter.py` / `test_readarr_adapter.py` — IMPORT-02/03 (respx ManualImport)
- [ ] slskd/*arr **fixtures**: `app/tests/fixtures/slskd/` (one search-responses JSON, one transfer JSON in each state) + `fixtures/manualimport/` (one GET mapping, one expected POST body) — mirrors the Phase-2 *arr fixture pattern, keeps Phase 4 offline-provable
- [ ] Live-NAS probe task (gated by D-11): capture real slskd `state` strings + one real ManualImport POST envelope → pin A1/A3
- [ ] No framework install needed (pytest/respx already pinned)

## Security Domain

> `security_enforcement` is not set to false in config.json → included.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | `X-API-Key` to slskd, `X-Api-Key` to *arr, `X-Plex-Token` to Plex — all read from env ONLY (existing `Settings` discipline: never logged, never baked, `.env` gitignored) |
| V3 Session Management | no | Stateless API keys; no sessions |
| V4 Access Control | partial | slskd reachable only via gluetun published port, LAN/Tailscale-only firewall (Phase 1); no new WAN surface |
| V5 Input Validation | yes | slskd search responses + *arr manualimport responses are untrusted JSON → `.get()`-defensive parsing (existing SP-3 pattern); a malformed peer filename must not crash the loop or escape the staging dir |
| V6 Cryptography | no (this phase) | TLS termination is the deployment's concern; no crypto implemented here |
| V12 File handling | **yes** | **Path traversal:** a malicious peer can name a file `../../etc/x`; the staging path MUST be confined (resolve + assert the final path is under the staging root before any move/import); `shutil.rmtree`/`move` must never operate outside the configured staging/quarantine roots |

### Known Threat Patterns for {Python httpx client + filesystem staging + SQLite}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malicious peer filename → path traversal out of staging | Tampering / Elevation | Resolve the destination path and assert `staging_root in resolved.parents` before any write/move/rmtree; reject otherwise |
| Untrusted slskd/*arr JSON crashes the loop | Denial of Service | `.get()`-defensive parsing (SP-3); per-call timeouts; one malformed result skipped, not fatal |
| `rmtree` purging the wrong dir (e.g. a mis-set quarantine root = `/data`) | Tampering / DoS | Never purge a path that isn't strictly under the configured staging/quarantine root; refuse to purge `/`, `/data`, `/data/media` |
| API key leakage in logs/exceptions | Info Disclosure | Keep keys in headers only; never log the headers dict or the full request; existing Settings never logs keys |
| SQL injection via peer-supplied strings into the ledger | Tampering | All ledger writes already use `?` placeholders (existing `repo.py` discipline) — extend the same to the new `staged_files` DAO |
| Readarr fault escalating into a music-path failure | DoS (availability) | Best-effort swallow→degrade posture on Readarr import methods (ARR-02) — structural, not optional |

## Project Constraints (from CLAUDE.md)

- **Platform:** `linux/amd64` only (Synology DS423+ J4125). No arch-specific code.
- **Networking:** slskd reached via gluetun's published port (`http://<NAS-IP>:5030`), NEVER by container name. Lidarr/Readarr/Plex reached by container name on synobridge.
- **Persistence:** `/volume1` bind-mounts, correct PUID/PGID (`1031:65536`), SQLite state.
- **Quality:** defer to Lidarr quality profiles/cutoffs; gate BEFORE download (Phase 3, consumed unchanged); never downgrade.
- **Behavior:** strictly fallback-only, fully hands-off; no manual approval/interaction (Phase 4 owner takes ZERO manual actions per D-09 note).
- **Import & cleanup:** isolated per-item staging → import ONLY wanted files via *arr Manual Import API → auto-purge staging. Download/import paths IDENTICAL across containers (single `/data`, atomic hardlinks — the #1 import-failure cause).
- **Sharing:** mandatory (but manual for Phase 4 per D-11; automation is Phase 5).
- ***arr-agnostic firewall:** no *arr field names in core; adapters normalize. Import-command payloads stay adapter-local (locked, grep-tested).
- **Books (Readarr):** best-effort, isolated behind the adapter; never gates/blocks/destabilizes music.

## Sources

### Primary (HIGH confidence)
- Codebase: `app/adapters/{base,lidarr,readarr,breaker}.py`, `app/core/{candidate,gate,selector}.py`, `app/state/{db,repo,schema.sql}.py`, `app/config.py`, `app/main.py`, `app/core/gap_detector.py` — the patterns Phase 4 extends.
- `.planning/phases/phase-4/{04-CONTEXT.md,RESEARCH-SEED.md}`, `.planning/{REQUIREMENTS,ROADMAP,STATE}.md`, `CLAUDE.md`, `DEPLOY.md` — constraints + decisions.
- Lidarr source (develop): `ManualImportResource.cs` (response/POST item fields), `ManualImportController.cs` (GET signature) — VERIFIED field names. https://github.com/lidarr/Lidarr
- slskd source: `DownloadService.cs` path-building (batchId vs legacy folder routing) — VERIFIED disk layout. https://github.com/slskd/slskd
- slskd-python-api source (`searches.py`, `transfers.py`) — VERIFIED endpoint paths, verbs, and enqueue body. https://github.com/bigoulours/slskd-python-api ; https://slskd-api.readthedocs.io/
- Plex URL commands — VERIFIED scan endpoint. https://support.plex.tv/articles/201638786-plex-media-server-url-commands/ ; https://www.plexopedia.com/plex-media-server/api/library/scan/

### Secondary (MEDIUM confidence)
- slskd config docs (`directories.downloads` / `directories.incomplete`, on-disk layout). https://github.com/slskd/slskd/blob/master/docs/config.md
- slskd releases (server 0.25.1, May 2026; `/api/v0`, `X-API-Key`). https://github.com/slskd/slskd/releases
- Lidarr ManualImport API issue #5647 (confirms barebones official docs; payload must be derived from source). https://github.com/lidarr/Lidarr/issues/5647
- Soularr `soularr.py` — confirms the `DownloadedAlbumsScan` anti-pattern Curator must NOT copy. https://github.com/mrusse/soularr

### Tertiary (LOW confidence — flagged for live verification)
- Exact ManualImport POST envelope casing/required-fields (A1) — needs a live DevTools capture.
- Exact slskd terminal transfer-state strings (A3) — needs a live probe.
- slskd `batchId` settability on enqueue (A2) — needs a live probe (documented fallback exists).
- Readarr ManualImport deltas (A5) — Readarr unmaintained; best-effort only.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependency; httpx/respx already pinned and proven; pattern is a direct extension of existing adapters.
- slskd endpoints/verbs/paths: HIGH — verified from the wrapper source and slskd source.
- slskd transfer-state enum + batchId enqueue: MEDIUM — field names verified, exact string values/settability need a live probe (A2/A3, documented fallbacks).
- *arr ManualImport field names: HIGH (source-verified); exact POST envelope: MEDIUM (A1, needs a live capture).
- Architecture/firewall/state-machine: HIGH — mirrors the established Phase-2/3 composition + firewall + migration patterns.
- Pitfalls/security: HIGH — derived from the explicit project constraints + verified API behaviors.

**Research date:** 2026-05-31
**Valid until:** 2026-06-30 (stable APIs; re-check slskd if it crosses a major version, and confirm A1/A3 on the live NAS at D-11 time)
