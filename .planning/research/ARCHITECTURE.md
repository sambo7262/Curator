# Architecture Research

**Domain:** Autonomous, fallback-only Soulseek (slskd) gap-filler for Lidarr (music) + Readarr (books, best-effort) — Synology homelab, Docker
**Researched:** 2026-05-29
**Confidence:** HIGH on container topology + networking (live-verified against gluetun/slskd sources this session); HIGH on component design; MEDIUM on exact *arr endpoint/command names + the Manual Import payload (verify/spike at build)

> **Verification status:** The hardest part — the gluetun/slskd/synobridge networking — was **live-verified
> this session** against the gluetun wiki, slskd VPN docs, and the gluetun control-server API reference (see
> Sources). One finding materially changes the design vs. naive assumptions: **slskd has built-in gluetun
> port-forwarding integration (since v0.24.4), so Curator does NOT need to build a port-sync component.**
> That simplification is baked into the component list and build order below. Items still marked MEDIUM are
> *arr-specific endpoint names — stable but worth confirming against each app's live `/swagger`.
>
> **Scope-update revision (2026-05-29, post-roadmap):** This doc makes two newer first-class concerns explicit,
> superseding the earlier drop-folder framing:
> 1. **Staging → selective Manual-Import → auto-purge cleanup flow.** Soulseek transfers arrive as whole folders
>    full of junk. Curator downloads into an **isolated per-item quarantine** under the shared tree, imports
>    **only the wanted files** via the *arr Manual Import API (NOT a blind rescan), then **auto-purges the entire
>    staging dir** so nothing unwanted ever reaches `/volume1` and the owner never hand-deletes. See Pattern 5,
>    data-flow steps [8a]–[8e], and the `staged_files` table.
> 2. **`*-arr`-agnostic adapter with Readarr circuit-breaker isolation.** Lidarr and Readarr sit behind ONE
>    interface; `ReadarrAdapter` is breaker-wrapped so Readarr's unmaintained instability can NEVER stall the
>    music path. See the adapter component row, Pattern 6, and the books build-order layer.
>
> **Networking facts re-verified this session (see Sources):** `network_mode: service:gluetun` is mutually
> exclusive with joining another docker network (ports must be published on gluetun); `FIREWALL_OUTBOUND_SUBNETS`
> whitelists a LAN/Docker subnet for *outbound* calls from inside the netns and is NOT needed for browser→WebUI
> (handled by gluetun's published port); slskd native gluetun PF landed in **0.24.4** and updates the listen
> port at runtime without restart.

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
│   │  scheduler · arr-adapter · gap-detector ·        │  │  at http://gluetun:5030│
│   │  trigger · matcher · quality-gate · downloader · │  │                       │
│   │  staging+cleanup · importer · verifier ·         │  │                       │
│   │  share-mgr · notifier · status API :8674         │  │                       │
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
   Single shared /volume1/data tree → mounted IDENTICALLY as /data in slskd,
   curator, Lidarr, Readarr  (staging quarantine + give-back share + library)
```

**Core topology decision:** `curator` is a normal `synobridge` member. Only `slskd` shares gluetun's network
namespace. This forces *only Soulseek P2P traffic* through the VPN while Curator's *arr polling and its
Homepage status API stay on the LAN. Curator reaches slskd **through gluetun's published port**
(`http://gluetun:5030`), never `http://slskd:...`. (Confidence: HIGH — confirmed canonical pattern.)

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **scheduler** | Interval loop; triggers gap-detector, dispatcher, reconciler | APScheduler (async) |
| **arr-adapter** | The *-arr-agnostic seam: ONE interface (`get_wanted`, `get_profile`, `get_queue_status`, `manual_import_candidates`, `execute_import`, `verify_imported`). Impls: `LidarrAdapter` (music, primary), `ReadarrAdapter` (books, **circuit-breaker wrapped**). The ONLY module that imports *arr knowledge. | httpx + Protocol; breaker around Readarr |
| **gap-detector** | Poll wanted/missing + cutoff **via the adapter** → upsert items | arr-adapter |
| **trigger (grace/fallback)** | Hold an item until `discovered_at + grace` AND no active/queued Usenet grab (never race the primary pipeline) | arr-adapter (queue/history) + SABnzbd |
| **matcher/scorer** | Build Soulseek queries from metadata; rank candidates (completeness, track-count, format/bitrate, uploader health); refuse-rather-than-guess | pure functions over slskd results |
| **quality-gate** | Enforce the *arr profile/cutoff; validate content (track-count completeness, fake/transcoded-FLAC heuristics, denylist) before AND after download | matcher output + adapter profile + filesystem |
| **downloader** | Issue slskd searches; enqueue chosen candidate **into the per-item staging dir**; track transfer | httpx slskd client → `gluetun:5030` |
| **staging + cleanup mgr** | Allocate `/data/staging/<uuid>/`; classify WANTED vs UNWANTED files; **auto-purge the entire staging dir** after a verified import OR a terminal failure (nothing unwanted ever leaves quarantine) | filesystem on the shared `/data` tree |
| **importer** | Hardlink WANTED files to a curated subdir; call the *arr **Manual Import API** with explicit per-file decisions (NOT a blind rescan); verify acceptance | filesystem + adapter (manual import) |
| **verifier/reconciler** | Confirm *arr actually imported; reconcile slskd↔state↔staging on startup; handle orphans/stalls/partials | httpx *arr + slskd + filesystem |
| **state/dedup store** | Source of truth: items, attempts, staged_files, statuses, backoff, staging paths, share stats; prevents re-work | SQLite + WAL |
| **share-manager** | Hardlink imported content into slskd's share dir; monitor share/upload stats to stay a good Soulseek citizen | slskd API + filesystem |
| **notifier** | Emit grab/import/failure/blocked/stuck/purged events | Apprise |
| **status API** | Read-only JSON for Homepage widget (gap queue, in-flight, stuck, imported 24h, VPN/slskd health) | FastAPI :8674 |

> **Note — no port-sync component.** Early designs of this stack hand-rolled a forwarded-port→slskd sync loop.
> That is now obsolete: **slskd's native gluetun integration** reads gluetun's control server itself and sets
> its own Soulseek listen port. Curator only *observes* health, it does not manage the port. (Confidence: HIGH)

**Dependency direction (compile-time):** `status/notifier` → read `state` ← written by `scheduler` →
orchestrates `[gap-detector → trigger → matcher → quality-gate → downloader → staging → importer → verifier →
share-mgr]` → all use `state`; `gap-detector/trigger/importer/verifier` → `arr-adapter`;
`matcher/downloader/share-mgr` → `slskd client`. **arr-adapter is the sole importer of *arr knowledge** — a
Readarr fault is contained behind its circuit breaker and cannot reach the music path.

---

## The gluetun / slskd / synobridge Networking Pattern (the tricky part, verified)

### The hard constraint
A container started with `network_mode: service:gluetun` **shares gluetun's network namespace entirely** — no
own IP, no own hostname, and it **cannot also list a `networks:` block** (sharing a netns is mutually exclusive
with joining other networks; Docker errors if you try to keep `ports:` on the tenant). So **slskd is invisible
on synobridge as a hostname**, and anything slskd exposes (web UI / API on 5030) must be **published by the
gluetun container**, because to Docker those ports live in gluetun's namespace. (Confidence: HIGH — gluetun wiki
"connect a container"; community confirmation: "Gluetun becomes the only door in or out; all Web UI ports must
be declared in the Gluetun service.")

### The three containers and their wiring

| Container | Network mode | Reaches | Reachable by |
|-----------|--------------|---------|--------------|
| `gluetun` | `synobridge` member; publishes slskd's ports | Internet via PIA (kill-switched) | Curator + Homepage via its synobridge IP / published ports |
| `slskd` | `network_mode: service:gluetun` (**no** `networks:`) | Internet **only** via gluetun; gluetun ctrl at `localhost:8000` | Only through gluetun's published ports |
| `curator` | `synobridge` member | Lidarr/Readarr/Prowlarr/SABnzbd by name; slskd API via `gluetun:5030` | Homepage (status API :8674) |

### Why this resolves the "can't join synobridge while using service:gluetun" constraint
The collision is real: a `service:gluetun` tenant cannot also be a synobridge member, so it cannot resolve
`lidarr`/`readarr` by name. We sidestep it instead of fighting it:
- **gluetun itself joins synobridge** and is the only VPN-bound container that needs a synobridge identity (for
  inbound publishing). slskd inherits gluetun's stack; Curator is a plain synobridge member.
- **Only slskd needs the tunnel**, and slskd does NOT call the *arr APIs — Curator does. So the awkward
  "reach the LAN from inside the netns" problem mostly evaporates: Curator talks to *arr natively on synobridge.
- **`FIREWALL_OUTBOUND_SUBNETS=<synobridge CIDR>`** is set on gluetun as the documented safety valve: it
  whitelists the LAN/Docker subnet so anything *inside* the netns that ever needs to initiate a LAN connection
  (e.g. slskd → an *arr, or a future netns tenant) can, instead of being dropped by the kill-switch. It is NOT
  required for the browser→slskd WebUI (that works via gluetun's published port). **Caveat (verified):** the
  whitelisted subnet must not overlap the VPN tunnel CIDR or port-forwarding breaks. (Confidence: HIGH.)

### Direction 1 — Curator → slskd API (into the VPN namespace)
- slskd listens on `0.0.0.0:5030` **inside gluetun's namespace**.
- The port is published **on the gluetun service**: `ports: ["5030:5030"]` declared on *gluetun*, not slskd.
- gluetun is on synobridge, so Curator calls `http://gluetun:5030/api/v0` (Docker DNS resolves `gluetun`).
- **Config rule:** `SLSKD_URL=http://gluetun:5030`. Never `http://slskd:5030`. (Confidence: HIGH — verified; the
  #1 misconfiguration in this stack. Put it in a code comment.)

### Direction 2 — Soulseek peers → slskd P2P listen (forwarded port → slskd, handled natively)
**This is the part that changed.** slskd ships built-in gluetun integration; Curator does not build it.
- PIA hands gluetun a **dynamic** forwarded port (persists 60 days as long as the `/gluetun` dir is bind-mounted).
- gluetun exposes it on its **control server**; inside the shared netns slskd reaches it at `localhost:8000`.
  (Note: the older `/v1/openvpn/portforwarded` path is deprecated and redirects to `/v1/portforward`.)
- **slskd native integration (v0.24.4+)** reads that endpoint and self-sets `soulseek.listen_port`, updating it
  at runtime without a restart when PIA rotates the port:
  ```yaml
  # on the slskd service:
  SLSKD_VPN: "true"
  SLSKD_VPN_PORT_FORWARDING: "true"
  SLSKD_VPN_GLUETUN_URL: "http://localhost:8000"      # gluetun ctrl, in the shared namespace
  SLSKD_VPN_GLUETUN_API_KEY: "${GLUETUN_API_KEY}"     # control-server auth (else 401)
  ```
  (Confidence: HIGH — verified via slskd VPN docs + the 0.24.4 release notes + community confirmations; exact
  var casing to confirm against the pinned slskd build's docs.)
- **gluetun control-server auth (IMPORTANT, recent gluetun):** control-server routes are **no longer public by
  default** and require an API key. The simplest verified form is
  `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"apikey","apikey":"${GLUETUN_API_KEY}"}'` on gluetun, matched
  by `SLSKD_VPN_GLUETUN_API_KEY` on slskd. Without it slskd gets 401 (slskd issue #1660). Homepage's gluetun
  widget likewise needs the key. Budget for this in step 0. (Confidence: HIGH.)
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
│   ├── main.py              # entrypoint: migrations → startup reconcile → scheduler + FastAPI
│   ├── config.py            # Pydantic Settings (env + YAML)
│   ├── scheduler.py         # APScheduler job wiring (the control loop)
│   ├── adapters/            # THE *-ARR-AGNOSTIC SEAM (only place *arr knowledge lives)
│   │   ├── base.py          # ArrAdapter Protocol: get_wanted/get_profile/get_queue_status/
│   │   │                    #   manual_import_candidates/execute_import/verify_imported
│   │   ├── lidarr.py        # LidarrAdapter (music — primary)
│   │   ├── readarr.py       # ReadarrAdapter (books — best-effort)
│   │   └── breaker.py       # circuit breaker isolating Readarr instability from the music path
│   ├── clients/
│   │   ├── slskd.py         # slskd REST client → http://gluetun:5030/api/v0
│   │   └── gluetun.py       # (optional) read VPN/PF health for status only → :8000/v1/portforward
│   ├── core/
│   │   ├── gap_detector.py  # poll wanted/missing+cutoff via adapter → items
│   │   ├── trigger.py       # grace-window + Usenet-aware fallback gate
│   │   ├── matcher.py       # query building + candidate scoring (refuse-rather-than-guess)
│   │   ├── quality_gate.py  # *arr-profile filter + track-count completeness + fake-FLAC + denylist
│   │   ├── downloader.py    # dispatch searches/downloads to slskd INTO the staging dir
│   │   ├── staging.py       # FIRST-CLASS: alloc/classify/PURGE the per-item quarantine dir
│   │   ├── importer.py      # hardlink WANTED → curated; Manual Import API (explicit decisions) + verify
│   │   ├── verifier.py      # confirm *arr import; startup reconcile (state↔slskd↔staging); orphan/stall
│   │   └── share.py         # hardlink imported content into slskd share + read share stats
│   ├── state/
│   │   ├── db.py            # SQLite connection (WAL), migrations
│   │   ├── models.py        # items, attempts, staged_files, peers, share_stats, events
│   │   └── repo.py          # dedup/upsert, backoff queries, status reads
│   ├── notify.py            # Apprise wrapper
│   └── api/
│       └── status.py        # FastAPI: Homepage JSON (:8674)
├── migrations/              # idempotent SQL (safe on container recreate)
├── Dockerfile               # multi-stage, python:3.12-slim, linux/amd64, non-root
└── tests/
```

### Structure Rationale
- **`adapters/` is the *-arr firewall:** the ONLY place that imports *arr knowledge. Music (`lidarr.py`) and
  books (`readarr.py`) implement one `ArrAdapter` Protocol; `ReadarrAdapter` is wrapped by `breaker.py` so a
  hung/500ing Readarr trips a circuit breaker — book items park as `ADAPTER_DOWN` and the **music path keeps
  flowing**. A future Readarr replacement (fork / Calibre flow) is a single adapter swap (Pitfall #17).
- **`clients/` isolated from `core/`:** only downloader/matcher/share/verifier touch slskd. *arr access goes
  exclusively through `adapters/`, never raw from `core/`.
- **`staging.py` is a first-class `core/` module, not a helper:** the staging→selective-import→purge cleanup is
  a headline product concern (nothing unwanted on `/volume1`, zero hand-deletion), so it owns alloc/classify/
  purge and the `staged_files` provenance.
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

### Pattern 5: Quarantine → selective Manual-Import → auto-purge (the cleanup spine)
**What:** Soulseek delivers *whole folders* (wanted audio + `.nfo`, dupe art, samples, `.url` ads, scene junk).
Curator downloads into an **isolated per-item quarantine** on the shared `/data` tree, hands the *arr **only**
the wanted files via the **Manual Import API** (explicit file→release decisions, NOT a blind rescan that would
ingest the junk), then **purges the entire staging dir** — wanted and unwanted alike — after a verified import
or a terminal failure. Result: nothing unwanted ever reaches `/volume1`; the owner never hand-deletes.
**When to use:** every acquisition. Non-negotiable for the "no manual cleanup" mandate (Pitfalls #5, #9).
**Trade-offs:** + deterministic file→release mapping, zero library pollution, self-cleaning. − more steps than a
drop-folder; requires staging on the **same filesystem** as the library so the curate-step hardlink is atomic
(not a cross-device copy).

```python
staging = staging.alloc(item)                       # /data/staging/<uuid>/  (same FS as library)
slskd.enqueue(candidate.files, dest=staging)        # whole folder lands here (incl. junk)
wanted, unwanted = staging.classify(staging, expected_tracks)   # record both in staged_files
quality_gate.verify_content(wanted)                 # post-DL: fake-FLAC decode + completeness
curated = staging.hardlink_wanted(staging, wanted)  # hardlinks WITHIN /data → atomic
cands = arr.manual_import_candidates(path=curated)  # GET /api/v1/manualimport
arr.execute_import(build_decisions(cands, wanted))  # POST command ManualImport (explicit, Move)
assert arr.verify_imported(item)                    # poll history/queue — closed loop
staging.purge(staging)                              # rm -rf the WHOLE dir (curated + originals + junk)
repo.transition(item, FILLED, purged_at=now())      # library copy persists; staging gone
```

### Pattern 6: Adapter + circuit-breaker for *-arr blast-radius isolation
**What:** Both *arr behind one `ArrAdapter`; `ReadarrAdapter` wrapped in a short-timeout circuit breaker.
**When to use:** Always — Readarr is retired/unmaintained; books are best-effort (Pitfall #17).
**Trade-offs:** + a Readarr metadata-server hiccup trips the breaker → book items go `ADAPTER_DOWN` while music
keeps flowing; swapping Readarr later is one adapter. − a thin abstraction layer (pays for itself immediately).

```python
class ArrAdapter(Protocol):
    def get_wanted(self) -> list[GapItem]: ...
    def get_profile(self, item) -> QualityProfile: ...
    def get_queue_status(self, item) -> QueueStatus: ...     # fallback-only race check
    def manual_import_candidates(self, path) -> list[Candidate]: ...
    def execute_import(self, decisions) -> None: ...
    def verify_imported(self, item) -> bool: ...

readarr = CircuitBreaker(ReadarrAdapter(...), on_open=lambda i: repo.mark(i, "ADAPTER_DOWN"))
# music (LidarrAdapter) is never behind the same breaker → Readarr cannot stall it
```

---

## Data Flow

### End-to-end flow (with the cleanup path + failure / retry / backoff paths)

```
[1] gap-detector polls Lidarr/Readarr wanted/missing + cutoff VIA THE ADAPTER
        upsert items (status=DISCOVERED, discovered_at=now)         [dedup = (arr_app, arr_id)]
        ▼
[2] trigger: GRACE + FALLBACK CHECK — skip until discovered_at + grace_period AND *arr history/queue
    (+ SABnzbd) show no recent success/pending grab (let Usenet win first)
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
[5] scorer ranks; quality-gate (track-count gate, name similarity, format/bitrate ≥ *arr profile,
    uploader free slot). Low confidence → REFUSE (logged, backed off — a valid outcome).
        ── all filtered ──────────► outcome=quality → backoff path [4]
        ▼
[6] staging.alloc → /data/staging/<uuid>/ ; downloader enqueues chosen candidate in slskd
    INTO that quarantine dir
        status=DOWNLOADING; items.staging_path set; attempt.slskd_transfer_id set
        ▼
[7] reconciler polls slskd transfer
        ── stalled > stall_timeout ► cancel in slskd; outcome=stall; PURGE partial staging;
                                      try next candidate; if none left → backoff path [4]
        ── slskd/VPN down ─────────► PAUSE (infra) — no attempt consumed; resume when healthy
        ▼
[8] download COMPLETE inside /data/staging/<uuid>/  (whole folder incl. junk)
        status=DOWNLOADED
        ▼
   ── CLEANUP PATH (staging → selective import → purge) ──
   [8a] staging.classify(dir, expected_tracks) → WANTED audio vs UNWANTED (.nfo/art-dupe/sample/.url/scene)
        record each row in staged_files; UNWANTED never leaves quarantine
   [8b] quality-gate post-DL verify on WANTED (fake/transcoded-FLAC decode, real track-count)
        ── fails ──► outcome=quality; denylist uploader; PURGE staging; backoff path [4]
   [8c] importer hardlinks WANTED → /data/staging/<uuid>/_curated/  (atomic, same FS)
   [8d] importer: arr.manual_import_candidates(_curated) → build explicit file→release decisions →
        arr.execute_import(ManualImport, Move)         (NOT a blind rescan)
        ▼
[9] verifier re-queries *arr — did the item actually import into /volume1 (left the wanted list)?
        ── still missing > import_timeout ► outcome=import; PURGE staging; backoff path [4]
        ── imported OK ───────────────────► status=IMPORTED
        ▼
[10] share-manager hardlinks imported content into /data/slskd/shared (give-back) → records upload contribution
        ▼
[11] staging.PURGE  rm -rf /data/staging/<uuid>/   (curated + originals + junk)
        status=FILLED; items.staging_path=NULL, purged_at=now; notify(success + purged)
```

> **Cleanup-path invariant:** an item reaches `FILLED` only after (a) *arr confirms the import AND (b) the
> staging dir is purged. A terminal failure at any step also purges staging. There is no code path that leaves
> a populated `/data/staging/<uuid>/` behind during normal operation; startup reconciliation (Pattern 4 +
> verifier) sweeps any orphan staging dirs left by a crash.

### State machine / backoff
```
items.status:  DISCOVERED → ELIGIBLE → SEARCHING → DOWNLOADING → DOWNLOADED
                  → IMPORTED → FILLED        (FILLED = imported AND staging purged)
                                  ▲            │            │
                                  └─ backoff ──┴── (no_match│quality│stall│import) ─┘  (each path PURGES staging)
                                  └─ EXHAUSTED/dormant (max attempts; long re-eligibility sweep)
                                  └─ PAUSED (infra; auto-resume, never counts as attempt)
                                  └─ ADAPTER_DOWN (Readarr breaker open; book item parked; music unaffected)
                                  └─ INVALIDATED (*arr no longer wants / edition changed → purge)
```
- **Backoff:** exponential + jitter per item: `delay = min(base * 2^attempt_count, cap) ± jitter`
  (e.g. 1d → 3d → 7d → 30d, capped). (Confidence: MEDIUM — tune.)
- **Failure classes** (`no_match`, `quality`, `stall`, `import`) bump the same counter but are recorded
  distinctly for diagnostics + provenance. `infra` does neither. **Every failure class purges staging.**
- **Startup reconciliation:** diff Curator state vs slskd live transfers AND *arr satisfaction AND on-disk
  `/data/staging/*` to avoid double-grabs and leaked quarantine dirs after a restart.

### Key data flows
1. **Discovery:** *arr API → adapter → gap-detector → `items` (deduped by `(arr_app, arr_id)`).
2. **Acquisition:** `items` → matcher/quality-gate → slskd search/download into staging (via gluetun) → `attempts`.
3. **Cleanup handoff:** staging dir → classify → curate (hardlink WANTED) → Manual Import → verify → purge → `FILLED`.
4. **Observability:** `state.*` → status API (Homepage polls) and notifier (push on grab/import/fail/blocked/stuck/purged).
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
  metadata_json    TEXT,                   -- foreign ids (MBID/ISBN), year, tracklist, wanted formats, profile/cutoff
  status           TEXT NOT NULL,          -- DISCOVERED|ELIGIBLE|SEARCHING|DOWNLOADING|DOWNLOADED|
                                           -- IMPORTED|FILLED|EXHAUSTED|PAUSED|ADAPTER_DOWN|INVALIDATED
  attempt_count    INTEGER NOT NULL DEFAULT 0,
  next_attempt_at  TEXT,                   -- ISO8601 backoff gate
  discovered_at    TEXT NOT NULL,          -- "first seen wanted" → grace timer
  grace_until      TEXT,                   -- discovered_at + grace_period
  staging_path     TEXT,                   -- /data/staging/<uuid>/ while in-flight; NULL after purge
  purged_at        TEXT,                   -- when the staging dir was auto-purged
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
  chosen_user       TEXT,                  -- Soulseek peer (provenance: who we got it from)
  chosen_files_json TEXT,                  -- file list + sizes (provenance: "tried X, 9/12 tracks")
  score             REAL,
  bytes             INTEGER,
  notes             TEXT
);

-- Per-file classification within a staging dir — drives selective import + purge audit.
CREATE TABLE staged_files (
  id             INTEGER PRIMARY KEY,
  attempt_id     INTEGER NOT NULL REFERENCES attempts(id),
  rel_path       TEXT NOT NULL,            -- relative to /data/staging/<uuid>/
  classification TEXT NOT NULL,            -- WANTED | UNWANTED
  imported       INTEGER NOT NULL DEFAULT 0,  -- 1 once *arr confirms this WANTED file imported
  bytes          INTEGER
);

-- Learned peers: reliability / free-slot history (improves uploader-health scoring + denylist).
CREATE TABLE peers (
  username      TEXT PRIMARY KEY,
  last_seen_at  TEXT,
  success_count INTEGER DEFAULT 0,
  fail_count    INTEGER DEFAULT 0,
  denylisted    INTEGER NOT NULL DEFAULT 0,  -- bad uploader (fake-FLAC / wrong match)
  avg_speed     REAL
);

-- Sharing/citizenship stats so Curator stays a net contributor (avoid leecher de-prioritization).
CREATE TABLE share_stats (
  id               INTEGER PRIMARY KEY,
  recorded_at      TEXT NOT NULL,
  item_id          INTEGER REFERENCES items(id),
  shared_path      TEXT,
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
  type     TEXT,                           -- grab|import|purge|failure|blocked|stuck
  message  TEXT
);

CREATE INDEX idx_items_status   ON items(status, next_attempt_at);
CREATE INDEX idx_attempts_item  ON attempts(item_id);
CREATE INDEX idx_staged_attempt ON staged_files(attempt_id);
```
`PRAGMA journal_mode=WAL` lets the status API read while the single writer process works; one process means no
write contention. (Confidence: HIGH for fit.)

> Note: there is no `forwarded_port` column — port management lives entirely in the slskd↔gluetun native
> integration, not in Curator's state. `staging_path` + `staged_files` are the provenance for the cleanup flow.

---

## Volume / Bind-Mount Layout (under /volume1)

The import handoff **hinges on path consistency**: the staging/quarantine dir must live on the **same
filesystem** and be addressed by the **same path** in slskd, Curator, and the *arr — so the curate-hardlink and
the *arr Manual Import are atomic hardlinks/moves, not slow cross-filesystem copies (the #1 *arr import failure
cause). Put staging *inside* the shared `/data` tree.

```
/volume1/docker/curator/
  ├─ config/                 → curator settings (no secrets)        curator:/config
  └─ db/curator.sqlite       → state store                          curator:/db
/volume1/docker/gluetun/     → gluetun state (forwarded_port, ctrl auth) gluetun:/gluetun  ← MUST persist (60-day PF)
/volume1/docker/slskd/
  └─ config/                 → slskd.yml (api key, shares, integration.vpn)  slskd:/app
/volume1/data/                                        ← single shared "data root", IDENTICAL path everywhere
  │                            mounted as /data in slskd, curator, Lidarr, Readarr
  ├─ staging/                → Curator quarantine: /data/staging/<uuid>/  (+ <uuid>/_curated/)
  │                            slskd downloads the WHOLE folder here; importer hardlinks
  │                            WANTED→_curated; Manual Import reads _curated; then the whole
  │                            <uuid>/ dir is PURGED. Same /data path in every container.
  ├─ slskd/
  │   ├─ shared/             → give-back share dir (hardlinks of imported content; mount RO to slskd)
  │   └─ incomplete/         → slskd in-progress (KEEP under /data — never a separate mount)
  ├─ music/                  → Lidarr library root / Plex (import target)
  └─ books/                  → Readarr library root (import target)
```

**Path rules (Confidence: HIGH on the principle; MEDIUM on the exact mount strings):**
- Mount the **same `/volume1/data` tree at the same mount point** (`/data`) in slskd, curator, Lidarr, Readarr so
  all four resolve `/data/staging/<uuid>/_curated/track.flac` identically → Manual Import accepts the path;
  hardlinks work.
- **Staging lives under `/data`** so download→curate→import are all atomic hardlinks on one filesystem; the
  auto-purge then `rm -rf`s the staging dir with the library copy already safely on its own inode.
- **Bind-mount gluetun's `/gluetun` dir** so the PIA forwarded port persists 60 days across restarts
  (verified). Without it, the port re-rolls on every restart.
- **Match the existing *arr stack's mount convention** (likely TRaSH-guide single `/data` mount). Do NOT invent
  a second scheme. **Verify the current *arr container mounts before fixing this** — the #1 *arr import failure
  cause.
- Consistent **PUID/PGID + umask 002** across slskd/curator/Lidarr/Readarr so *arr can move what Curator wrote
  (Pitfall #12). slskd share tree mounted **read-only** where practical (Pitfall #18).
- Curator's importer hardlinks/curates only *within* the shared `/data` tree, then calls the *arr **Manual
  Import API** with explicit decisions — never copies across mounts; never writes the final library path (let
  *arr rename/move); never points an *arr drop-folder at the staging dir (would ingest the junk).

---

## Suggested Build Order / Dependency Graph (Horizontal Layers)

Build bottom-up; the riskiest infra goes first so failures surface immediately.

```
0. NETWORKING SMOKE TEST  ← highest risk, do FIRST (spike, not a feature)
   gluetun(PIA non-US, /gluetun mounted, control-server apikey) + slskd(network_mode:service:gluetun,
   SLSKD_VPN native PF) + throwaway curl box on synobridge.
   Prove: (a) slskd API reachable at http://gluetun:5030 from synobridge
          (b) slskd egress IP == PIA IP (no DNS leak)
          (c) slskd self-acquired the PIA forwarded port via gluetun ctrl server (authed),
              re-syncing after a restart — not just first boot
          (d) tunnel drop kills slskd traffic (fail-closed)
          (e) Curator resolves lidarr/readarr/sabnzbd by name; a file written into /data is a real
              hardlink (not a cross-FS copy) and movable by the *arr PUID/PGID
        ▼
1. STATE STORE  (schema incl. staged_files + idempotent migrations + dedup upsert)  ← everything writes here
        ▼
2. ARR-ADAPTER (ArrAdapter Protocol + LidarrAdapter; Readarr breaker stubbed) +
   GAP-DETECTOR (wanted/missing+cutoff) + TRIGGER (grace + Usenet-aware fallback)  [needs 1]
        │
        ├─► 3. SLSKD CLIENT + DOWNLOADER  (search/enqueue into staging/poll)   [needs 0,1]
        │         ▼
        │      4. MATCHER/SCORER + QUALITY-GATE (profile + track-count + fake-FLAC; refuse-rather-than-guess) [needs 3]
        ▼         ▼
5. STAGING+CLEANUP MGR (alloc/classify/PURGE) + IMPORTER (hardlink WANTED → Manual Import API + verify)
   + VERIFIER/RECONCILER (closed-loop confirm; startup orphan-staging sweep)   [needs 2,3,4 + path layout]
   → END-TO-END MUSIC HAPPY PATH incl. selective import + auto-purge
        ▼
6. SCHEDULER  (grace + backoff control loop + startup reconciliation tying 2–5)  [needs 2–5]
        │
        ├─► 7. SHARE-MANAGER (hardlink imported→share; good-citizen baseline)   [needs 3,5]
        ├─► 8. STATUS API (Homepage customapi)                                  [needs 1]
        └─► 9. NOTIFIER (Apprise; incl. purged events)                          [needs 6]
        ▼
10. BOOKS (ReadarrAdapter behind the breaker) — feature-flagged, best-effort, NEVER gates music  [needs 2-9]
        ▼
11. DEPLOYMENT  (compose + GitHub Actions → Docker Hub + teardown loop)
```

**Mapping to the roadmap's horizontal layers (ROADMAP.md):** Step 0 = Phase 1 (VPN-Routed Networking
Foundation); Steps 1–2 = Phase 2 (State Ledger + *arr Adapter + Gap Detection); Steps 3–4 = Phase 3 (Matching &
Quality Gating); Step 5 = Phase 4 (Acquisition, Staging & Clean Import — the crucible); Steps 6–7 = Phase 5
(Autonomy, Sharing & Self-Recovery); Steps 8–9 = Phase 6 (Observability & Notifications); Step 10 (books) rides
the same layers behind the adapter, enabled only once music is solid.

**Build-order implications for the roadmap:**
- **Step 0 gates everything** — but it is *lower* risk than feared because slskd's native gluetun integration
  removes the port-sync component. Still make it an explicit early go/no-go: prove the five smoke-test
  assertions (esp. native PF acquisition + control-server auth + hardlink parity) before component work.
- gap-detector (2) and downloader/matcher (3,4) parallelize once state (1) exists.
- staging/importer/verifier (5) cannot be validated until step 0 + the path layout are settled — and the Manual
  Import payload must be spiked here (see Open Questions).
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
| Low thousands | Page the *arr wanted/missing API; cap slskd concurrency + queue; budget-cap total staging bytes |
| 10k+ (unlikely here) | Still SQLite; the real limit is Soulseek peer availability + share standing, not Curator |

### Scaling priorities
1. **First bottleneck:** Soulseek availability/peer free-slots — mitigate with retry/backoff + uploader-health
   scoring (`peers` table), not more compute.
2. **Second bottleneck:** disk in staging if purge ever lags — cap total staging bytes; reconcile-on-startup
   sweeps orphans. Then *arr API politeness under large wanted lists — page + cache, widen poll interval.

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

### Anti-Pattern 3: Cross-filesystem copy on import / separate /downloads and /music mounts
**What people do:** Put slskd downloads/staging and the *arr library on different mounts.
**Why it's wrong:** Forces slow copies, breaks atomic import/hardlinks, doubles disk use, churns permissions.
**Do this instead:** one shared `/volume1/data` tree with consistent `/data` path across all containers; staging
under `/data`.

### Anti-Pattern 4: Treating "download complete" as "imported"
**What people do:** Mark the item done when slskd finishes; fire-and-forget the rescan.
**Why it's wrong:** *arr may silently skip ambiguous folders; the gap is still open (the classic
"downloaded-but-not-imported" labor).
**Do this instead:** closed-loop verify — re-query *arr; mark `IMPORTED` only on confirmation; mark `FILLED`
only after the staging dir is purged.

### Anti-Pattern 5: Drop-folder import (let *arr scan the whole download)
**What people do:** point an *arr monitored/drop folder at slskd's download dir and trigger a rescan.
**Why it's wrong:** *arr ingests the **whole junky folder** — `.nfo`, dupe art, samples, scene cruft land in
`/volume1`; you lose per-file control and reintroduce manual cleanup (Pitfalls #5, #9). This supersedes the
earlier "drop-folder + rescan, Manual Import as fallback" framing.
**Do this instead:** the cleanup spine (Pattern 5) — download to an isolated staging dir, **Manual Import API
with explicit per-file decisions for WANTED files only**, verify, then **auto-purge the entire staging dir**.

### Anti-Pattern 6: Letting a Readarr fault stall the music path
**What people do:** call Lidarr and Readarr through the same code with no isolation.
**Why it's wrong:** Readarr is unmaintained; a hung metadata server or 500s would block the whole loop (Pitfall #17).
**Do this instead:** both behind one `ArrAdapter`; wrap `ReadarrAdapter` in a circuit breaker → books park as
`ADAPTER_DOWN`, music keeps flowing (Pattern 6).

### Anti-Pattern 7: Burning attempts on infra outages / forgetting control-server auth
**What people do:** Count every failed dispatch identically; assume gluetun's control API is public.
**Why it's wrong:** A VPN blip can "exhaust" the catalog; and recent gluetun requires an API key for the control
server — without it, slskd's PF acquisition and the Homepage widget silently fail (401).
**Do this instead:** classify `infra` separately (pause, no backoff bump); configure gluetun control-server auth
and pass the key to slskd + Homepage.

---

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Lidarr / Readarr | REST v1 + `X-Api-Key` (httpx), polled, **behind the `ArrAdapter` seam** | Defer ALL quality to *arr profiles. **Import via Manual Import API** (`GET /api/v1/manualimport` → explicit decisions → `POST /api/v1/command` ManualImport), NOT drop-folder. Payload is under-documented — **spike early** (Lidarr #5647). Readarr archived but API frozen-stable; behind a **circuit-breaker** so its instability can't reach music. |
| slskd | REST `http://gluetun:5030/api/v0` + `X-API-Key` | Search + transfers (download INTO `/data/staging/<uuid>/`) + share stats; never address `slskd` directly. Native gluetun PF handles listen port. |
| gluetun control server | HTTP `http://gluetun:8000/v1/portforward` (authed) | Curator reads it only for VPN/PF *health* display. slskd reaches it at `localhost:8000` inside the shared netns. Old `/v1/openvpn/portforwarded` deprecated (→ /v1/portforward). |
| PIA (via gluetun) | WireGuard/OpenVPN + `VPN_PORT_FORWARDING=on` | **US regions do NOT support PF** — use a PF-capable non-US region (e.g. CA Toronto). Bind-mount `/gluetun` so the port persists 60 days. |
| SABnzbd / *arr queue | Curator reads queue/history | fallback-only race check before grabbing (Pitfall #15) |
| Plex | **none** — *arr notifies Plex itself | Curator must NOT call Plex (anti-feature) |
| Homepage | Curator FastAPI JSON `:8674` (customapi widget) | gap_queue / in_flight / stuck / imported_24h / VPN+slskd health; separate gluetun + slskd widgets need the control-server / slskd API keys. |
| Notifications | Apprise (Discord/ntfy/Telegram) | Only on state transitions: grabbed / imported / purged / failed-final / blocked / stuck. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| core/ ↔ state/ | Direct calls via repo.py | Only shared mutable surface; transactional write-ahead intent |
| core/ ↔ adapters/ | Protocol calls | adapters hide ALL *arr knowledge; Readarr breaker-isolated |
| core/ ↔ clients/ | Async httpx calls | clients hide slskd/gluetun auth+URL detail |
| importer ↔ staging | filesystem on shared `/data` | hardlink curate, then purge |
| scheduler ↔ core/ | Function invocation on triggers | core modules independently testable |
| status API ↔ state/ | Read-only queries (WAL) | No writes from the API path |

---

## Deployment Artifacts (shape)

### docker-compose.yml (Confidence: HIGH on structure + verified env keys)

```yaml
services:
  gluetun:
    image: qmcgaw/gluetun        # PIN a v3.x tag (control-server auth landed in recent v3.x)
    cap_add: [NET_ADMIN]
    devices: ["/dev/net/tun:/dev/net/tun"]
    networks: [synobridge]
    ports:
      - "5030:5030"              # slskd web/API, published via gluetun's namespace
    volumes:
      - /volume1/docker/gluetun:/gluetun   # persist forwarded_port (60-day) + ctrl auth state
    environment:
      VPN_SERVICE_PROVIDER: private internet access
      VPN_TYPE: wireguard
      OPENVPN_USER: ${PIA_USER}            # (or WIREGUARD_* per gluetun PIA setup)
      OPENVPN_PASSWORD: ${PIA_PASS}
      SERVER_REGIONS: ${PIA_REGION}        # MUST be a PF-capable non-US region (e.g. CA Toronto)
      VPN_PORT_FORWARDING: "on"
      VPN_PORT_FORWARDING_PROVIDER: private internet access
      FIREWALL_OUTBOUND_SUBNETS: ${SYNOBRIDGE_CIDR}   # e.g. 172.20.0.0/16 (must NOT overlap VPN tunnel CIDR)
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"apikey","apikey":"${GLUETUN_API_KEY}"}'
    # gluetun ships a healthcheck used by slskd's depends_on

  slskd:
    image: slskd/slskd           # PIN >= v0.24.4 (native gluetun PF); STACK.md recommends 0.25.1
    network_mode: "service:gluetun"        # shares gluetun netns; NO networks: block here
    depends_on:
      gluetun: { condition: service_healthy }
    environment:
      PUID: ${PUID}
      PGID: ${PGID}
      SLSKD_API_KEY: ${SLSKD_API_KEY}
      SLSKD_VPN: "true"
      SLSKD_VPN_PORT_FORWARDING: "true"
      SLSKD_VPN_GLUETUN_URL: "http://localhost:8000"   # gluetun ctrl in the shared namespace
      SLSKD_VPN_GLUETUN_API_KEY: ${GLUETUN_API_KEY}    # control-server auth (else 401; verify key name)
    volumes:
      - /volume1/docker/slskd/config:/app
      - /volume1/data:/data           # SAME path as *arr & curator (staging + shared + library)
    # NO ports: here — gluetun publishes them

  curator:
    image: ${DOCKERHUB_USER}/curator:latest
    networks: [synobridge]
    depends_on: [gluetun, slskd]
    environment:
      SLSKD_URL: "http://gluetun:5030"     # via gluetun, NEVER http://slskd
      SLSKD_API_KEY: ${SLSKD_API_KEY}
      GLUETUN_CONTROL_URL: "http://gluetun:8000"   # health display only
      GLUETUN_API_KEY: ${GLUETUN_API_KEY}
      LIDARR_URL: "http://lidarr:8686"
      READARR_URL: "http://readarr:8787"
      LIDARR_API_KEY: ${LIDARR_API_KEY}
      READARR_API_KEY: ${READARR_API_KEY}
      SABNZBD_URL: "http://sabnzbd:8080"
      PUID: ${PUID}
      PGID: ${PGID}
    ports:
      - "8674:8674"                         # status API (on synobridge — published directly)
    volumes:
      - /volume1/docker/curator/config:/config
      - /volume1/docker/curator/db:/db      # SQLite state (its own mount, NOT under /data)
      - /volume1/data:/data                 # SAME identical path → atomic hardlinks + ManualImport parity

networks:
  synobridge:
    external: true
```

> **Path-parity fix (important):** slskd and curator both mount `/volume1/data` at the **identical** in-container
> path `/data`. This is what makes `/data/staging/<uuid>/_curated/track.flac` resolve to the same bytes in
> slskd, curator, and the *arr — the precondition for Manual Import accepting the path and for the curate-step
> hardlink being atomic. Confirm the existing Lidarr/Readarr containers already mount this same tree at `/data`;
> align them if not.

### Secrets / env
- PIA creds, *arr API keys, slskd API key, gluetun control-server key in a host-local `.env` (chmod 600,
  git-ignored), referenced as `${VAR}`. Never bake secrets into the image — the image is generic, `.env` is
  host-specific. slskd's own API key + gluetun's control auth live in their bind-mounted config/env. Scan CI
  logs and image layers for leaks (Pitfall #18). Never WAN-publish slskd/Curator (LAN/Tailscale only).

### GitHub Actions → Docker Hub
1. Push to `main` (or a `v*` tag) triggers the workflow.
2. `docker/build-push-action` builds **`linux/amd64`** only (DS423+ is amd64 — no arm64, skip QEMU).
3. Login with `DOCKERHUB_USERNAME` + `DOCKERHUB_TOKEN` repo secrets; tag `:latest` + `:${{ github.sha }}` +
   semver via metadata-action (sha tag enables rollback).
4. Push to Docker Hub. (Action majors per STACK.md: checkout@v4, buildx@v3, login@v3, metadata@v5,
   build-push@v6 — verify none have bumped. `cache: gha` keeps the iterate loop fast.)

### Iterate-by-teardown loop
```
edit → git push → Actions builds & pushes image →
on Synology:  docker compose pull curator && docker compose up -d curator
              (full cycle for compose/env/network changes:  down → pull → up -d)
observe logs + Homepage status → repeat
```
- Curator is stateless except the SQLite file (on a bind mount), so recreate is safe and fast.
- Keep migrations idempotent so a recreated container reconciles against existing state (+ startup
  reconciliation against slskd/*arr/staging).
- For **networking** changes, tear down all three (`down`) and re-up to re-establish the shared netns.

---

## Open Questions / Validation Needed Before Roadmap Hardens

1. **Pin versions:** slskd ≥ v0.24.4 (native gluetun PF; STACK.md recommends 0.25.1) and gluetun v3.x; confirm
   `SLSKD_VPN_*` var casing + the exact control-server-auth key name. (MEDIUM — resolve in step 0.)
2. **gluetun control-server auth** shape (default-role apikey vs per-route `config.toml`) and which routes slskd
   + Homepage need. (MEDIUM — step 0.)
3. **PIA PF-capable region** — confirm current list, pick non-US (CA Toronto/Montreal likely). (MEDIUM.)
4. **Existing *arr mount convention** on this Synology (single `/data` vs split) — dictates volume layout;
   verify against running containers before coding the importer. (MEDIUM.)
5. **Lidarr/Readarr v1 endpoint/command names** (wanted/missing, wanted/cutoff, manualimport, command names)
   against live `/swagger`. (MEDIUM.)
6. **Homepage customapi mapping schema** for the status widget. (LOW impact.)
7. **Manual Import payload shape (the cleanup-flow crux):** the exact `GET /api/v1/manualimport` response and
   the `POST /api/v1/command` ManualImport body (per-file `path`→`albumId`/`trackId`/`bookId` decisions,
   `importMode`, how `rejections[]` are reported). Under-documented and version-sensitive — **spike this in
   Phase 4 against the live API before committing the importer**, modelling off Soularr / the *arr UI's network
   calls. The entire staging→selective-import→purge value depends on getting this right. (MEDIUM-HIGH risk.)
8. **slskd "download into a specific dest folder"** — confirm the slskd transfer API lets Curator direct a
   download into `/data/staging/<uuid>/` (vs a single global download dir). If it only supports a global dir,
   staging becomes "download to global dir → move into per-item quarantine → classify → import → purge"; verify
   in Phase 4. (MEDIUM.)
9. **WANTED/UNWANTED classification rules** — start simple (audio-extension allowlist + expected-track-count for
   music; format-match for books; everything else UNWANTED) and refine from observed junk patterns. (LOW.)

## Sources

- gluetun wiki — "Connect a container to gluetun" (`network_mode: service:gluetun` shares the full netns; cannot
  also join another network; ports published on gluetun):
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/connect-a-container-to-gluetun.md — HIGH
- gluetun wiki — firewall / `FIREWALL_OUTBOUND_SUBNETS` (whitelist LAN/Docker subnet for outbound from inside
  the netns; not needed for browser→WebUI):
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/options/firewall.md ,
  https://deepwiki.com/qdm12/gluetun-wiki/6.5-container-networking — HIGH
- gluetun — `FIREWALL_OUTBOUND_SUBNETS` must NOT overlap the VPN tunnel CIDR or PF breaks (community-verified):
  https://github.com/qdm12/gluetun/issues/2771 — MEDIUM
- gluetun wiki — PIA provider + VPN port forwarding (`VPN_PORT_FORWARDING=on`, `/gluetun` persists port 60 days,
  US not supported for PF):
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/private-internet-access.md ,
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/vpn-port-forwarding.md — HIGH
- gluetun control server API — `/v1/portforward`; old `/v1/openvpn/portforwarded` deprecated; routes
  private/authed by default:
  https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md — HIGH
- slskd VPN docs + native gluetun integration (`SLSKD_VPN`, `SLSKD_VPN_PORT_FORWARDING`, `SLSKD_VPN_GLUETUN_URL`,
  `SLSKD_VPN_GLUETUN_API_KEY`; native PF since v0.24.4, runtime listen-port update):
  https://github.com/slskd/slskd/blob/master/docs/vpn.md ,
  https://newreleases.io/project/github/slskd/slskd/release/0.24.4 ,
  https://github.com/slskd/slskd/discussions/1308 , https://github.com/slskd/slskd/issues/1432 — HIGH
- Fallback port-sync tools (escape hatches, not default): https://github.com/GeoffreyCoulaud/glueforward ,
  https://github.com/tieum/slskd-port-forward-gluetun-server — HIGH (existence)
- Servarr (Lidarr/Readarr) Manual Import (`GET /api/v1/manualimport`, `POST /api/v1/command` ManualImport,
  `rejections[]`, `importMode`); payload under-documented: wiki.servarr.com + Lidarr issue #5647 — MEDIUM
- Homepage gluetun/slskd widgets (need control-server / slskd API keys):
  https://gethomepage.dev/widgets/services/gluetun/ , https://gethomepage.dev/widgets/services/slskd/ — MEDIUM
- Sibling research: `.planning/research/STACK.md`, `FEATURES.md`, `PITFALLS.md`; project brief
  `.planning/PROJECT.md`; `.planning/ROADMAP.md` (horizontal-layer phases) — HIGH

---
*Architecture research for: autonomous fallback-only Soulseek/slskd gap-filler (Lidarr music + Readarr books,
best-effort), Synology homelab with gluetun/PIA*
*Researched: 2026-05-29 · scope-update revision (staging→selective-import→purge cleanup flow + *-arr-agnostic
adapter with Readarr breaker) applied 2026-05-29*
</content>
