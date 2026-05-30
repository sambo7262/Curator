# Technology Stack

**Project:** Curator — autonomous fallback-only P2P media gap-filler (Lidarr/Readarr → slskd/Soulseek)
**Researched:** 2026-05-29
**Overall confidence:** MEDIUM (see "Verification Note" — tool-based live verification was unavailable this session; versions reflect training knowledge through Jan 2026 and MUST be re-pinned before build)

> **Verification Note (read first):** During this research session the sandbox tool layer returned no observable output for any Read/Bash/WebSearch/WebFetch call, so I could not live-verify version pins against Context7 or official docs. Every version below is marked with a confidence level and, where it matters, a one-line "verify with" command. Treat exact patch versions as HYPOTHESES to confirm at build time; treat architecture/wiring guidance as HIGH confidence (it is structural and stable). Do not ship `:latest` — pin after verifying.

---

## Recommended Stack

### Core Orchestrator (the Curator service itself)
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.12.x (3.13 OK) | Orchestrator runtime | Ecosystem fit: every relevant client (*arr, slskd) has Python bindings or trivial REST; fast to write polling/state logic; tiny image on `python:3.12-slim`. 3.12 is the safe LTS-ish line on linux/amd64. |
| httpx | 0.27.x+ | HTTP client for Lidarr/Readarr/slskd REST | Async-capable, connection pooling, timeouts/retries cleaner than `requests`. One client for all three APIs. |
| pydantic | 2.x | Config + API response models | Validates env/YAML config and typed models for *arr/slskd payloads; v2 is the current line. |
| pydantic-settings | 2.x | Env/file config loading | First-class 12-factor config from env (Docker-friendly) + optional YAML. |
| APScheduler | 3.10.x | Poll scheduler | Cron/interval jobs for "scan cutoff-unmet", "check in-flight", "reap stuck" without a separate cron container. (Alt: a plain asyncio loop — fine too.) |
| FastAPI | 0.11x | Status endpoint for Homepage widget | Exposes `/api/status` (JSON) + `/healthz`; async, pairs with httpx; trivial to render for Homepage custom API widget. |
| uvicorn | 0.30.x+ | ASGI server | Runs FastAPI in-container. Single worker is plenty for a homelab control plane. |
| SQLAlchemy | 2.x (Core or ORM) | DB access over SQLite | Stable migrations path if state grows; or use stdlib `sqlite3` directly for minimalism (see Persistence). |
| apprise | 1.9.x | Notifications fan-out | One library → Discord/ntfy/Plex/Telegram/etc. via URL DSL. (See Notifications.) |
| tenacity | 8.x / 9.x | Retry/backoff | Wrap flaky slskd searches and *arr calls with jittered backoff. |

**Confidence:** HIGH on library *choices*; MEDIUM on exact patch versions (pin at build).

### Source Engine (Soulseek access)
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **slskd** | `slskd/slskd:latest` → **pin to a dated tag** (e.g. `0.22.x` line; verify current) | Soulseek daemon w/ REST API + web UI | The de-facto modern Soulseek server for automation. Headless, REST-first, token auth, runs as a container. **Curator drives this directly.** |

**Confidence:** HIGH that slskd is the right engine; MEDIUM on version pin.
**Verify with:** `docker pull slskd/slskd` then check the running build's `/api/v0/application` (returns version), or GitHub releases for `slskd/slskd`.

### VPN Sidecar
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **gluetun** | `qmcgaw/gluetun:latest` → **pin a `v3.x` tag** (verify; the `:v3` major has stabilized port-forward + control server) | VPN tunnel + port-forward provider for slskd | Mature, PIA-aware, exposes a control-server API to read the forwarded port. slskd routes through it via `network_mode`. |

**Confidence:** HIGH on choice; MEDIUM on tag.
**Verify with:** GitHub `qmcgaw/gluetun` releases; confirm the env var names against the gluetun wiki "Private Internet Access" page (names have churned across majors — see Pitfalls).

### Persistence
| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| **SQLite** | bundled (stdlib `sqlite3`, WAL mode) | Dedup/attempt memory, in-flight tracking | Single service, single writer, low volume → SQLite is the correct answer. Bind-mount the `.db` to /volume1 for durability. No Postgres operational cost. |

**Confidence:** HIGH.

---

## The slskd-direct vs Soularr Decision (explicit recommendation)

**Recommendation: Drive slskd DIRECTLY via its REST API from the Curator orchestrator. Do NOT adopt Soularr as a runtime dependency.**

### Why direct
- **Hands-off, fallback-only operation is a control problem, not a "do a one-shot import" problem.** Soularr is fundamentally a *batch matcher*: it reads Lidarr's wanted list, searches slskd, downloads, and triggers import — typically run on a cron/interval. It is excellent for "fill my whole library" but it is opinionated about *what* and *when*, and it does its own matching. Curator's value is the **fallback gate** (only act on what Usenet/SAB genuinely failed to get) and **dedup memory / stuck-detection / status surfacing** — none of which Soularr owns. Wrapping Soularr means fighting its loop.
- **Soularr targets Lidarr; Readarr support is absent/weak.** Curator must cover books too. Building Readarr support on top of Soularr means forking it anyway. Going direct gives one code path for both *arr apps.
- **API stability / blast radius.** Soularr is a community project with its own release cadence and its own bugs; coupling Curator's reliability to it adds a second moving part. slskd's REST API is the stable contract you actually want to depend on.
- **You already need an orchestrator** (poller, state store, Homepage endpoint, notifications). Once you have that, the slskd search/download/import calls are a thin module — Soularr would duplicate the orchestrator you're building.

### What you reuse from Soularr (don't reinvent)
Read Soularr's source as a **reference implementation** for the slskd search→match→download→import flow and especially its **track/album matching heuristics** (filename/format/quality scoring). That matching logic is the genuinely hard part; lift the approach, not the runtime.

### When Soularr WOULD be the right call
If the goal were "bulk-backfill my entire Lidarr library from Soulseek, one-shot, music-only, I'll babysit it" — use Soularr, it's purpose-built and faster to stand up. That is *not* Curator's mission.

**Confidence:** HIGH (this is an architecture judgment, robust to version drift).

---

## slskd API surface (what Curator calls)

Base: `http://slskd:5030/api/v0` (default container port 5030). **Auth model:** API key via header `X-API-Key: <key>` (set in slskd config `web.authentication.api_keys`), OR JWT obtained from `POST /api/v0/session` with username/password. **Use an API key for service-to-service** — no token refresh, simplest for hands-off.

Key endpoints (verify exact shapes against your running build's Swagger at `/swagger`):
- `GET  /api/v0/application` — version/health.
- `POST /api/v0/searches` — start a search `{ "searchText": "...", "id": "<uuid>" }`.
- `GET  /api/v0/searches/{id}` — poll search state.
- `GET  /api/v0/searches/{id}/responses` — candidate files per peer (username, files[], bitrate, size, freeUploadSlots, queueLength).
- `POST /api/v0/transfers/downloads/{username}` — enqueue downloads (array of `{filename, size}`).
- `GET  /api/v0/transfers/downloads` — in-flight/queued/completed state (for stuck detection).
- `DELETE /api/v0/transfers/downloads/{username}/{id}` — cancel a stuck transfer.
- `GET  /api/v0/options` — read effective config (incl. listen port — relevant to gluetun wiring).

**Confidence:** HIGH on the shape of the surface; MEDIUM on exact path casing/versions — confirm via the live `/swagger` once slskd is up.

---

## Lidarr & Readarr APIs

Both are Servarr-family; same auth and command model. **Auth:** `X-Api-Key` header (key in Settings → General). Base: `http://lidarr:8686/api/v1`, `http://readarr:8787/api/v1`.

> Note: Lidarr's web API is **v1** (not v3 — that's Radarr/Sonarr). Readarr is also **v1**.

Endpoints Curator needs:
- **Wanted/missing:** `GET /api/v1/wanted/missing?page=1&pageSize=...&sortKey=...` — items with no acceptable file.
- **Cutoff-unmet:** `GET /api/v1/wanted/cutoff?...` — items below cutoff quality (the "Usenet got *something* but not good enough" set).
- **Quality profiles:** `GET /api/v1/qualityprofile` — to honor cutoff logic and pick acceptable formats.
- **Album/Artist (Lidarr):** `GET /api/v1/album?...`, `GET /api/v1/artist` — to resolve what's monitored.
- **Book/Author (Readarr):** `GET /api/v1/book`, `GET /api/v1/author`.
- **History (the fallback signal):** `GET /api/v1/history?...` and `GET /api/v1/history/failed` — to detect that the Usenet/SAB grab *failed* (this is how Curator stays "fallback-only" instead of racing the primary pipeline).
- **Queue:** `GET /api/v1/queue` — what's currently being grabbed (don't act if primary is in-flight).
- **Manual import:** `GET /api/v1/manualimport?folder=<path>` to get import candidates, then `POST /api/v1/command` with `{ "name": "ManualImport", "files": [...] }` (or `{ "name": "DownloadedAlbumsScan"/"DownloadedBooksScan", "path": "..." }`).
- **Commands:** `POST /api/v1/command` — `RefreshArtist`/`RefreshAuthor`, `RescanArtist`/`RescanFolder`, `MissingAlbumSearch` etc. `GET /api/v1/command/{id}` to poll completion.

### Readarr unmaintained-status implication (IMPORTANT)
Readarr development has been **effectively wound down / archived** by the Servarr team (announced 2025). Practical consequences for Curator:
- **The v1 API is frozen, not evolving** — which is actually *good* for a stable integration contract: it won't break under you.
- **No new bugfixes** — expect quirks in metadata/matching; design Curator to be defensive (validate responses, tolerate missing fields).
- **Plan for a successor.** Community forks/replacements exist or are emerging; keep the Readarr integration behind an interface so a future swap (e.g. a replacement reader-media manager) is a single adapter change.
- **Mitigation:** Curator should treat Readarr import via the **folder-scan command path** (drop file in monitored folder → trigger scan) which is the most stable, least-API-surface approach if manualimport gets flaky.

**Confidence:** HIGH on Lidarr API; MEDIUM-HIGH on Readarr (frozen API is stable but under-documented); HIGH on the unmaintained framing.
**Verify with:** each app's `/swagger` (Settings → General → "Open API"/ `http://host:port/swagger`).

---

## VPN: gluetun + PIA + port-forward → slskd (concrete wiring)

### The hard requirement
Soulseek needs an **inbound listen port** to receive connections (better search results, ability to be browsed/queued by peers). PIA provides **port forwarding only on specific regions** — and **US regions do NOT support port forwarding.** You must use a PF-capable region.

**PIA regions that support port forwarding (NOT exhaustive; verify on PIA's PF list):** CA Toronto/Montreal/Vancouver, most EU (Netherlands, Switzerland, Germany, France, UK, etc.), and several APAC. **Avoid all US locations for the slskd tunnel.** Closest-to-US PF-capable, low-latency pick: **CA Toronto / CA Montreal.**

**Confidence:** HIGH on "US has no PF"; MEDIUM on the exact current region list — verify against PIA's published port-forwarding region list before pinning a region.

### gluetun container env (PIA + PF)
```yaml
gluetun:
  image: qmcgaw/gluetun:latest   # PIN a v3.x tag after verifying
  cap_add: [NET_ADMIN]
  devices: [/dev/net/tun:/dev/net/tun]
  environment:
    - VPN_SERVICE_PROVIDER=private internet access
    - OPENVPN_USER=${PIA_USER}
    - OPENVPN_PASSWORD=${PIA_PASS}
    - SERVER_REGIONS=CA Toronto        # MUST be a PF-capable region (not US)
    - VPN_PORT_FORWARDING=on
    - VPN_PORT_FORWARDING_PROVIDER=private internet access
    # control server (read the forwarded port from here):
    - HTTP_CONTROL_SERVER_ADDRESS=:8000
  networks: [synobridge]
  ports:
    - "5030:5030"   # PUBLISH slskd's web/API port HERE (see network_mode note)
```
> **Env-name caveat:** gluetun env var names have changed across majors (`VPNSP` → `VPN_SERVICE_PROVIDER`, `PORT_FORWARDING` → `VPN_PORT_FORWARDING`, etc.). The names above match the current v3 line per training knowledge — **confirm against the gluetun wiki PIA page** at build time.

### slskd routed THROUGH gluetun, still reachable on synobridge
The pattern: slskd uses **`network_mode: "service:gluetun"`** so it shares gluetun's network namespace (all slskd traffic exits via the VPN). Because of this, **slskd publishes NO ports itself** — you publish slskd's `5030` on the *gluetun* service (shown above). slskd is then reachable at `http://gluetun:5030` from other synobridge containers, and Curator calls it there.

```yaml
slskd:
  image: slskd/slskd:latest   # PIN after verifying
  network_mode: "service:gluetun"   # share gluetun's netns → VPN egress
  depends_on: [gluetun]
  # NO ports: here — they live on gluetun
  volumes:
    - /volume1/docker/slskd:/app
    - /volume1/data/music:/data/music   # adjust to your share layout
  environment:
    - SLSKD_REMOTE_CONFIGURATION=true
```
Curator and Homepage reach the API as **`http://gluetun:5030/api/v0`** (the slskd port published on gluetun). This satisfies "route slskd through VPN AND keep it reachable on synobridge for the API."

### Feeding the forwarded port into slskd's listen port
slskd does not auto-read gluetun's forwarded port, and gluetun assigns it dynamically. Two robust options:

1. **gluetun control-server API (recommended for Curator):** Curator (or a tiny sidecar/cron) polls `GET http://gluetun:8000/v1/openvpn/portforwarded` → `{"port": NNNNN}`, then sets slskd's listen port via slskd's API/config and (if changed) restarts/reconfigures slskd. Since Curator already orchestrates, **make this a Curator startup + periodic task.** This is the cleanest, no-extra-container path.
2. **gluetun file output:** gluetun can write the forwarded port to a file (`VPN_PORT_FORWARDING_STATUS_FILE=/tmp/forwarded_port` on a shared volume); a script reads it and patches slskd config. More moving parts than option 1.

> slskd listen port config key: `soulseek.listen_port` (env `SLSKD_SLSK_LISTEN_PORT` — verify exact name). Set it to the PIA forwarded port. If the port can't be set without restart on your slskd version, have Curator restart the slskd container (via Docker socket — adds a dependency) or set slskd to re-read config.

**Confidence:** HIGH on the network_mode/namespace pattern and the control-server-port approach; MEDIUM on exact gluetun env var names and slskd config keys (verify both).

---

## Notifications

**Recommendation: Apprise (in-process Python library), config-driven via env.**

| Option | Verdict | Why |
|--------|---------|-----|
| **Apprise (lib)** | **CHOSEN** | One dependency, one URL DSL → Discord, ntfy, Telegram, Plex, email, Slack, etc. Curator builds an `Apprise()` object from a list of URLs in config and calls `.notify()` on grab/failure/blocked/stuck events. Flexible, zero per-service code, swap targets by editing env. |
| Native Discord webhook only | Reject | Locks you to one channel; you'll want ntfy push for "stuck" alerts and maybe Plex. |
| Apprise API (separate container) | Optional | Only if you want other apps to share it; for a single service the in-process lib is simpler. |

Event taxonomy Curator should emit: `grabbed` (info), `imported` (info), `failed`/`no-source-found` (warning), `blocked` (primary still in-flight — debug), `stuck` (transfer stalled past threshold — warning/error). Map severities to Apprise tags so users can route (e.g. ntfy for errors, Discord for everything).

**Confidence:** HIGH.

---

## Homepage (gethomepage.dev) integration

**Recommendation: Custom API widget** pointed at Curator's `/api/status`. This is the most flexible Homepage widget — you control the JSON and the field mapping.

Curator exposes (FastAPI):
```
GET /api/status  →
{
  "gap_queue":  12,   // gaps detected, awaiting action
  "in_flight":   3,   // currently downloading via slskd
  "stuck":       1,   // stalled transfers
  "imported_24h": 7
}
```

Homepage `services.yaml` entry:
```yaml
- Curator:
    icon: mdi-music-box-multiple
    href: http://gluetun:5030   # or Curator's own UI if you add one
    widget:
      type: customapi
      url: http://curator:8080/api/status
      refreshInterval: 30000
      mappings:
        - field: gap_queue
          label: Gap Queue
        - field: in_flight
          label: In-Flight
        - field: stuck
          label: Stuck
          additionalField:                 # optional color/emphasis
            field: stuck
        - field: imported_24h
          label: Imported 24h
```
The `customapi` widget (type: `customapi`) supports field mappings, multiple fields, refresh interval, and an optional bearer/api-key header (`headers:`) if you protect `/api/status`. Keep the endpoint unauthenticated on synobridge or use a static header token.

**Confidence:** HIGH on the customapi approach; MEDIUM on exact mapping sub-keys (Homepage's customapi schema is stable but evolving — verify the `mappings`/`additionalField` keys against current Homepage docs).

---

## CI/CD — GitHub Actions → Docker Hub (linux/amd64)

Target is a single Synology J4125 = **linux/amd64 only** (no need for arm builds → faster CI). Pattern:

```yaml
name: build
on:
  push:
    tags: ['v*']
    branches: [main]
jobs:
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: youruser/curator
          tags: |
            type=semver,pattern={{version}}
            type=sha
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Action versions (current major lines as of late 2025/early 2026):
- `actions/checkout@v4`
- `docker/setup-buildx-action@v3`
- `docker/login-action@v3`
- `docker/metadata-action@v5`
- `docker/build-push-action@v6`

Use a **Docker Hub access token** (not password) in `DOCKERHUB_TOKEN`. `cache-from/to: type=gha` for fast incremental builds. Since single-arch, you can skip QEMU/`setup-qemu-action`.

**Confidence:** HIGH on the pattern; MEDIUM on action patch tags (the major versions above are correct as of cutoff — confirm none have bumped, e.g. build-push to v7).

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Source engine driver | slskd direct (REST) | Soularr | Soularr is a batch matcher, Lidarr-only, owns its own loop; Curator needs a fallback gate + Readarr + state/status it doesn't provide. Reuse its matching logic, not its runtime. |
| Source engine | slskd | Nicotine+ / raw soulseek-network libs | Nicotine+ is a GUI desktop client, not headless/API-first; raw protocol libs are far more work and less maintained. slskd is the automation-native choice. |
| Orchestrator lang | Python | Go / Node | Python has the tightest *arr/slskd ecosystem fit + Soularr reference is Python; Go would be smaller/faster but slower to write and no ecosystem advantage for a homelab control plane. |
| State store | SQLite | Postgres | Single writer, low volume; Postgres adds a container + ops cost for zero benefit at this scale. |
| Notifications | Apprise | Native per-service webhooks | Apprise = one lib, all targets, env-swappable. |
| Homepage widget | customapi | Building a Homepage "official" integration | customapi needs no upstream PR and gives full control of the JSON contract. |
| VPN | gluetun | wireguard-only custom sidecar | gluetun bakes in PIA PF + a control server to read the forwarded port — exactly the awkward part. |
| Scheduler | APScheduler / asyncio loop | separate cron container | In-process scheduling keeps Curator a single deployable unit. |

---

## Installation (orchestrator)

```bash
# pyproject / requirements (pin exact versions after a fresh resolve)
httpx>=0.27
pydantic>=2.6
pydantic-settings>=2.2
fastapi>=0.111
uvicorn[standard]>=0.30
apscheduler>=3.10
apprise>=1.9
tenacity>=8.5
# SQLAlchemy optional if not using stdlib sqlite3:
SQLAlchemy>=2.0
```

Container base: `python:3.12-slim`; multi-stage build; run as non-root; bind-mount `/volume1/docker/curator/data` for the SQLite db + config.

---

## Open Items to Verify Before Build (carry into roadmap)

1. **Pin exact versions** for: slskd, gluetun (and confirm v3 env var names), all Python libs, GitHub Action majors. (Tool verification was unavailable this session.)
2. **Confirm PIA port-forwarding region list** is current and pick a non-US PF region (likely CA Toronto/Montreal).
3. **Confirm slskd listen-port config key** and whether it can be set without a container restart (drives the gluetun-PF → slskd-port mechanism).
4. **Confirm gluetun control-server path** `/v1/openvpn/portforwarded` for the current major.
5. **Confirm Homepage `customapi` mapping schema** (`mappings`, `additionalField`).
6. **Validate Readarr `/swagger`** on your actual install to lock the import/command path before depending on it.

## Sources

- slskd: GitHub `slskd/slskd` (releases, `/swagger` on running instance) — VERIFY (not live-checked this session)
- Soularr: GitHub (read as reference implementation) — VERIFY
- Servarr/Lidarr/Readarr API: each app's `/swagger`; wiki.servarr.com — VERIFY
- gluetun: GitHub `qmcgaw/gluetun` wiki, "Private Internet Access" page — VERIFY
- PIA port-forwarding region list: PIA official docs — VERIFY
- Homepage customapi widget: gethomepage.dev docs — VERIFY
- Apprise: GitHub `caronc/apprise` — VERIFY
- GitHub Actions: `docker/build-push-action`, `docker/metadata-action`, `actions/checkout` repos — VERIFY

> All "VERIFY" markers exist because the session's tool layer returned no output; the guidance reflects training knowledge through Jan 2026, which covers this ecosystem well, but version pins must be confirmed.
