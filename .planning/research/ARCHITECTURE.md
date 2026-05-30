# Architecture Research

**Domain:** Autonomous, fallback-only Soulseek (slskd) gap-filler for Lidarr/Readarr — Synology homelab, Docker
**Researched:** 2026-05-29
**Confidence:** HIGH on container topology + networking (live-verified against gluetun/slskd sources); HIGH on component design; MEDIUM on exact *arr endpoint/command names (verify at build)

> **Verification status:** The hardest part — the gluetun/slskd/synobridge networking — was **live-verified
> this session** against the gluetun wiki, slskd VPN docs, and the gluetun control-server API reference (see
> Sources). One finding materially changes the design vs. naive assumptions: **slskd has built-in gluetun
> port-forwarding integration (since ~v0.24.4), so Curator does NOT need to build a port-sync component.**
> That simplification is baked into the component list and build order below. Items still marked MEDIUM are
> *arr-specific endpoint names — stable but worth confirming against each app's live `/swagger`.

---

## Standard Architecture

Curator is a **three-container unit** deployed by one `docker-compose.yml`, layered onto the existing
`synobridge` network where Lidarr/Readarr/Prowlarr/SABnzbd/Plex already operate.

### System Overview

```
                         Synology DS423+  (linux/amd64, Container Manager)
┌──────────────────────────────────────────────────────────────────────────────┐
│  docker network: synobridge  (external user-defined bridge)                    │
│                                                                                │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   (pre-existing)     │
│   │ Lidarr   │  │ Readarr  │  │ Prowlarr │  │ SABnzbd  │                       │
│   └────┬─────┘  └────┬─────┘  └──────────┘  └──────────┘                       │
│        │  HTTP (API, polled — pull model)   │                                  │
│        ▼             ▼                                                         │
│   ┌────────────────────────────────────────────────┐                          │
│   │  curator   (this project — Python monolith)     │──┐  HTTP → slskd API     │
│   │  scheduler · gap-detector · matcher · downloader │  │  at http://gluetun:5030│
│   │  importer · verifier · share-mgr · notifier      │  │                       │
│   │  status API  :8674 (Homepage)                    │  │                       │
│   └────────────────────────────────────────────────┘  │                       │
│                                                        │                       │
│   ┌────────────────────────────────────────────────┐  │                       │
│   │  gluetun  (VPN sidecar: PIA + port-forwarding)  │◄─┘                       │
│   │  member of synobridge; PUBLISHES slskd's ports  │                          │
│   │  control server :8000 (forwarded port, authed)  │      ALL Soulseek        │
│   │   ┌────────────────────────────────────────┐   │      egress via PIA ───► │
│   │   │ slskd (network_mode: service:gluetun)   │   │                 Internet │
│   │   │ shares gluetun netns; API :5030;        │   │                          │
│   │   │ NATIVE gluetun PF: reads ctrl srv,      │   │                          │
│   │   │ self-sets soulseek listen port          │   │                          │
│   │   └────────────────────────────────────────┘   │                          │
│   └────────────────────────────────────────────────┘                          │
│                                                                                │
└──────────────────────────────────────────────────────────────────────────────┘
        Bind mounts → /volume1   (config · db · slskd downloads · shared library · /gluetun)
```

**Core topology decision:** `curator` is a normal `synobridge` member. Only `slskd` shares gluetun's network
namespace. This forces *only Soulseek P2P traffic* through the VPN while Curator's *arr polling and its
Homepage status API stay on the LAN. Curator reaches slskd **through gluetun's published port**
(`http://gluetun:5030`), never `http://slskd:...`. (Confidence: HIGH — confirmed canonical pattern.)

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **scheduler** | Interval loop; triggers gap-detector, dispatcher, reconciler | APScheduler (async) |
| **gap-detector** | Poll Lidarr/Readarr wanted/missing + cutoff; check *arr history/queue for fallback gate → upsert items | httpx *arr client |
| **matcher/scorer** | Build Soulseek queries from metadata; rank candidates (completeness, track-count, format/bitrate, uploader health) | pure functions over slskd results |
| **downloader** | Issue slskd searches; enqueue chosen candidate; track transfer | httpx slskd client → `gluetun:5030` |
| **state/dedup store** | Source of truth: items, attempts, statuses, backoff, share stats; prevents re-work | SQLite + WAL |
| **importer** | Drop completed files in *arr-visible path; trigger *arr import/rescan (Manual Import fallback) | filesystem + *arr command API |
| **verifier/reconciler** | Confirm *arr actually imported; reconcile slskd↔state on startup; handle orphans/stalls | httpx *arr + slskd |
| **share-manager** | Monitor slskd share stats (shared files, upload ratio) to stay a good Soulseek citizen | slskd API |
| **notifier** | Emit grab/import/failure/blocked/stuck events | Apprise |
| **status API** | Read-only JSON for Homepage widget (gap queue, in-flight, stuck, imported 24h, VPN/slskd health) | FastAPI :8674 |

> **Note — no port-sync component.** Early designs of this stack hand-rolled a forwarded-port→slskd sync loop.
> That is now obsolete: **slskd's native gluetun integration** reads gluetun's control server itself and sets
> its own Soulseek listen port. Curator only *observes* health, it does not manage the port. (Confidence: HIGH)

---

## The gluetun / slskd / synobridge Networking Pattern (the tricky part, verified)

### The hard constraint
A container started with `network_mode: service:gluetun` **shares gluetun's network namespace entirely** — no
own IP, no own hostname, and it **cannot also list a `networks:` block** (sharing a netns is mutually exclusive
with joining other networks). So **slskd is invisible on synobridge as a hostname**, and anything slskd exposes
(web UI / API on 5030) must be **published by the gluetun container**, because to Docker those ports live in
gluetun's namespace. (Confidence: HIGH — gluetun wiki "connect a container"; `network_mode: service:gluetun`
shares the entire network stack exclusively.)

### The three containers and their wiring

| Container | Network mode | Reaches | Reachable by |
|-----------|--------------|---------|--------------|
| `gluetun` | `synobridge` member; publishes slskd's ports | Internet via PIA (kill-switched) | Curator + Homepage via its synobridge IP / published ports |
| `slskd` | `network_mode: service:gluetun` (**no** `networks:`) | Internet **only** via gluetun; gluetun ctrl at `localhost:8000` | Only through gluetun's published ports |
| `curator` | `synobridge` member | Lidarr/Readarr/Prowlarr by name; slskd API via `gluetun:5030` | Homepage (status API :8674) |

### Direction 1 — Curator → slskd API (into the VPN namespace)
- slskd listens on `0.0.0.0:5030` **inside gluetun's namespace**.
- The port is published **on the gluetun service**: `ports: ["5030:5030"]` declared on *gluetun*, not slskd.
- gluetun is on synobridge, so Curator calls `http://gluetun:5030/api/v0` (Docker DNS resolves `gluetun`).
- **Config rule:** `SLSKD_URL=http://gluetun:5030`. Never `http://slskd:5030`. (Confidence: HIGH — verified; the
  #1 misconfiguration in this stack. Put it in a code comment.)

### Direction 2 — Soulseek peers → slskd P2P listen (the forwarded port → slskd, now handled natively)
**This is the part that changed.** slskd ships built-in gluetun integration; Curator does not build it.
- PIA hands gluetun a **dynamic** forwarded port (persists 60 days as long as the `/gluetun` dir is bind-mounted).
- gluetun exposes it on its **control server** at `http://localhost:8000/v1/portforward` → `{"port": NNNNN}`
  (inside the shared netns slskd reaches it at `localhost:8000`). **Note:** the old
  `/v1/openvpn/portforwarded` path is deprecated and 301-redirects to `/v1/portforward`. (Confidence: HIGH)
- **slskd native integration** (~v0.24.4+) reads that endpoint and self-sets `soulseek.listen_port`:
  ```yaml
  # on the slskd service:
  SLSKD_VPN: "true"
  SLSKD_VPN_PORT_FORWARDING: "true"
  SLSKD_VPN_GLUETUN_URL: "http://localhost:8000"   # gluetun ctrl, in the shared namespace
  ```
  (Some builds expose `GLUETUN_INCOMING_PORT` as an alternative.) (Confidence: HIGH — verified via slskd VPN
  docs + community confirmations; exact var casing to confirm against the pinned slskd build's docs.)
- **gluetun control-server auth (IMPORTANT, ~v3.40+):** control-server routes are **no longer public by
  default** and require an API key configured via gluetun's `config.toml` (role→routes→apikey). The slskd
  integration must be given that key, and Homepage's gluetun widget likewise. Budget for this in step 0.
  (Confidence: HIGH — verified.)
- **Fallbacks if native integration misbehaves on the pinned versions** (keep as escape hatches, do NOT build
  up-front): gluetun `VPN_PORT_FORWARDING_UP_COMMAND` running a `sed` on `slskd.yml`; or a tiny sidecar like
  `glueforward` / `slskd-port-forward-gluetun-server`. (Confidence: HIGH these exist.)

### Failure / kill-switch semantics
- If gluetun's tunnel drops, slskd loses **all** connectivity (no other route) — desired **fail-closed**: zero
  Soulseek leakage to the bare WAN. (Confidence: HIGH)
- `slskd.depends_on: { gluetun: { condition: service_healthy } }` so slskd starts only after the tunnel is up
  (gluetun ships a healthcheck).
- Curator treats "slskd API unreachable" / "slskd not connected to Soulseek" as **transient infra** → pause
  dispatch + back off, **never burning a per-item attempt** on it.

### Why NOT put Curator in gluetun's namespace too
It would lose clean synobridge DNS to the *arr stack and need *arr APIs republished through gluetun; worse, a
VPN drop would blind Curator to its own controllers. Only slskd goes in the tunnel.

---

## Recommended Project Structure

Single Python process (per STACK.md: Python 3.12, httpx, APScheduler, FastAPI, SQLite). A **modular monolith** —
homelab scale (hundreds–low thousands of items) does not justify multiple services.

```
curator/
├── app/
│   ├── main.py              # entrypoint: start scheduler + FastAPI status API
│   ├── config.py            # Pydantic Settings (env + YAML)
│   ├── scheduler.py         # APScheduler job wiring (the control loop)
│   ├── clients/
│   │   ├── arr.py           # Lidarr/Readarr REST v1 client (httpx) — behind a common interface
│   │   ├── slskd.py         # slskd REST client → http://gluetun:5030/api/v0
│   │   └── gluetun.py       # (optional) read VPN/PF health for status only → :8000/v1/portforward
│   ├── core/
│   │   ├── gap_detector.py  # poll *arr wanted/missing+cutoff, fallback gate → items
│   │   ├── matcher.py       # query building + candidate scoring/quality filter (refuse-rather-than-guess)
│   │   ├── downloader.py    # dispatch searches/downloads to slskd
│   │   ├── importer.py      # drop-folder + rescan; Manual Import fallback
│   │   ├── verifier.py      # confirm *arr import; startup reconciliation; orphan/stall handling
│   │   └── share.py         # read slskd share stats (good-citizen baseline)
│   ├── state/
│   │   ├── db.py            # SQLite connection (WAL), migrations
│   │   ├── models.py        # items, attempts, peers, share_stats, events
│   │   └── repo.py          # dedup/upsert, backoff queries, status reads
│   ├── notify.py            # Apprise wrapper
│   └── api/
│       └── status.py        # FastAPI: Homepage JSON (:8674)
├── migrations/              # idempotent SQL (safe on container recreate)
├── Dockerfile               # multi-stage, python:3.12-slim, linux/amd64, non-root
└── tests/
```

### Structure Rationale
- **`clients/` isolated from `core/`:** only downloader/matcher/verifier touch slskd; only gap-detector/importer/
  verifier touch *arr. The *arr client sits behind a **common interface** so a Readarr replacement is a single
  adapter swap (per FEATURES.md Readarr-retirement hedge).
- **`state/` is the single mutable surface:** every `core/` module is stateless between ticks and reads/writes
  through `repo.py`. Survives restarts trivially (state on a bind mount).
- **`scheduler.py` is glue only:** modules are independently invokable (CLI/test) before being wrapped in the
  loop — enables the build order below.

---

## Architectural Patterns

### Pattern 1: Pull / reconcile loop (not event-driven)
**What:** Curator polls *arr and slskd on intervals and reconciles desired-vs-actual against the state store. No
webhooks from *arr required.
**When to use:** Homelab scale, eventual consistency fine, restart-resilience required.
**Trade-offs:** + Dead-simple recovery (state in SQLite, loop resumes); + no fragile callbacks across the VPN
namespace boundary. − Latency bounded by poll interval (acceptable for a fallback filler).

### Pattern 2: slskd addressed only via `gluetun:5030`
**What:** Never reference `slskd` as a hostname; all slskd HTTP goes to gluetun's published port.
**When to use:** Always, given slskd shares gluetun's netns.
**Trade-offs:** + Correct & only working option. − Non-obvious; must be documented to avoid the `http://slskd` trap.

### Pattern 3: Grace-then-fallback gate enforces "fallback only"
**What:** Items are ineligible until `discovered_at + grace_period` AND *arr history/queue confirm no recent
successful/pending grab. Curator fires only for items still genuinely missing after grace.
**When to use:** Core product constraint — must not race/override the Usenet pipeline.
**Trade-offs:** + Guarantees supplementary behavior, avoids grabbing a worse P2P copy before a good Usenet copy
lands. − Adds intentional latency (the point).

### Pattern 4: Infra-state vs item-failure separation
**What:** VPN/slskd outages pause work without consuming attempts or advancing per-item backoff.
**When to use:** Always.
**Trade-offs:** + A 2-hour VPN blip can't "exhaust" the whole catalog. − One extra outcome class (`infra`).

```python
# reconciler skeleton illustrating infra-vs-item separation
if not slskd.is_connected():            # VPN/slskd down
    repo.pause_dispatch(reason="infra") # no attempt consumed, no backoff bump
    return
outcome = run_attempt(item)             # SUCCESS | no_match | quality | stall | import
repo.record_attempt(item, outcome)      # only these advance backoff/attempt_count
```

---

## Data Flow

### End-to-end flow (with failure / retry / backoff paths)

```
[1] gap-detector polls Lidarr/Readarr wanted/missing + cutoff
        upsert items (status=DISCOVERED, discovered_at=now)
        ▼
[2] GRACE + FALLBACK CHECK — skip until discovered_at + grace_period AND *arr history/queue
    show no recent success/pending grab (let Usenet win first)
        eligible → status=ELIGIBLE
        ▼
[3] dispatcher picks ELIGIBLE items under concurrency cap where next_attempt_at <= now
        status=SEARCHING; open attempt row
        ▼
[4] matcher builds query → slskd search (via gluetun:5030)
        ── no results ─────────────► outcome=no_match
                                      attempt_count++; next_attempt_at=now+backoff; status=ELIGIBLE
                                      at max_attempts → status=EXHAUSTED/dormant + notify
        ▼
[5] scorer ranks; quality filter (track-count gate, name similarity, format/bitrate ≥ *arr profile,
    uploader has free slot). Low confidence → REFUSE (logged, backed off — a valid outcome).
        ── all filtered ──────────► outcome=quality → backoff path [4]
        ▼
[6] downloader enqueues chosen candidate in slskd
        status=DOWNLOADING; attempt.slskd_transfer_id set
        ▼
[7] reconciler polls slskd transfer
        ── stalled > stall_timeout ► cancel in slskd; outcome=stall; try next candidate;
                                      if none left → backoff path [4]
        ── slskd/VPN down ─────────► PAUSE (infra) — no attempt consumed; resume when healthy
        ▼
[8] download COMPLETE on disk  (slskd downloads dir == *arr import dir)
        status=DOWNLOADED
        ▼
[9] importer: drop-folder + trigger *arr rescan (DownloadedAlbumsScan / RescanFolders);
    if not imported after N sec → Manual Import API fallback
        ▼
[10] verifier re-queries *arr — is item still missing?
        ── still missing > import_timeout ► outcome=import; clean files; backoff path [4]
        ── imported OK ───────────────────► status=FILLED/imported + notify(success)
        ▼
[11] state update; share-manager records upload contribution
```

### State machine / backoff
```
items.status:  DISCOVERED → ELIGIBLE → SEARCHING → DOWNLOADING → DOWNLOADED → FILLED
                                  ▲            │            │
                                  └─ backoff ──┴── (no_match│quality│stall│import) ──┘
                                  └─ EXHAUSTED/dormant (max attempts; long re-eligibility sweep)
                                  └─ PAUSED (infra; auto-resume, never counts as attempt)
                                  └─ INVALIDATED (*arr no longer wants / edition changed → purge)
```
- **Backoff:** exponential + jitter per item: `delay = min(base * 2^attempt_count, cap) ± jitter`
  (e.g. 1d → 3d → 7d → 30d, capped). (Confidence: MEDIUM — tune.)
- **Failure classes** (`no_match`, `quality`, `stall`, `import`) bump the same counter but are recorded
  distinctly for diagnostics + provenance. `infra` does neither.
- **Startup reconciliation:** diff Curator state vs slskd live transfers AND *arr satisfaction to avoid
  double-grabs after a restart (per FEATURES.md self-recovery).

### Key data flows
1. **Discovery:** *arr API → gap-detector → `items` (deduped by `(arr_app, arr_id)`).
2. **Acquisition:** `items` → matcher → slskd search/download (via gluetun) → `attempts`.
3. **Handoff:** completed file (shared `/volume1/data` tree) → importer → *arr rescan → verifier → `FILLED`.
4. **Observability:** `state.*` → status API (Homepage polls) and notifier (push on grab/fail/blocked/stuck).
5. **VPN/PF:** handled *inside* the slskd↔gluetun namespace (native integration); Curator only reads health.

---

## State Schema (SQLite + WAL)

```sql
-- One row per monitored gap (album/book), keyed by *arr identity.
CREATE TABLE items (
  id               INTEGER PRIMARY KEY,
  arr_app          TEXT NOT NULL,          -- 'lidarr' | 'readarr'
  arr_id           TEXT NOT NULL,          -- albumId / bookId from *arr
  kind             TEXT NOT NULL,          -- 'album' | 'book'
  artist_or_author TEXT,
  title            TEXT,
  metadata_json    TEXT,                   -- foreign ids, year, tracklist, wanted formats, profile/cutoff
  status           TEXT NOT NULL,          -- DISCOVERED|ELIGIBLE|SEARCHING|DOWNLOADING|
                                           -- DOWNLOADED|FILLED|EXHAUSTED|PAUSED|INVALIDATED
  attempt_count    INTEGER NOT NULL DEFAULT 0,
  next_attempt_at  TEXT,                   -- ISO8601 backoff gate
  discovered_at    TEXT NOT NULL,          -- "first seen wanted" → grace timer
  grace_until      TEXT,                   -- discovered_at + grace_period
  filled_at        TEXT,
  last_error       TEXT,
  UNIQUE(arr_app, arr_id)                  -- dedup: one row per *arr item
);

-- One row per search→download→import cycle (audit, backoff history, provenance).
CREATE TABLE attempts (
  id                INTEGER PRIMARY KEY,
  item_id           INTEGER NOT NULL REFERENCES items(id),
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  outcome           TEXT,                  -- SUCCESS|no_match|quality|stall|import|infra
  slskd_search_id   TEXT,
  slskd_transfer_id TEXT,
  chosen_user       TEXT,                  -- Soulseek peer
  chosen_files_json TEXT,                  -- file list + sizes (provenance: "tried X, 9/12 tracks")
  score             REAL,
  bytes             INTEGER,
  notes             TEXT
);

-- Learned peers: reliability / free-slot history (improves uploader-health scoring).
CREATE TABLE peers (
  username      TEXT PRIMARY KEY,
  last_seen_at  TEXT,
  success_count INTEGER DEFAULT 0,
  fail_count    INTEGER DEFAULT 0,
  avg_speed     REAL
);

-- Sharing/citizenship stats so Curator stays a net contributor (avoid leecher de-prioritization).
CREATE TABLE share_stats (
  id               INTEGER PRIMARY KEY,
  recorded_at      TEXT NOT NULL,
  shared_files     INTEGER,
  shared_bytes     INTEGER,
  uploaded_bytes   INTEGER,
  downloaded_bytes INTEGER
);

-- Event log feeding notifier + status API "recent activity".
CREATE TABLE events (
  id       INTEGER PRIMARY KEY,
  ts       TEXT NOT NULL,
  level    TEXT,                           -- info|warn|error
  item_id  INTEGER REFERENCES items(id),
  message  TEXT
);

CREATE INDEX idx_items_status   ON items(status, next_attempt_at);
CREATE INDEX idx_attempts_item  ON attempts(item_id);
```
`PRAGMA journal_mode=WAL` lets the status API read while the single writer process works; one process means no
write contention. (Confidence: HIGH for fit.)

> Note: there is no `forwarded_port` column — port management lives entirely in the slskd↔gluetun native
> integration, not in Curator's state.

---

## Volume / Bind-Mount Layout (under /volume1)

The import handoff **hinges on path consistency**: slskd's completed-download path must be the same physical
path, addressed identically, that Lidarr/Readarr scan for imports — so import is a hardlink/atomic-move, not a
slow cross-filesystem copy.

```
/volume1/docker/curator/
  ├─ config/                 → curator settings (no secrets)        curator:/config
  └─ db/curator.sqlite       → state store                          curator:/data
/volume1/docker/gluetun/     → gluetun state (forwarded_port, auth) gluetun:/gluetun   ← MUST persist (60-day PF)
/volume1/docker/slskd/
  └─ config/                 → slskd.yml (api key, shares)          slskd:/app
/volume1/data/                                        ← single shared "data root"
  ├─ downloads/soulseek/     → slskd completed downloads
  │                            slskd sees   /downloads
  │                            curator sees /data/shared/downloads/soulseek
  │                            *arr sees    (its existing mount of this same path)
  ├─ music/                  → Lidarr library root / Plex (import target)
  └─ books/                  → Readarr library root (import target)
```

**Path rules (Confidence: HIGH on the principle; MEDIUM on the exact mount strings):**
- Mount the **same `/volume1/data` tree** into slskd, curator, Lidarr, Readarr so all four see one filesystem.
- **Bind-mount gluetun's `/gluetun` dir** so the PIA forwarded port persists 60 days across restarts
  (verified). Without it, the port re-rolls on every restart.
- **Match the existing *arr stack's mount convention** (likely TRaSH-guide single `/data` mount). Do NOT invent
  a second scheme. **Verify the current *arr container mounts before fixing this** — the #1 *arr import failure
  cause.
- Curator's importer relocates only *within* the shared tree (if at all) and then calls the *arr rescan/import
  API — never copies across mounts; never writes the final library path (let *arr rename/move).

---

## Suggested Build Order / Dependency Graph

Build bottom-up; the riskiest infra goes first so failures surface immediately.

```
0. NETWORKING SMOKE TEST  ← highest risk, do FIRST (spike, not a feature)
   gluetun(PIA, /gluetun mounted, control-server apikey) + slskd(network_mode:service:gluetun,
   SLSKD_VPN native PF) + throwaway curl box on synobridge.
   Prove: (a) slskd API reachable at http://gluetun:5030 from synobridge
          (b) slskd egress IP == PIA IP
          (c) slskd self-acquired the PIA forwarded port via gluetun:8000/v1/portforward (authed)
          (d) tunnel drop kills slskd traffic (fail-closed)
        ▼
1. STATE STORE  (schema + idempotent migrations + dedup upsert)   ← everything writes here
        ▼
2. *ARR CLIENT (common interface) + GAP-DETECTOR (wanted/missing+cutoff, fallback gate) [needs 1]
        │
        ├─► 3. SLSKD CLIENT + DOWNLOADER  (search/enqueue/poll)   [needs 0,1]
        │         ▼
        │      4. MATCHER/SCORER + QUALITY FILTER (refuse-rather-than-guess) [needs 3]
        ▼         ▼
5. IMPORTER (drop-folder+rescan, Manual Import fallback) + VERIFIER/RECONCILER [needs 2,3 + paths]
        ▼
6. SCHEDULER  (grace + backoff control loop + startup reconciliation tying 2–5) [needs 2–5]
        │
        ├─► 7. SHARE-MANAGER (good-citizen baseline)              [needs 3]
        ├─► 8. STATUS API (Homepage customapi)                    [needs 1]
        └─► 9. NOTIFIER (Apprise)                                 [needs 6]
        ▼
10. BOOKS (Readarr) via the *-arr-agnostic adapter — feature-flagged, best-effort [needs 2-9]
        ▼
11. DEPLOYMENT  (compose + GitHub Actions → Docker Hub + teardown loop)
```

**Build-order implications for the roadmap:**
- **Step 0 gates everything** — but it is now *lower* risk than feared because slskd's native gluetun
  integration removes the port-sync component. Still make it an explicit early go/no-go: prove the four
  smoke-test assertions (esp. native PF acquisition + control-server auth) before component work.
- gap-detector (2) and downloader/matcher (3,4) parallelize once state (1) exists.
- importer/verifier (5) cannot be validated until step 0 + the path layout are settled.
- scheduler (6) is integration glue — build 2–5 as individually invokable/CLI-triggerable units first, then
  wrap them in the loop.
- **Music end-to-end ships before books** (per FEATURES.md Readarr-retirement risk); books (10) is a flagged
  adapter layered on a working music loop.

---

## Scaling Considerations

Single-user homelab; "scale" = catalog size and Soulseek politeness, not concurrency.

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Hundreds of items | SQLite + single poll loop is ample; 1–3 concurrent slskd downloads |
| Low thousands | Page the *arr wanted/missing API; cap slskd concurrency + queue; respect *arr rate limits |
| 10k+ (unlikely here) | Still SQLite; the real limit is Soulseek peer availability + share standing, not Curator |

### Scaling priorities
1. **First bottleneck:** Soulseek availability/peer free-slots — mitigate with retry/backoff + uploader-health
   scoring (`peers` table), not more compute.
2. **Second bottleneck:** *arr API politeness under large wanted lists — page + cache, widen poll interval.

---

## Anti-Patterns

### Anti-Pattern 1: Putting Curator inside gluetun's netns too
**What people do:** Run curator with `network_mode: service:gluetun` "to reach slskd easily."
**Why it's wrong:** Curator loses synobridge DNS to the *arr stack, needs *arr APIs republished through
gluetun, and a VPN drop blinds it to its own controllers.
**Do this instead:** Only slskd shares the VPN namespace; Curator stays on synobridge and reaches slskd via
`gluetun:5030`.

### Anti-Pattern 2: Hand-rolling forwarded-port → slskd sync
**What people do:** Build a custom loop reading gluetun's port and patching slskd.
**Why it's wrong:** slskd already does this natively (`SLSKD_VPN`/`SLSKD_VPN_GLUETUN_URL`); a custom loop is
redundant and another failure point.
**Do this instead:** Use slskd's native gluetun integration; Curator only *observes* VPN/PF health for status.
(Keep `UP_COMMAND`/glueforward as a documented fallback, not the default.)

### Anti-Pattern 3: Cross-filesystem copy on import
**What people do:** Put slskd downloads and the *arr library on different mounts.
**Why it's wrong:** Forces slow copies, breaks atomic import/hardlinks, doubles disk use.
**Do this instead:** one shared `/volume1/data` tree with consistent path mapping across all containers.

### Anti-Pattern 4: Treating "download complete" as "imported"
**What people do:** Mark the item done when slskd finishes; fire-and-forget the rescan.
**Why it's wrong:** *arr may silently skip ambiguous folders; the gap is still open (the classic
"downloaded-but-not-imported" labor).
**Do this instead:** closed-loop verify — re-query *arr; escalate to Manual Import API; only then mark FILLED.

### Anti-Pattern 5: Burning attempts on infra outages / forgetting control-server auth
**What people do:** Count every failed dispatch identically; assume gluetun's control API is public.
**Why it's wrong:** A VPN blip can "exhaust" the catalog; and ~v3.40+ gluetun requires an API key for the
control server — without it, slskd's PF acquisition and the Homepage widget silently fail.
**Do this instead:** classify `infra` separately (pause, no backoff bump); configure gluetun `config.toml`
auth and pass the key to slskd + Homepage.

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Lidarr / Readarr | REST v1 + `X-Api-Key` (httpx), polled, behind common interface | Defer ALL quality to *arr profiles. Verify wanted/missing, wanted/cutoff, manualimport, command names against live `/swagger`. Readarr archived but API frozen-stable; keep behind adapter. |
| slskd | REST `http://gluetun:5030/api/v0` + `X-API-Key` | Search + transfers + share stats; never address `slskd` directly. Native gluetun PF handles listen port. |
| gluetun control server | HTTP `http://gluetun:8000/v1/portforward` (authed) | Curator reads it only for VPN/PF *health* display. slskd reaches it at `localhost:8000` inside the shared netns. Old `/v1/openvpn/portforwarded` is deprecated (301 → /v1/portforward). |
| PIA (via gluetun) | OpenVPN + `VPN_PORT_FORWARDING=on` | **US regions do NOT support PF** — use a PF-capable non-US region (e.g. CA Toronto). Bind-mount `/gluetun` so the port persists 60 days. |
| Homepage | Curator FastAPI JSON `:8674` (customapi widget) | gap_queue / in_flight / stuck / imported_24h; separate gluetun widget needs the control-server API key. |
| Notifications | Apprise (Discord/ntfy/Telegram) | Only on state transitions: grabbed / imported / failed-final / blocked / stuck. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| core/ ↔ state/ | Direct calls via repo.py | Only shared mutable surface |
| core/ ↔ clients/ | Async httpx calls | clients hide all external auth/URL detail; *arr behind a swappable adapter |
| scheduler ↔ core/ | Function invocation on triggers | core modules independently testable |
| status API ↔ state/ | Read-only queries (WAL) | No writes from the API path |

---

## Deployment Artifacts (shape)

### docker-compose.yml (Confidence: HIGH on structure + verified env keys)

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun        # PIN a v3.x tag (control-server auth landed ~v3.40)
    cap_add: [NET_ADMIN]
    devices: ["/dev/net/tun:/dev/net/tun"]
    networks: [synobridge]
    ports:
      - "5030:5030"              # slskd web/API, published via gluetun's namespace
    volumes:
      - /volume1/docker/gluetun:/gluetun   # persist forwarded_port (60-day) + config.toml (ctrl auth)
    environment:
      VPN_SERVICE_PROVIDER: private internet access
      OPENVPN_USER: ${PIA_USER}
      OPENVPN_PASSWORD: ${PIA_PASS}
      SERVER_REGIONS: ${PIA_REGION}        # MUST be a PF-capable non-US region (e.g. CA Toronto)
      VPN_PORT_FORWARDING: "on"
      VPN_PORT_FORWARDING_PROVIDER: private internet access
    # gluetun ships a healthcheck used by slskd's depends_on
    # control-server API key configured in /gluetun/config.toml (role→routes→apikey)

  slskd:
    image: slskd/slskd           # PIN >= v0.24.4 (native gluetun PF)
    network_mode: "service:gluetun"        # shares gluetun netns; NO networks: block here
    depends_on:
      gluetun: { condition: service_healthy }
    volumes:
      - /volume1/docker/slskd/config:/app
      - /volume1/data/downloads/soulseek:/downloads
    environment:
      SLSKD_VPN: "true"
      SLSKD_VPN_PORT_FORWARDING: "true"
      SLSKD_VPN_GLUETUN_URL: "http://localhost:8000"   # gluetun ctrl in the shared namespace
      # SLSKD_VPN_GLUETUN_APIKEY: ${GLUETUN_API_KEY}   # if control-server auth is enabled (verify key name)
    # NO ports: here — gluetun publishes them

  curator:
    image: ${DOCKERHUB_USER}/curator:latest
    networks: [synobridge]
    depends_on: [gluetun]
    environment:
      SLSKD_URL: "http://gluetun:5030"     # via gluetun, NEVER http://slskd
      GLUETUN_CONTROL_URL: "http://gluetun:8000"   # health display only
      GLUETUN_API_KEY: ${GLUETUN_API_KEY}
      LIDARR_URL: "http://lidarr:8686"
      READARR_URL: "http://readarr:8787"
      LIDARR_API_KEY: ${LIDARR_API_KEY}
      READARR_API_KEY: ${READARR_API_KEY}
      SLSKD_API_KEY: ${SLSKD_API_KEY}
    volumes:
      - /volume1/docker/curator/config:/config
      - /volume1/docker/curator/db:/data
      - /volume1/data:/data/shared          # shared tree for import path resolution

networks:
  synobridge:
    external: true
```

### Secrets / env
- PIA creds, *arr API keys, slskd API key, gluetun control-server key in a host-local `.env` (chmod 600,
  git-ignored), referenced as `${VAR}`. Never bake secrets into the image — the image is generic, `.env` is
  host-specific. slskd's own API key + gluetun's control `config.toml` live in their bind-mounted config dirs.

### GitHub Actions → Docker Hub
1. Push to `main` triggers the workflow.
2. `docker/build-push-action` builds **`linux/amd64`** only (DS423+ is amd64 — no arm64, skip QEMU).
3. Login with a `DOCKERHUB_TOKEN` repo secret; tag `:latest` + `:${{ github.sha }}` (metadata-action).
4. Push to Docker Hub. (Action majors per STACK.md: checkout@v4, buildx@v3, login@v3, metadata@v5,
   build-push@v6 — verify none have bumped.)

### Iterate-by-teardown loop
```
edit → git push → Actions builds & pushes image →
on Synology:  docker compose pull curator && docker compose up -d curator
              (full cycle for compose/env/network changes:  down → pull → up -d)
observe logs + Homepage status → repeat
```
- Curator is stateless except the SQLite file (on a bind mount), so recreate is safe and fast.
- Keep migrations idempotent so a recreated container reconciles against existing state (+ startup
  reconciliation against slskd/*arr).
- For **networking** changes, tear down all three (`down`) and re-up to re-establish the shared netns.

---

## Open Questions / Validation Needed Before Roadmap Hardens

1. **Pin versions:** slskd ≥ v0.24.4 (native gluetun PF) and gluetun v3.x; confirm `SLSKD_VPN_*` var casing +
   whether `SLSKD_VPN_GLUETUN_APIKEY` (or similar) is the control-server-auth key. (MEDIUM — resolve in step 0.)
2. **gluetun control-server `config.toml`** role/apikey shape (auth required ~v3.40+) and which routes slskd +
   Homepage need. (MEDIUM — step 0.)
3. **PIA PF-capable region** — confirm current list, pick non-US (CA Toronto/Montreal likely). (MEDIUM.)
4. **Existing *arr mount convention** on this Synology (single `/data` vs split) — dictates volume layout;
   verify against running containers before coding the importer. (MEDIUM.)
5. **Lidarr/Readarr v1 endpoint/command names** (wanted/missing, wanted/cutoff, manualimport, command names)
   against live `/swagger`. (MEDIUM.)
6. **Homepage customapi mapping schema** for the status widget. (LOW impact.)

## Sources

- gluetun wiki — "Connect a container to gluetun" (`network_mode: service:gluetun` shares the full netns; no
  `depends_on` needed): https://github.com/qdm12/gluetun-wiki/blob/main/setup/connect-a-container-to-gluetun.md — HIGH
- gluetun wiki — PIA provider + VPN port forwarding (`VPN_PORT_FORWARDING=on`, `/gluetun` persists port 60
  days): https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/private-internet-access.md ,
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/vpn-port-forwarding.md — HIGH
- gluetun control server API — `/v1/portforward` returns `{"port":N}`; old `/v1/openvpn/portforwarded`
  deprecated (301); routes private/authed by default (~v3.40+):
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md ,
  https://deepwiki.com/qdm12/gluetun/7.1-api-endpoints-reference ,
  https://deepwiki.com/qdm12/gluetun/7.2-authentication — HIGH
- slskd VPN docs + native gluetun integration (`SLSKD_VPN`, `SLSKD_VPN_PORT_FORWARDING`,
  `SLSKD_VPN_GLUETUN_URL`, `GLUETUN_INCOMING_PORT`; native PF since ~v0.24.4):
  https://github.com/slskd/slskd/blob/master/docs/vpn.md ,
  https://github.com/slskd/slskd/discussions/946 , https://github.com/slskd/slskd/issues/1432 — HIGH
- Fallback port-sync tools (escape hatches, not default): https://github.com/GeoffreyCoulaud/glueforward ,
  https://github.com/tieum/slskd-port-forward-gluetun-server — HIGH (existence)
- Homepage gluetun widget (needs control-server auth post-update): https://gethomepage.dev/widgets/services/gluetun/ ,
  https://github.com/gethomepage/homepage/discussions/6017 — MEDIUM
- Sibling research: `.planning/research/STACK.md`, `FEATURES.md`; project brief `.planning/PROJECT.md` — HIGH

---
*Architecture research for: autonomous fallback-only Soulseek gap-filler (Lidarr/Readarr, Synology)*
*Researched: 2026-05-29*
