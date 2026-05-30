# Stack Research

**Domain:** Autonomous, fallback-only P2P (Soulseek) media gap-filler orchestrating Lidarr/Readarr on a Synology homelab
**Researched:** 2026-05-29
**Confidence:** HIGH on the source-engine decision, *arr APIs, gluetun+PIA wiring (including slskd's native VPN integration and the gluetun auth syntax), persistence, Homepage, notifications, and CI/CD. Library versions verified against PyPI/project pages.

---

## Headline Decisions (read this first)

1. **Source engine: drive `slskd` directly via its REST API. Do NOT use Soularr.** Soularr is Lidarr-only (no Readarr/books), is a stateless cron script with a known reputation for redundant downloads, mismatches, and quality drift — i.e. it reproduces the exact pain the owner already hit. Curator exists to *replace* that labor with stateful, correct, hands-off orchestration. Building on `slskd-api` + the slskd REST surface gives Curator full control over matching, dedup memory, quality gating, and books. (Detail + rationale below.)

2. **VPN: `slskd` runs with `network_mode: service:gluetun`. slskd has NATIVE gluetun integration (`integration.vpn.gluetun`, since v0.24.4) — it polls gluetun's control server, waits for VPN-ready, and auto-syncs its Soulseek listen port to PIA's forwarded port. Curator does NOT need to build port-sync.** PIA port forwarding works only on **non-US** regions. Curator stays on `synobridge` and reaches slskd via gluetun's published port. The one gotcha: recent gluetun requires control-server auth or slskd gets 401. (Exact wiring below — most important integration detail.)

3. **Orchestrator language: Python 3.12.** The whole ecosystem (`slskd-api`, `pyarr`, Apprise) is Python; matching it maximizes leverage and minimizes glue code.

4. **State store: SQLite (WAL) via SQLModel/SQLAlchemy.** Single service, single writer, embeddable, zero ops. Postgres is unjustified here.

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| **slskd** (`slskd/slskd`, pin `0.25.1`) | 0.25.1 (Apr 2026) | Headless Soulseek daemon: search, download, **share management** (satisfies give-back), **native VPN/gluetun integration** | De-facto modern Soulseek server. Full REST API + API-key auth, manages shares natively (handles leecher-blocking), and — critically — since 0.24.4 natively polls gluetun and auto-applies PIA's forwarded port as its listen port. AGPLv3 + Additional Terms since 0.25.0; 0.25.x also added PUID/PGID Docker handling. |
| **Python** | 3.12 | Curator orchestrator runtime | The *arr + Soulseek automation ecosystem is Python. `slskd-api` (needs >=3.11), `pyarr`, `apprise` are first-class. No other runtime gives this leverage. |
| **gluetun** (`qmcgaw/gluetun`, pin a dated tag) | latest v3.x | VPN sidecar; slskd shares its network namespace; exposes the control server slskd polls | Standard homelab VPN gateway. Built-in PIA support with port forwarding and a control server (`/v1/openvpn/portforwarded`) that slskd's native integration consumes. NOTE: recent gluetun requires control-server auth on all routes (see wiring). |
| **SQLite** | bundled w/ Python; file under `/volume1/docker/curator/db/` | Persistent dedup/state memory (attempted / in-flight / succeeded / unavailable + backoff) | Single-service, single-writer workload. Embedded, transactional, zero admin. WAL mode covers poller + API concurrency. |
| **FastAPI + Uvicorn** | FastAPI 0.115+, Uvicorn 0.34+ | Curator's JSON status endpoint (gap queue / in-flight / stuck) for the Homepage `customapi` widget; plus `/healthz` | Minimal, async, auto-JSON. Homepage's `customapi` widget just needs a clean JSON endpoint; FastAPI delivers it in a few lines and coexists with the background poller. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| **slskd-api** (`bigoulours/slskd-python-api`) | 0.1.5 (Apr 2026; needs Python >=3.11) | Typed Python client for the slskd REST API (searches, transfers, shares, options) | Primary interface to the source engine. Avoids hand-rolling slskd HTTP. Fall back to raw `httpx` for any endpoint the wrapper lags on. |
| **pyarr** | 6.x (6.4.0+, latest; pin a specific 6.x) | Python client for **Lidarr** and **Readarr** (v1) — wanted/missing, cutoff, quality profiles, commands, manual import | One library covers both apps (plus Sonarr/Radarr/Prowlarr if ever needed). Actively updated through 2026. Pin a specific 6.x and keep raw-`httpx` fallbacks for Readarr's quirkier endpoints. |
| **httpx** | 0.28.1 | Async HTTP for anything not covered by the typed clients (raw *arr/slskd endpoints, health checks) | The escape hatch + one consistent async HTTP stack. |
| **APScheduler** | 3.10+ | Schedules the periodic gap-scan / grace-window / backoff poller | Cleaner than hand-rolled sleep loops; interval + cron triggers, jitter, pause/resume. |
| **SQLModel** (or SQLAlchemy 2.x) | SQLModel 0.0.22+ | Typed ORM over SQLite for state tables | Pydantic-native (pairs with FastAPI). Plain SQLAlchemy if you prefer no extra layer. |
| **Apprise** | 1.9.x | Unified push notifications: grab / failure / blocked events | One config string → Discord, ntfy, Plex, Telegram, 90+ targets. No per-service webhook code; owner swaps targets via env. |
| **pydantic-settings** | 2.x | Typed env/YAML config loading | Curator config (grace window, thresholds, URLs, keys) validated from env — fits the compose deploy model. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| **uv** (Astral) | Dependency mgmt + lockfile | `uv.lock` for reproducible CI; far faster than pip. `pip` + `requirements.txt` is a fine fallback. |
| **ruff** | Lint + format | One tool, near-zero config. |
| **Docker buildx (Buildkit)** | Build `linux/amd64` image | Targets the J4125 from any CI host. |
| **GitHub Actions** | CI/CD: build → push Docker Hub | See CI/CD section for pinned action versions + buildx pattern. |

---

## The Source Engine Decision: slskd-direct vs Soularr (explicit)

**Recommendation: slskd-direct (Curator drives `slskd` via `slskd-api`/REST). Bypass Soularr entirely.**

| Criterion | slskd-direct (RECOMMENDED) | Soularr + slskd |
|-----------|----------------------------|-----------------|
| Books / Readarr | Curator implements it; Readarr is just another *arr REST API | **Not supported — Lidarr only.** Hard blocker for the books requirement. |
| Dedup / "don't re-grab" memory | Curator owns SQLite state (attempted/succeeded/unavailable + backoff) | Stateless cron run; redundant downloads were a reported weakness + a stated owner pain |
| Match correctness | Curator controls matching, gates on *arr metadata | Filename-ratio threshold (≥0.8) + blacklists; owner already hit wrong matches |
| Quality (defer to *arr profiles) | Curator reads the *arr quality profile/cutoff and filters before grabbing | Soularr has its own filtering — the "separate quality logic that downgrades" risk PROJECT explicitly avoids |
| Fallback-only ("grace then fallback") | Curator decides *when* to act (after grace, only if Usenet hasn't satisfied) | Just grabs wanted items; no grace/fallback coordination with Usenet |
| Hands-off | The design goal | "Worked but required heavy manual labor" — owner's actual experience |
| Status to Homepage + notifications | First-class — Curator owns its state and API | Bolt-on only (separate Soularr-Dashboard project) |
| Build cost | Higher up front (implement search→match→import) | Lower up front, but re-imports the exact problems Curator exists to solve |

Soularr is valuable as a **reference implementation** — it proves the `pyarr` + `slskd-api` combo works and shows the search/match/import flow end-to-end. Read its source. But adopting it as the engine re-imports its limitations (Lidarr-only, stateless, owner-confirmed labor). Curator's mandate ("build whatever it takes for true hands-off operation") points squarely at slskd-direct.

**Maintained alternatives considered and rejected:** there is no better-maintained, multi-app (music+books) hands-off Soulseek orchestrator than what Curator would build. Soularr forks (Soularr-Dashboard, binhex/arch-soularr, Unraid/Windows setups) are packaging/UX variants of the same Lidarr-only engine. slskd is the only stable, maintained primitive — so build on the primitive.

---

## VPN Wiring: gluetun + PIA → slskd (concrete — most important section)

### Network topology
- `gluetun` joins `synobridge` and **publishes** slskd's web port.
- `slskd` uses `network_mode: "service:gluetun"` — no network of its own; **all** its traffic (incl. Soulseek peer connections) exits via the PIA tunnel, and its web/API port is reachable through gluetun's published port. This is how slskd is VPN-routed while its API stays reachable on `synobridge`.
- `curator` stays on `synobridge`. It reaches **slskd** at `http://gluetun:<slskd-web-port>` and reaches **Lidarr/Readarr/Plex/SAB** by their own container names.

### PIA port forwarding facts (verified)
- `VPN_PORT_FORWARDING=on` + `VPN_PORT_FORWARDING_PROVIDER=private internet access` makes gluetun request a forwarded port.
- **US servers do NOT support PIA port forwarding** — it works on essentially all non-US PIA servers. Use a non-US region near home, e.g. **CA Toronto / CA Montreal / CA Vancouver**, or Switzerland, Netherlands, Romania, etc. Set via `SERVER_REGIONS`. The PIA port is random and **rotates on reconnect**, so it must be read dynamically.
- gluetun exposes the current forwarded port on its **control server**: `GET http://<gluetun>:8000/v1/openvpn/portforwarded` → `{"port": NNNNN}`.

### Feeding the forwarded port into slskd — NATIVE (the win)
**slskd has built-in gluetun integration since v0.24.4. Curator does NOT need to build a port-sync loop.** Configure slskd's `integration.vpn`:

```yaml
# slskd config (or SLSKD_VPN_* env vars)
integration:
  vpn:
    enabled: true            # SLSKD_VPN=true
    port_forwarding: true     # SLSKD_VPN_PORT_FORWARDING=true
    polling_interval: 2500    # SLSKD_VPN_POLLING_INTERVAL
    gluetun:
      url: http://localhost:8000   # SLSKD_VPN_GLUETUN_URL (localhost: shares gluetun's netns)
      timeout: 1000
      api_key: ${GLUETUN_API_KEY}  # SLSKD_VPN_GLUETUN_API_KEY (must match gluetun's control-server key)
```

Behavior: slskd polls gluetun, **delays connecting to the Soulseek server until the VPN reports ready**, auto-applies the forwarded port as its Soulseek listen port, and on VPN drop it disconnects and re-polls. This directly satisfies "route source traffic through VPN, keep firewall closed, hands-off."

### Gluetun control-server auth gotcha (verified — REQUIRED, exact syntax)
Recent gluetun makes **all control-server routes private by default** — there are no public routes. Without auth, slskd gets `401 Unauthorized` (slskd issue #1660). Configure an apikey role on gluetun and pass the same key to slskd:

```yaml
gluetun environment:
  # exact verified syntax:
  HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"apikey","apikey":"abcd123"}'

slskd environment:
  SLSKD_VPN_GLUETUN_URL: http://localhost:8000
  SLSKD_VPN_GLUETUN_API_KEY: abcd123   # must equal the gluetun apikey
```
(Auth precedence in gluetun: if an apikey is configured → apikey auth; else if a username is set → basic auth; else unauthenticated. For fine-grained control you can use a control-server `config.toml` with per-route roles, but the default-role apikey above is enough for slskd to read `/v1/openvpn/portforwarded`.)

### Example compose skeleton (illustrative)
```yaml
services:
  gluetun:
    image: qmcgaw/gluetun:latest        # pin a dated tag once chosen
    cap_add: [NET_ADMIN]
    networks: [synobridge]
    ports:
      - "5030:5030"     # slskd web/API, published via gluetun (verify slskd's web port for the pinned image)
    environment:
      VPN_SERVICE_PROVIDER: private internet access
      OPENVPN_USER: ${PIA_USER}
      OPENVPN_PASSWORD: ${PIA_PASS}
      SERVER_REGIONS: CA Toronto                 # NON-US, port-forwarding-capable
      VPN_PORT_FORWARDING: "on"
      VPN_PORT_FORWARDING_PROVIDER: private internet access
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"apikey","apikey":"${GLUETUN_API_KEY}"}'
    volumes:
      - /volume1/docker/gluetun:/gluetun

  slskd:
    image: slskd/slskd:0.25.1
    network_mode: "service:gluetun"     # shares VPN netns; routed + reachable via gluetun
    depends_on: [gluetun]
    environment:
      SLSKD_API_KEY: ${SLSKD_API_KEY}            # role/cidr-scoped key for Curator
      SLSKD_VPN: "true"
      SLSKD_VPN_PORT_FORWARDING: "true"
      SLSKD_VPN_GLUETUN_URL: http://localhost:8000   # localhost == gluetun's netns
      SLSKD_VPN_GLUETUN_API_KEY: ${GLUETUN_API_KEY}
    volumes:
      - /volume1/docker/slskd:/app
      - /volume1/music:/data/music                # shares satisfy give-back

  curator:
    image: <dockerhub-user>/curator:latest
    networks: [synobridge]
    depends_on: [slskd]
    environment:
      SLSKD_URL: http://gluetun:5030             # slskd via gluetun's published port
      SLSKD_API_KEY: ${SLSKD_API_KEY}
      LIDARR_URL: http://lidarr:8686
      READARR_URL: http://readarr:8787
    volumes:
      - /volume1/docker/curator:/config
      - /volume1/music:/data/music
      - /volume1/books:/data/books

networks:
  synobridge:
    external: true
```

> With `network_mode: service:gluetun`, slskd's ports are declared on the **gluetun** service, and slskd reaches the control server at `http://localhost:8000` (same netns). Verify slskd's actual web port for the pinned image and align the published port + `SLSKD_URL`.

---

## *arr API Reference (Lidarr v1 / Readarr v1)

Both expose the Servarr v1 surface. Auth = `X-Api-Key` header (per-app key). Endpoints Curator needs:

| Need | Endpoint | Notes |
|------|----------|-------|
| Missing items | `GET /api/v1/wanted/missing` | Paged; monitored items with no file |
| Cutoff-unmet | `GET /api/v1/wanted/cutoff` | Items below quality cutoff |
| Quality profiles | `GET /api/v1/qualityprofile` | Read profile/cutoff to gate quality (no separate logic) |
| Trigger search / refresh / rescan | `POST /api/v1/command` | Lidarr: `MissingAlbumSearch`, `AlbumSearch`, `RefreshArtist`, `RescanFolder`. Readarr: `MissingBookSearch`, `BookSearch`, `RefreshAuthor`, `RescanFolder`. Poll status by command id. |
| List import candidates | `GET /api/v1/manualimport?folder=<drop>` | After slskd downloads, list what the *arr would import from the drop path |
| Trigger import | `POST /api/v1/command` `ManualImport` (or `POST /api/v1/manualimport`) | Hand files to the *arr so they import into `/volume1` and Plex sees them. Payload is under-documented — model it off Soularr / the *arr UI network calls. |
| Records | Lidarr `GET /api/v1/artist`, `/album`; Readarr `/author`, `/book` | Match the right release/edition |

**Readarr unmaintained-status implication:** upstream dev halted in 2024; it still runs but gets no fixes. The **API surface is therefore frozen — which is good for Curator** (a stable, non-moving target). Risks: no bugfixes, possible metadata-server fragility over time. Mitigation: pin everything, keep raw-`httpx` fallbacks for Readarr endpoints (especially ManualImport — note open Readarr issues #2042/#2230 around manual import behavior), and isolate books as a pluggable module that can later retarget a Readarr fork or Calibre flow without touching the music path.

---

## Persistence: SQLite vs Postgres (recommendation: SQLite)

| Factor | SQLite (RECOMMENDED) | Postgres |
|--------|----------------------|----------|
| Service shape | Single service, single writer — ideal | Built for concurrent writers; overkill |
| Ops | Zero — a file on a bind-mount | Second container, healthcheck, backups, tuning |
| Concurrency | Poller + FastAPI reader handled by WAL | Strong, unneeded |
| Footprint | Negligible | More RAM/CPU + another failure point |
| Backup | Copy the file | `pg_dump` / volume mgmt |

Use SQLite **WAL mode** at `/volume1/docker/curator/db/curator.db`. Schema sketch: a `gap` table (foreign id, app, state ∈ {attempted, in_flight, succeeded, unavailable}, attempts, next_retry_at, last_error, slskd_search_id, first_seen, grace_until). Reserve Postgres only if Curator ever becomes multi-instance — not foreseeable.

---

## Homepage Integration (`customapi` widget)

Curator's FastAPI service exposes one JSON status endpoint; Homepage renders it with the built-in **`customapi`** widget — no Homepage code, just YAML in the existing `services.yaml`.

**Curator endpoint (example):** `GET http://curator:8675/status` →
```json
{ "queued": 12, "in_flight": 3, "stuck": 1, "succeeded_24h": 7, "last_run": "2026-05-29T20:00:00Z" }
```

**Homepage `services.yaml` entry:**
```yaml
- Curator:
    icon: mdi-music-box
    href: http://curator:8675
    widget:
      type: customapi
      url: http://curator:8675/status
      refreshInterval: 30000
      mappings:
        - { field: queued,    label: Queued,    format: number }
        - { field: in_flight, label: In Flight, format: number }
        - { field: stuck,     label: Stuck,     format: number }
        - { field: last_run,  label: Last Run,  format: relativeDate }
```
`customapi` supports dot-notation (`data.value`), array indexing (`locations.1.name`), formats (text/number/float/percent/date/relativeDate/size/duration), `remap`, `prefix`/`suffix`/`scale`, custom headers, list display mode, and `refreshInterval` (ms). Keep the JSON flat to keep mappings trivial. slskd also has a **native Homepage widget** — use it alongside Curator's for raw transfer stats.

---

## Notifications (recommendation: Apprise)

Use **Apprise** rather than hand-coding Discord/ntfy/Plex webhooks. One library, one config (URLs like `discord://...`, `ntfy://...`, `plex://...`), and the owner adds/swaps targets via env with no code change. Fire on the three project events: **grab** (started/succeeded), **failure** (import/download failed), **blocked** (leecher-blocked / no source / stuck past timeout). Native single-service webhooks only make sense if you deliberately want zero dependencies — not the case here, where flexible hands-off reconfig wins.

---

## CI/CD: GitHub Actions → Docker Hub (`linux/amd64`)

Pinned action majors (current): `actions/checkout@v4`, `docker/setup-qemu-action@v3`, `docker/setup-buildx-action@v3`, `docker/login-action@v3`, `docker/metadata-action@v5`, `docker/build-push-action@v6`.

```yaml
name: build-push
on:
  push:
    branches: [main]
    tags: ["v*"]
jobs:
  docker:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3        # optional: ubuntu-latest is amd64; needed only if adding arm64
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ${{ secrets.DOCKERHUB_USERNAME }}/curator
          tags: |
            type=ref,event=branch
            type=semver,pattern={{version}}
            type=sha
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64                  # single target → simple & fast for the J4125
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```
`ubuntu-latest` is amd64, so QEMU is optional unless you later add arm64. `type=gha` cache keeps the iterate-rebuild loop fast.

---

## Installation

```bash
# Curator orchestrator (pyproject.toml / requirements.txt)
# Core
pip install "fastapi>=0.115" "uvicorn[standard]>=0.34" "httpx>=0.28" \
            "apscheduler>=3.10" "sqlmodel>=0.0.22" "pydantic-settings>=2"
# Domain clients
pip install "pyarr>=6.4" "slskd-api>=0.1.5" "apprise>=1.9"
# Dev
pip install -U ruff
# (or `uv add ...` with a uv.lock for reproducible CI)
```

Container images (compose, not pip):
```
slskd/slskd:0.25.1
qmcgaw/gluetun:latest   # pin to a dated tag once chosen
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| slskd-direct (Curator owns logic) | Soularr + slskd | Only if music-only, throwaway, and you accept stateless cron behavior. Not viable here (no Readarr/books, reproduces owner's pain). |
| Python 3.12 orchestrator | Go / Node | If you wanted one static binary and were willing to reimplement `pyarr`/`slskd-api`. Python ecosystem leverage outweighs it. |
| SQLite | Postgres | Only if Curator ever runs multi-instance with concurrent writers. Not foreseeable. |
| slskd NATIVE gluetun integration | Curator/sidecar port-sync loop (tieum/slskd-port-forward-gluetun-server, glueforward) | Only if pinned to slskd < 0.24.4. With 0.24.4+, native is strictly simpler — no extra moving parts. |
| `network_mode: service:gluetun` | gluetun as a routed gateway / `FIREWALL_OUTBOUND_SUBNETS` | Routing many containers through one VPN; more config. Shared-netns is simplest for one VPN'd service. |
| Apprise | Native Discord/ntfy webhooks | Zero-dependency purity; loses multi-target flexibility. |
| FastAPI status endpoint | slskd's native Homepage widget alone | Use slskd's widget *in addition*; it can't express Curator's gap-queue/grace/backoff state. |
| `customapi` Homepage widget | Standalone UI | Never — explicitly out of scope. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Soularr as the engine | Lidarr-only (no books), stateless, owner-confirmed redundant-download/mismatch/quality issues, no grace/fallback | slskd-direct via `slskd-api` (read Soularr only as reference) |
| PIA **US** servers | PIA does **not** support port forwarding on US → no inbound peer port → degraded Soulseek reachability | A non-US PF region (CA Toronto, Switzerland, etc.) in `SERVER_REGIONS` |
| Hardcoded/static slskd listen port | PIA's forwarded port rotates on reconnect; a stale port breaks incoming connections | slskd native VPN integration (`SLSKD_VPN_PORT_FORWARDING=true`) auto-syncs it |
| gluetun control server with NO auth | Recent gluetun makes all routes private → slskd 401 (issue #1660) | `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` apikey, matched in `SLSKD_VPN_GLUETUN_API_KEY` |
| Building a custom port-sync loop on slskd 0.24.4+ | Redundant — native integration already does it | slskd `integration.vpn.gluetun` |
| Postgres for this app | Operational overhead, no concurrency benefit for one writer | SQLite + WAL |
| Per-service webhook code | Brittle, not swappable without code changes | Apprise |
| Treating Readarr as actively maintained | Dev halted 2024; no fixes coming | Pin it, keep `httpx` fallbacks, isolate the books module for future retarget |
| `:latest` for slskd in production compose | Reproducibility / surprise breakage on rebuild | Pin `slskd/slskd:0.25.1`; bump deliberately |

## Stack Patterns by Variant

**If slskd's pinned image web port differs from 5030:**
- Adjust the gluetun `ports:` mapping and `SLSKD_URL`; the published port lives on the gluetun service.

**If slskd-api (0.1.5) lacks a needed endpoint:**
- Call the slskd REST API directly with `httpx`. Don't block on the wrapper.

**If gluetun returns 401 to slskd:**
- Control-server-auth requirement — set `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` apikey on gluetun and match it in `SLSKD_VPN_GLUETUN_API_KEY`.

**If pinning slskd < 0.24.4 for any reason:**
- No native VPN integration — fold a port-sync loop into Curator (read gluetun `/v1/openvpn/portforwarded`, push to slskd listen port). Prefer upgrading instead.

**If the Readarr metadata server degrades later:**
- Keep music fully operational; swap the books module's backend behind Curator's internal interface.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| slskd 0.25.1 | slskd-api 0.1.5 (Python >=3.11) | Verify the wrapper covers transfers/searches/shares you need; raw HTTP for gaps. Native VPN integration in 0.24.4+. 0.25.0 changed licensing + PUID/PGID Docker behavior — read release notes before bumping. |
| slskd 0.24.4+ | gluetun v3.x control server | Native `integration.vpn.gluetun`; gluetun needs control-server auth (apikey role) or slskd gets 401. |
| pyarr 6.x | Lidarr v1 + Readarr v1 | Same library, both apps; actively updated through 2026. Pin a specific 6.x. |
| FastAPI 0.115+ | Uvicorn 0.34+, pydantic v2 / SQLModel 0.0.22+ | Standard modern FastAPI stack; all pydantic v2. |
| gluetun v3.x | PIA + `VPN_PORT_FORWARDING=on` | Non-US `SERVER_REGIONS` required for PF; control server on :8000. |
| docker/build-push-action v6 | setup-buildx v3, login v3, metadata v5 | Current pinned CI majors. |

## Sources

- https://github.com/slskd/slskd + /releases — latest stable **0.25.1** (Apr 2026); image `slskd/slskd`; API-key auth model (HIGH)
- https://github.com/slskd/slskd/blob/master/docs/config.md — API key format (`role=...;cidr=...;<16-255 char>`, `SLSKD_API_KEY`), `soulseek.listen_port`, and the **`integration.vpn.gluetun` block** with `SLSKD_VPN_*` env vars (HIGH)
- https://github.com/slskd/slskd/blob/master/docs/vpn.md + slskd issue #1660 — native gluetun integration since **0.24.4**; wait-for-ready + auto port-forward; **control-server auth required (else 401)**; exact `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` apikey syntax (HIGH)
- https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md — control-server auth roles, default-role apikey, all routes private by default (HIGH)
- https://github.com/mrusse/soularr — Soularr is **Lidarr-only**, uses `pyarr` + `slskd-api`, cron/Docker, filename-ratio matching ≥0.8 (HIGH)
- https://github.com/qdm12/gluetun-wiki — PIA: `VPN_PORT_FORWARDING=on`, `VPN_PORT_FORWARDING_PROVIDER`, `SERVER_REGIONS`, control server `/v1/openvpn/portforwarded`; **US not supported for PF**, non-US works (HIGH)
- https://www.privateinternetaccess.com/blog/the-beginners-guide-to-vpn-port-forwarding/ + top10vpn PIA port-forwarding guide — PF on essentially all non-US PIA servers; port random + rotates (HIGH)
- https://lidarr.audio/docs/api/ , https://readarr.com/docs/api/ , https://wiki.servarr.com — v1 endpoints `wanted/missing`, `wanted/cutoff`, `qualityprofile`, `command`, `manualimport`; `X-Api-Key`; Readarr retired (HIGH)
- https://gethomepage.dev/widgets/services/customapi/ + /widgets/services/slskd/ — `customapi` YAML/mappings/formats; native slskd widget (HIGH)
- https://pypi.org/project/slskd-api/ + https://github.com/bigoulours/slskd-python-api — slskd-api **0.1.5** (Apr 2026), Python >=3.11 (HIGH)
- https://pypi.org/project/pyarr/ + https://github.com/totaldebug/pyarr — pyarr **6.x** (latest), supports Lidarr + Readarr (HIGH)
- https://pypi.org/project/apprise/ — Apprise 1.9.x; Discord/ntfy/Plex targets (HIGH)
- https://pypi.org/project/httpx/ — httpx 0.28.1 (HIGH)
- github.com/docker/{build-push-action v6, setup-buildx-action v3, login-action v3, metadata-action v5, setup-qemu-action v3} (HIGH)

---
*Stack research for: autonomous fallback-only Soulseek media gap-filler (Lidarr+Readarr) on Synology*
*Researched: 2026-05-29*
