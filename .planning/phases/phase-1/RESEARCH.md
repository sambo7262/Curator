# Phase 1: VPN-Routed Networking Foundation - Research

**Researched:** 2026-05-30
**Domain:** Container networking (VPN kill-switch + shared netns), gluetun+PIA port forwarding, slskd native gluetun integration, Synology Container Manager, GitHub Actions CI → Docker Hub
**Confidence:** HIGH on topology, slskd↔gluetun integration, control-server auth, and CI; MEDIUM on the exact live PIA PF region list and the existing NAS `/data`/PUID layout (both require on-NAS executor verification — flagged below).

> **Verification status (this session):** The hard parts were live-verified against current
> sources: gluetun control-server auth (the v3.40.0 breaking change), slskd's native
> `SLSKD_VPN_*` integration (official `docs/vpn.md`), PIA port-forwarding behavior, current
> image tags on Docker Hub, and the latest `docker/*` action versions. Two items genuinely
> cannot be pinned without account/NAS access and are given explicit executor steps: the live
> PIA PF region list, and the existing *arr `/data` mount + PUID/PGID. Sibling research
> (STACK.md, ARCHITECTURE.md, PITFALLS.md) was itself live-verified on 2026-05-29 and agrees
> with this session's findings.

---

<user_constraints>
## User Constraints (from CLAUDE.md + PROJECT.md + ROADMAP.md)

> No phase-level CONTEXT.md exists yet (this is standalone research before `/gsd:discuss-phase`).
> Constraints below are extracted verbatim from project-level governance docs and are binding.

### Locked Decisions
- **Platform:** Synology DS423+ (Intel J4125, `linux/amd64` only), Docker via Container Manager.
- **Networking:** Curator runs on the `synobridge` Docker network. slskd runs `network_mode: service:gluetun` (cannot also join synobridge) — Curator reaches slskd via gluetun's published port, and reaches Lidarr/Plex by container name on synobridge.
- **Privacy:** All Soulseek traffic must route through gluetun + PIA with port forwarding on a **non-US region** (US has no PF). Kill-switch on. The forwarded port must be synced into slskd's listen port automatically.
- **Persistence:** `/volume1` bind-mounts with correct PUID/PGID. State store is SQLite.
- **Deploy:** single docker-compose YAML pulling a Docker Hub image built/pushed by GitHub Actions; iterate by teardown/rebuild.
- **Import paths must be identical across containers** (single `/data` mount, atomic hardlinks — the #1 import-failure cause).
- **Source engine:** build slskd-direct, NOT Soularr (decided in STACK.md; affects only what Curator becomes later — Phase 1 ships a stub).
- **Secrets:** never baked into the image; runtime env/`.env` only.

### Claude's Discretion
- Curator Phase-1 skeleton shape (FastAPI health stub recommended below).
- Exact `/volume1` subfolder names (TRaSH-style layout recommended; must match existing *arr).
- CI tagging strategy and exact image tag to pin (verified candidates given below).
- WireGuard vs OpenVPN for the PIA transport (recommendation + tradeoff below).

### Deferred Ideas (OUT OF SCOPE for Phase 1)
- All Curator application logic: gap detection, *arr adapter, matching, quality gating, slskd search/download, staging/import/purge, sharing, notifications, Homepage widget. Those are Phases 2-6. Phase 1 only proves the substrate deploys and the stack comes online.
- Registering slskd as an *arr download client (Phase 4 decision).
- Books/Readarr branch.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INFRA-01 | slskd via gluetun+PIA, non-US PF, kill-switch | gluetun PIA config + `network_mode: service:gluetun`; verified env vars (Std Stack, Pattern 1) |
| INFRA-02 | Forwarded port auto-synced to slskd listen port | slskd native `SLSKD_VPN_*` integration — **VERIFIED** via `docs/vpn.md`; no sidecar needed |
| INFRA-03 | gluetun on synobridge, subnet allowed outbound (*arr reachable); Curator reaches slskd via published port | `FIREWALL_OUTBOUND_SUBNETS` + gluetun `ports:` publish (Pattern 2/3) |
| INFRA-04 | Single docker-compose pulling Docker Hub image | Annotated compose skeleton below |
| INFRA-05 | GitHub Actions builds+pushes linux/amd64 to Docker Hub | Workflow below (single-platform, no QEMU) — verified action versions |
| INFRA-06 | Single identical `/data` mount, hardlink-capable, correct PUID/PGID | `/data` layout + on-NAS verification step |
</phase_requirements>

---

## Summary

Phase 1 stands up a three-container substrate on a Synology DS423+: **gluetun** (PIA tunnel,
kill-switch on, port forwarding), **slskd** (Soulseek daemon sharing gluetun's network
namespace), and a minimal **Curator** FastAPI health stub (plain `synobridge` member). The
defining architectural choice — confirmed across all sibling research and re-verified this
session — is the **gluetun-on-synobridge** pattern: gluetun joins `synobridge` and *publishes*
slskd's web port (5030) onto it, while slskd runs `network_mode: service:gluetun` so every
slskd packet exits through the PIA tunnel with a fail-closed kill-switch. Curator and the
existing *arr stack reach slskd at `http://gluetun:5030`; Curator reaches the *arr APIs
directly by container name over `synobridge`.

The single biggest correctness risk — and the one most likely to silently fail — is
**gluetun's control-server authentication**. As of **gluetun v3.40.0** *all* control-server
routes are private by default; there are no public routes. slskd polls that control server to
learn PIA's forwarded port, so without auth configured, slskd gets `401 Unauthorized` (slskd
issue #1660) and the forwarded-port sync silently never happens. The verified fix is one env
var on gluetun (`HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` set to an apikey JSON) and the matching
key on slskd (`SLSKD_VPN_GLUETUN_API_KEY`). With that in place, **slskd's native gluetun
integration (the `integration.vpn` block / `SLSKD_VPN_*` env vars) handles port forwarding
entirely — no sidecar, no custom port-sync code** (INFRA-02 is satisfied by configuration, not
engineering). This was confirmed verbatim from slskd's official `docs/vpn.md`.

Two more wiring details: (1) `FIREWALL_OUTBOUND_SUBNETS` must include the synobridge subnet
CIDR so the kill-switch doesn't sever LAN traffic, and that CIDR must **not** overlap the VPN
tunnel CIDR; (2) PIA port forwarding works on essentially all **non-US** regions and **never**
on US — the forwarded port is dynamic and persists 60 days only if `/gluetun` is bind-mounted.
CI is a single-platform (`linux/amd64`, no QEMU) build with the current `docker/*` actions,
pushing to Docker Hub.

**Primary recommendation:** Build in this order — (Wave 0) on-NAS recon + live PIA PF region
confirmation; (A) gluetun+PIA alone, confirm non-US public IP + non-zero forwarded port via the
authed control server; (B) attach slskd via shared netns, confirm kill-switch fail-closed +
auto port-sync surviving a restart; (C) Curator stub + CI → Docker Hub; (D) full smoke-test
go/no-go. Pin every image by tag (and ideally digest); never deploy `:latest`.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| VPN tunnel + kill-switch + PF acquisition | gluetun container | — | Single chokepoint; owns the netns slskd shares |
| Soulseek protocol egress | slskd (in gluetun netns) | — | All P2P traffic must traverse the VPN |
| Forwarded-port → listen-port sync | slskd native gluetun client | gluetun control server (source of truth) | slskd polls the control server and self-applies the port at runtime |
| Reaching *arr APIs | Curator (synobridge member) | synobridge network | Curator is NOT in the VPN netns; talks LAN directly by container name |
| Exposing slskd to LAN/Curator | gluetun `ports:` publish on synobridge | — | slskd has no own net stack; ports live in gluetun's namespace |
| Persistent media/config storage | `/data` bind mount (host) | host filesystem | Single hardlink-capable mount, identical path everywhere |
| Image build + distribution | GitHub Actions → Docker Hub | — | Reproducible `linux/amd64` artifact |
| Deploy orchestration | docker-compose (Container Manager) | — | Single declarative file |

---

## Standard Stack

### Core
| Image | Version (verified 2026-05-30) | Purpose | Why Standard |
|-------|------------------------------|---------|--------------|
| `qmcgaw/gluetun` | **`v3.41.1`** (latest stable; `v3` floating tag also points here) [VERIFIED: Docker Hub tag API + gluetun releases] | VPN client: kill-switch, PIA PF, control server | De-facto self-hosted VPN sidecar; native PIA PF; control server slskd consumes |
| `slskd/slskd` | **`0.23.1`** plain-semver tag exists [VERIFIED: Docker Hub `/tags/0.23.1`]; **`latest`** tracks the 0.25.x line. STACK.md recommends 0.25.1. **See "slskd tag scheme" note — confirm a pinnable stable tag in Wave 0.** | Soulseek daemon: web UI + REST API + native gluetun VPN integration | Standard modern Soulseek server; native gluetun PF since v0.24.4 |
| `python:3.12-slim` | `3.12-slim` [VERIFIED: Docker Hub tag exists] | Curator health-stub base | Stable, small, hardlink-friendly; matches STACK.md (Python 3.12) |
| Synology Container Manager | DSM 7.2+ | Docker runtime | Native on DS423+ |

> **slskd tag scheme note [MEDIUM]:** slskd's Docker Hub publishes mostly build-stamped tags
> like `0.25.1.65534-<sha>` plus moving `latest`/`canary`. A clean `0.23.1` semver tag is
> confirmed to exist; a clean `0.25.1` was not confirmed in this session. **Wave 0 action:**
> `curl -s https://hub.docker.com/v2/repositories/slskd/slskd/tags?page_size=100 | jq -r '.results[].name'`,
> pick a stable, non-canary tag ≥ 0.24.4 (native gluetun PF requires ≥0.24.4), and pin it by
> tag **and digest**. Do not use `latest` in the committed compose.

> **gluetun pin choice [HIGH]:** Pin `v3.41.1` (latest stable, released Feb 2025; control-server
> auth landed in v3.40.0 so any v3.40.x+ works). The `pr-*`/`test`/`latest` tags on Docker Hub
> are dev builds — avoid them for reproducibility (INFRA-04).

### Supporting (CI — verified latest majors this session)
| Action | Version [VERIFIED: GitHub releases API, 2026-05-30] | Purpose |
|--------|-----------------------------------------------------|---------|
| `actions/checkout` | `@v4` | Checkout |
| `docker/setup-buildx-action` | `@v3` | Buildx builder |
| `docker/login-action` | `@v3` | Docker Hub auth |
| `docker/metadata-action` | `@v5` | Tag/label generation |
| `docker/build-push-action` | `@v6` | Build + push |
| FastAPI + `uvicorn[standard]` | latest stable (pin in `requirements.txt`) | Curator health endpoint |

> `docker/setup-qemu-action` is **omitted on purpose** — DS423+ is amd64 and `ubuntu-latest`
> runners are amd64, so a `linux/amd64`-only build needs no emulation (faster CI). [VERIFIED:
> ARCHITECTURE.md + STACK.md agree; QEMU only needed to add arm64.]

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| slskd native gluetun PF | external sidecar (`glueforward`, `tieum/slskd-port-forward-gluetun-server`) or gluetun `VPN_PORT_FORWARDING_UP_COMMAND` + `sed` | Only if pinned slskd < 0.24.4. With ≥0.24.4, native is strictly simpler — keep sidecars as documented escape hatches, not the default. [VERIFIED: both tools exist] |
| `network_mode: service:gluetun` | gluetun as routed gateway with explicit iptables | Shared-netns is the documented, simplest pattern for one VPN'd service. |
| WireGuard (PIA) | OpenVPN (PIA) | WireGuard is faster; OpenVPN is the historically more-documented PIA+PF path. **PIA PF works on both via gluetun.** Recommend WireGuard; OpenVPN as fallback. [MEDIUM — confirm WG PF on the chosen build in Wave 0] |
| Pin `0.23.1` | a 0.25.x stable tag | 0.23.1 is the confirmed clean semver, but predates some 0.25.x fixes/native-PF maturity. Prefer a ≥0.24.4 stable tag if one is pinnable; else 0.23.1 lacks native PF and forces a sidecar. **Resolve in Wave 0.** |

**Installation (deploy on NAS):** `docker compose pull && docker compose up -d` (Container
Manager imports the compose file).

---

## Package Legitimacy Audit

> slopcheck was not installed in this environment; registry existence was verified directly via
> the Docker Hub tag API and GitHub releases API. All four images are first-party, well-known,
> high-trust projects. Per protocol, because slopcheck did not run, treat version pins as needing
> a Wave 0 human-verify confirmation of the exact tag+digest before the compose is committed.

| Image | Registry | Verified | Source Repo | slopcheck | Disposition |
|-------|----------|----------|-------------|-----------|-------------|
| `qmcgaw/gluetun:v3.41.1` | Docker Hub | HTTP 200 on tag API | github.com/qdm12/gluetun | not run | Approved — pin digest in Wave 0 |
| `slskd/slskd` (tag TBD ≥0.24.4) | Docker Hub | tag list fetched; `0.23.1` confirmed | github.com/slskd/slskd | not run | Approved — confirm exact stable tag + digest in Wave 0 |
| `python:3.12-slim` | Docker Hub (official lib) | HTTP 200 | github.com/docker-library/python | not run | Approved — official image |
| `docker/*-action`, `actions/checkout` | GitHub Marketplace (official Docker / GitHub orgs) | releases API: buildx v3, login v3, metadata v5, build-push v6, checkout v4 | github.com/docker/*, github.com/actions/checkout | n/a | Approved — official orgs |

**Removed (SLOP):** none. **Flagged (SUS):** none. **Wave 0 gate:** confirm slskd stable tag + pin all images by digest.

---

## Architecture Patterns

### System Architecture Diagram

```
                 ┌──────────────────────── synobridge (external Docker bridge) ──────────────────────┐
                 │                                                                                    │
  Home LAN ──►   │   ┌────────┐  ┌────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────┐         │
  (browsers,     │   │ Radarr │  │ Sonarr │  │  Lidarr  │  │ Readarr  │  │Prowlarr│  │ SAB  │  (Plex   │
   *arr UIs,     │   └───▲────┘  └───▲────┘  └────▲─────┘  └──────────┘  └────────┘  └──────┘  native) │
   Homepage)     │       │           │            │  *arr APIs (LAN, NOT via VPN, by container name)   │
                 │   ┌───┴───────────┴────────────┴────┐                                              │
                 │   │  curator (FastAPI /healthz stub) │── http://gluetun:5030 ──┐                    │
                 │   │  plain synobridge member         │                         │                    │
                 │   │  reads /data (Phase 1: RO proof) │◄── reads /data          ▼                    │
                 │   └───────────────┬──────────────────┘            ┌─────────────────────────┐       │
                 │                   │                               │ gluetun (PIA tunnel)     │       │
                 └───────────────────┼───────────────────────────────│  • kill-switch ON        │───────┘
                                     │ /data bind mount               │  • FIREWALL_OUTBOUND_    │
                                     ▼                                │    SUBNETS=synobridge    │
                            ┌──────────────────┐                      │  • control server :8000  │
                            │ /volume1/data    │                      │    (authed, apikey)      │
                            │ (single FS,      │                      │  • ports: 5030 published │
                            │  hardlink-cap)   │◄── reads /data ──┐   └────────────┬─────────────┘
                            └──────────────────┘                  │   network_mode: service:gluetun
                                                       ┌──────────┴───────────────┐
                                                       │ slskd (no own net stack) │
                                                       │  • polls gluetun :8000    │
                                                       │    → self-sets Soulseek   │
                                                       │      listen port (PF)     │
                                                       │  • Soulseek egress ───────┼──► PIA VPN ──► Internet
                                                       └───────────────────────────┘   (ONLY path; fail-closed)
```

Data-flow notes:
- Curator → slskd: `http://gluetun:5030` (gluetun publishes slskd's web/API port on synobridge). **Never `http://slskd:5030`** — slskd has no synobridge hostname.
- Curator → *arr: direct over synobridge by container name; never through the VPN.
- slskd → Soulseek: forced through the PIA tunnel. If gluetun drops, slskd has zero network path (kill-switch fail-closed).
- gluetun control server (`:8000`) → slskd: slskd polls it (authenticated) to learn the PIA forwarded port and self-applies it as its Soulseek listen port at runtime (INFRA-02).

### Recommended Project Structure
```
Curator/
├── docker-compose.yml          # single stack file (INFRA-04)
├── .env                        # secrets — gitignored
├── .env.example                # committed template
├── .gitignore                  # MUST include .env
├── Dockerfile                  # Curator image, built by CI
├── .github/workflows/
│   └── docker-publish.yml       # INFRA-05 CI → Docker Hub
├── app/
│   ├── main.py                 # FastAPI /healthz + /readyz stub
│   ├── requirements.txt
│   └── tests/test_health.py
└── scripts/
    └── smoke-test.sh            # the NAS go/no-go checklist, runnable
```

### Pattern 1: Shared network namespace VPN kill-switch (INFRA-01)
**What:** slskd has no network stack; it joins gluetun's via `network_mode: service:gluetun`.
gluetun's firewall is default-deny except the tunnel + allowed subnets, so if the tunnel drops
slskd's only egress path vanishes — fail-closed, no IP leak.
**Example:**
```yaml
slskd:
  network_mode: "service:gluetun"   # NO `networks:` and NO `ports:` here — both live on gluetun
  depends_on:
    gluetun:
      condition: service_healthy    # gluetun ships a healthcheck [VERIFIED: ARCHITECTURE.md]
```

### Pattern 2: gluetun-on-synobridge + port publishing (INFRA-03)
**What:** gluetun joins `synobridge` (so Curator/*arr can resolve `gluetun`) AND publishes
slskd's ports (ports for a netns tenant must be declared on the namespace owner).
```yaml
gluetun:
  networks: [synobridge]
  ports:
    - "5030:5030"      # slskd web UI / API, published via gluetun
```

### Pattern 3: Kill-switch LAN allowance (`FIREWALL_OUTBOUND_SUBNETS`) (INFRA-03)
**What:** The kill-switch blocks all non-tunnel egress by default, which would also drop
gluetun↔synobridge LAN traffic. `FIREWALL_OUTBOUND_SUBNETS` whitelists the synobridge/Docker
subnet so LAN connectivity survives. [VERIFIED: gluetun firewall wiki + PITFALLS #10]
**Caveat [VERIFIED: gluetun issue #2771]:** the whitelisted subnet must **NOT overlap the VPN
tunnel CIDR**, or port forwarding/routing breaks.
**Determine the synobridge CIDR (run on NAS):**
```bash
docker network inspect synobridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```
Set `FIREWALL_OUTBOUND_SUBNETS` to that value (e.g. `172.20.0.0/16`).

### Anti-Patterns to Avoid (all [VERIFIED] against PITFALLS.md/ARCHITECTURE.md)
- Putting slskd directly on synobridge (not in gluetun's netns) → IP leak; defeats INFRA-01.
- Declaring `ports:` or `networks:` on the `service:gluetun` tenant → compose error.
- Putting **Curator** in gluetun's netns → loses synobridge DNS to *arr; a VPN drop blinds Curator. Only slskd goes in the tunnel.
- Choosing a US PIA region → no port forwarding; transfers/sharing cripple silently.
- `FIREWALL_OUTBOUND_SUBNETS` overlapping the VPN CIDR → broken routing.
- Floating `:latest` tags → non-reproducible (INFRA-04) and breaks on upstream auth changes.
- Hardcoding slskd's listen port → PIA's PF port rotates; native sync handles it.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| VPN kill-switch / fail-closed firewall | Custom iptables | gluetun's built-in firewall | DNS/IPv6 leak + reconnect races handled |
| PIA port-forward acquisition | Custom PIA API client | gluetun `VPN_PORT_FORWARDING=on` | PIA PF handshake/renewal is version-specific |
| Forwarded-port → slskd listen-port sync | Polling sidecar / cron `sed` | slskd **native** `SLSKD_VPN_*` integration | [VERIFIED] one fewer container; maintained upstream; updates at runtime without restart |
| Control-server API key | Hand-written token | `docker run --rm qmcgaw/gluetun genkey` | [VERIFIED: control-server wiki] produces a 22-char base58 key |
| Multi-arch CI | QEMU matrix | single `--platform linux/amd64` | DS423+ is amd64-only |
| Container user mapping | chown gymnastics | `PUID`/`PGID` env | slskd + gluetun support it; matches *arr stack |

**Key insight:** Every hard part of this phase (kill-switch, PF, port-sync, control auth) has a
verified configuration-driven solution. The only real engineering is the Curator stub + CI; the
rest is correct wiring + verification.

---

## Common Pitfalls

### Pitfall 1: gluetun v3.40+ control-server auth — slskd gets 401 (THE #1 risk)
**What goes wrong:** slskd's poll for the forwarded port returns `401 Unauthorized`; port sync
silently never happens; slskd never gets a listen port. [VERIFIED: slskd issue #1660]
**Why:** As of **gluetun v3.40.0**, ALL control-server routes are private by default — no public
routes remain. [VERIFIED: gluetun control-server wiki + v3.40.0 release notes]
**How to avoid:** Generate a key (`docker run --rm qmcgaw/gluetun genkey`), set on gluetun
`HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"apikey","apikey":"<KEY>"}'`, and set the same
key on slskd `SLSKD_VPN_GLUETUN_API_KEY: <KEY>`. Optionally also set
`GLUETUN_HTTP_CONTROL_SERVER_ENABLE: on` (slskd docs show it explicitly). [VERIFIED: slskd
`docs/vpn.md`]
**Warning sign:** gluetun logs 401 on the portforward route; slskd never reports a listen port.

### Pitfall 2: PIA US regions have NO port forwarding
**What goes wrong:** No forwarded port; Soulseek peers can't connect; sharing fails → leecher
block. [VERIFIED: PIA help docs + gluetun PIA wiki + PITFALLS #6]
**Why:** PIA disables PF on US servers. Essentially **all non-US** servers support it.
**How to avoid:** Pick a PF-capable non-US region near home (Canada is closest/lowest-latency).
**Confirm live in Wave 0** (the region list changes): see Verification Protocol.
**Warning sign:** gluetun logs "port forwarding not supported for this region" or forwarded
port `0`.

### Pitfall 3: Forwarded port is dynamic and must persist
**What goes wrong:** Port re-rolls on reconnect/reboot and slskd keeps a stale port. [VERIFIED:
PITFALLS #7]
**How to avoid:** (a) slskd native sync re-applies it at runtime; (b) **bind-mount `/gluetun`**
so PIA keeps the same forwarded port for 60 days. [VERIFIED: gluetun PIA wiki — "keep the same
port for 60 days as long as you bind mount /gluetun"]
**Warning sign:** connectivity works fresh, dies after a NAS reboot.

### Pitfall 4: Path identity / hardlinks (INFRA-06)
**What goes wrong:** If `/data` is mounted at different paths or as separate volumes across
slskd vs *arr, hardlinks fail (`EXDEV`) → imports become slow cross-FS copies. [VERIFIED:
PITFALLS #5, the "near-universal" import failure cause]
**How to avoid:** Mount the **same host path** to the **same container path** `/data`
everywhere, single mount, one filesystem.
**Warning sign:** `ln` across `/data` subdirs returns "Invalid cross-device link".

### Pitfall 5: `network_mode: service:gluetun` vs synobridge collision
**What goes wrong:** A netns tenant cannot also join synobridge, so it can't resolve `lidarr`
by name. [VERIFIED: PITFALLS #11]
**How to avoid:** This is exactly why **Curator stays a plain synobridge member** and only slskd
is in the netns. Curator talks to *arr natively; slskd doesn't call *arr at all (in Phase 1 it
calls nothing but Soulseek).
**Warning sign:** "could not resolve host lidarr" from inside the netns.

### Pitfall 6: PUID/PGID ownership on `/volume1`
**What goes wrong:** Files written by one container can't be read/moved by the *arr → permission
errors. [VERIFIED: PITFALLS #12]
**How to avoid:** Consistent PUID/PGID across all containers matching the `/volume1` media owner
(Synology often uid 1026 / gid 100); umask 002. Pre-create bind dirs with correct ownership.
**Warning sign:** "permission denied" / files owned by root.

### Pitfall 7: Secrets in git / image
**What goes wrong:** PIA creds, *arr keys, Docker Hub token leak. [VERIFIED: PITFALLS #18]
**How to avoid:** All secrets in `.env` (gitignored); commit `.env.example` only; CI uses
GitHub repo secrets; never bake into the image. Use a Docker Hub **access token**, not the
account password.

### Pitfall 8: Synology Container Manager network quirks
**What goes wrong:** Network membership silently drops on redeploy; name resolution flakey after
network changes. [VERIFIED: PITFALLS #11]
**How to avoid:** Create the network and deploy via compose (not the GUI editor); for networking
changes do a full `down` → `up -d`, not just `restart`.

---

## Code Examples

> Env-var names, the control-server auth JSON, and slskd integration keys below are **VERIFIED**
> this session against slskd `docs/vpn.md`, the gluetun control-server wiki, and the gluetun PIA
> wiki. Image tags are verified-existing; confirm the exact slskd stable tag + digests in Wave 0.

### Annotated `docker-compose.yml`
```yaml
# Curator Phase-1 stack. Pin digests in Wave 0; confirm slskd stable tag >= 0.24.4.
services:
  # ─────────────────────────────── VPN tunnel (PIA) ───────────────────────────────
  gluetun:
    image: qmcgaw/gluetun:v3.41.1        # [VERIFIED tag exists]; pin @sha256 in Wave 0
    container_name: gluetun
    cap_add: [NET_ADMIN]
    devices:
      - /dev/net/tun:/dev/net/tun
    networks: [synobridge]               # gluetun joins synobridge (INFRA-03)
    ports:
      - "5030:5030"                      # publish slskd's web/API onto synobridge (Pattern 2)
    environment:
      # --- PIA / VPN (INFRA-01) --- [VERIFIED env names: gluetun PIA wiki] ---
      VPN_SERVICE_PROVIDER: "private internet access"
      VPN_TYPE: "wireguard"              # recommend WireGuard; OpenVPN is the fallback path
      OPENVPN_USER: "${PIA_USER}"        # PIA token exchange uses these for BOTH WG and OVPN
      OPENVPN_PASSWORD: "${PIA_PASSWORD}"
      SERVER_REGIONS: "${PIA_PF_REGION}" # non-US PF region, e.g. "CA Toronto" — verify live (Wave 0)
      # --- Port forwarding (INFRA-02) --- [VERIFIED: gluetun PIA wiki] ---
      VPN_PORT_FORWARDING: "on"
      VPN_PORT_FORWARDING_PROVIDER: "private internet access"
      # --- Kill-switch LAN allowance (INFRA-03) --- [VERIFIED] ---
      FIREWALL_OUTBOUND_SUBNETS: "${SYNOBRIDGE_CIDR}"  # docker network inspect; must NOT overlap VPN CIDR
      # --- Control-server auth (v3.40+ REQUIRED) (INFRA-02) --- [VERIFIED: slskd docs/vpn.md] ---
      GLUETUN_HTTP_CONTROL_SERVER_ENABLE: "on"
      HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE: '{"auth":"apikey","apikey":"${GLUETUN_API_KEY}"}'
      # --- user mapping / tz (INFRA-06) ---
      PUID: "${PUID}"
      PGID: "${PGID}"
      TZ: "${TZ}"
    volumes:
      - /volume1/docker/gluetun:/gluetun  # persist forwarded port (60-day) + auth (Pitfall 3)
    restart: unless-stopped
    # gluetun ships a built-in healthcheck used by slskd's depends_on

  # ─────────────────────────────── Soulseek daemon ───────────────────────────────
  slskd:
    image: slskd/slskd:<STABLE_TAG>      # Wave 0: pick stable tag >= 0.24.4, pin @sha256
    container_name: slskd
    network_mode: "service:gluetun"      # share VPN netns (Pattern 1) — NO ports/networks here
    depends_on:
      gluetun:
        condition: service_healthy
    environment:
      # --- native gluetun PF sync (INFRA-02) --- [VERIFIED: slskd docs/vpn.md] ---
      SLSKD_VPN: "true"
      SLSKD_VPN_PORT_FORWARDING: "true"
      SLSKD_VPN_GLUETUN_URL: "http://localhost:8000"   # control server is in the SAME netns → localhost
      SLSKD_VPN_GLUETUN_API_KEY: "${GLUETUN_API_KEY}"  # MUST equal gluetun's apikey (else 401)
      # --- slskd own API key (for later Curator use; harmless in Phase 1) ---
      SLSKD_API_KEY: "${SLSKD_API_KEY}"
      # Do NOT hardcode SLSKD_SLSK_LISTEN_PORT — native PF sets it dynamically (Pitfall 3)
      PUID: "${PUID}"
      PGID: "${PGID}"
      TZ: "${TZ}"
    volumes:
      - /volume1/docker/slskd:/app        # slskd config/state
      - /volume1/data:/data               # SAME identical path everywhere (INFRA-06)
    restart: unless-stopped

  # ─────────────────────────────── Curator app stub ───────────────────────────────
  curator:
    image: ${DOCKERHUB_USER}/curator:latest   # built by CI (INFRA-05); pin a sha tag in prod
    container_name: curator
    networks: [synobridge]               # plain synobridge member (reaches slskd via gluetun:5030)
    depends_on: [gluetun]
    environment:
      SLSKD_URL: "http://gluetun:5030"     # slskd via gluetun's published port — NEVER http://slskd
      SLSKD_API_KEY: "${SLSKD_API_KEY}"
      GLUETUN_CONTROL_URL: "http://gluetun:8000"   # health display only (Phase 1: optional)
      GLUETUN_API_KEY: "${GLUETUN_API_KEY}"
      LIDARR_URL: "http://lidarr:8686"
      LIDARR_API_KEY: "${LIDARR_API_KEY}"
      PUID: "${PUID}"
      PGID: "${PGID}"
      TZ: "${TZ}"
    ports:
      - "8674:8674"                        # Curator status/health endpoint (matches ARCHITECTURE.md)
    volumes:
      - /volume1/docker/curator/config:/config
      - /volume1/data:/data:ro             # Phase-1 stub proves it can read /data (read-only)
    restart: unless-stopped

networks:
  synobridge:
    external: true                         # already created by the existing media stack
```

### `.env.example` (commit this; real `.env` is gitignored)
```dotenv
# ---- PIA / VPN ----
PIA_USER=
PIA_PASSWORD=
PIA_PF_REGION=CA Toronto          # PF-capable non-US region — verify live (Wave 0)
SYNOBRIDGE_CIDR=172.20.0.0/16     # from: docker network inspect synobridge

# ---- gluetun control-server auth ----
GLUETUN_API_KEY=                  # generate: docker run --rm qmcgaw/gluetun genkey

# ---- slskd ----
SLSKD_API_KEY=                    # slskd's own API key (used by Curator from Phase 2 on)

# ---- *arr API key (Curator → LAN reachability proof) ----
LIDARR_API_KEY=

# ---- Docker Hub (image ref) ----
DOCKERHUB_USER=

# ---- user / tz ----
PUID=1026          # verify on NAS: id <media-user>
PGID=100           # verify on NAS: id <media-user>
TZ=America/Toronto
```

### Control-server auth — two equivalent forms [VERIFIED: gluetun control-server wiki]
The compose above uses the **default-role apikey** env var (simplest, enough for slskd). If you
prefer per-route scoping, bind-mount `/volume1/docker/gluetun/auth/config.toml`:
```toml
# /volume1/docker/gluetun/auth/config.toml  (path overridable via HTTP_CONTROL_SERVER_AUTH_CONFIG_FILEPATH)
[[roles]]
name = "slskd"
routes = ["GET /v1/portforward"]    # current route; /v1/openvpn/portforwarded is deprecated → /v1/portforward
auth = "apikey"
apikey = "REPLACE_WITH_GLUETUN_API_KEY"
```

### Curator FastAPI health stub — `app/main.py`
```python
# Phase-1 proof: image builds, pulls from Docker Hub, runs on synobridge, reads /data.
import os
from pathlib import Path
from fastapi import FastAPI

app = FastAPI(title="Curator", version="0.1.0-phase1")
DATA = Path("/data")

@app.get("/healthz")
def healthz():
    return {"status": "ok", "phase": 1}

@app.get("/readyz")
def readyz():
    return {
        "data_mount_present": DATA.is_dir(),
        "data_readable": os.access(DATA, os.R_OK),
        "slskd_url": os.getenv("SLSKD_URL"),
    }
```

### Curator `Dockerfile`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
EXPOSE 8674
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8674"]
```
`app/requirements.txt`: `fastapi` + `uvicorn[standard]` (pin exact versions in Wave 0).

### GitHub Actions — `.github/workflows/docker-publish.yml` (INFRA-05)
```yaml
# Single-platform linux/amd64, no QEMU. Action majors VERIFIED 2026-05-30.
name: docker-publish
on:
  push:
    branches: [main]
    tags: ["v*"]
permissions:
  contents: read
jobs:
  build-push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}    # Docker Hub ACCESS TOKEN, not password
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ secrets.DOCKERHUB_USERNAME }}/curator
          tags: |
            type=ref,event=branch
            type=semver,pattern={{version}}
            type=sha,format=short
            type=raw,value=latest,enable={{is_default_branch}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64                       # single platform → no QEMU
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```
**Required GitHub repo secrets:** `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` (a Docker Hub access token).

---

## Single `/data` Mount + PUID/PGID (INFRA-06)

**Recommended TRaSH-style layout on `/volume1`** (must match the existing *arr convention —
verify first):
```
/volume1/
├── data/                      # SINGLE mount, same path in every container as /data
│   ├── media/
│   │   ├── music/             # Lidarr library root
│   │   └── books/             # Readarr library root (later)
│   └── downloads/
│       └── soulseek/          # slskd writes here (Phase 4)
└── docker/
    ├── gluetun/               # gluetun state + auth/config.toml  (MUST persist — 60-day PF)
    ├── slskd/                 # slskd config/state
    └── curator/config/        # curator config
```
Mount `/volume1/data:/data` **identically** in slskd, curator, AND the existing *arr containers.
Single mount on one filesystem = hardlinks work = atomic imports.

**Cannot inspect the live NAS — executor verification (Wave 0):**
```bash
# 1. Confirm an existing *arr already uses a single /data mount (align if not)
docker inspect lidarr --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'

# 2. Determine PUID/PGID from the /volume1 media owner
id <media-user>          # e.g. uid=1026 gid=100 → PUID=1026 PGID=100

# 3. Confirm downloads + media are on the SAME filesystem (hardlink-capable)
df /volume1/data         # single filesystem row covering the whole tree

# 4. Hardlink smoke test (the real proof of INFRA-06)
mkdir -p /volume1/data/downloads/soulseek /volume1/data/media/music
touch /volume1/data/downloads/soulseek/_hltest && \
  ln /volume1/data/downloads/soulseek/_hltest /volume1/data/media/music/_hltest && \
  echo "HARDLINK OK" || echo "HARDLINK FAILED (cross-device)"; \
  rm -f /volume1/data/downloads/soulseek/_hltest /volume1/data/media/music/_hltest
```

---

## Phase 1 Smoke-Test / Go-No-Go Checklist (run ON the NAS)

> Maps 1:1 to the four ROADMAP success criteria. Control-server route/port [VERIFIED].

```bash
### Criterion 1: VPN egress + kill-switch + non-US PF ###

# 1a. slskd egress goes through PIA (not home IP), no DNS leak
docker exec slskd sh -c 'wget -qO- https://ipinfo.io/ip'   # expect a PIA IP, country != US, != home
curl -s https://ipinfo.io/ip                               # home IP for contrast

# 1b. Forwarded port is non-zero and region is non-US (control server is authed)
docker exec gluetun wget -qO- --header="X-Api-Key: $GLUETUN_API_KEY" http://localhost:8000/v1/portforward
docker exec gluetun wget -qO- --header="X-Api-Key: $GLUETUN_API_KEY" http://localhost:8000/v1/publicip/ip
#    expect {"port": <nonzero>} and a non-US country

# 1c. Kill-switch fail-closed: stop tunnel → slskd must lose ALL egress
docker stop gluetun
docker exec slskd sh -c 'wget -T 5 -qO- https://ipinfo.io/ip' && echo "LEAK!! FAIL" || echo "FAIL-CLOSED OK"
docker start gluetun     # restore

### Criterion 2: PF auto-sync surviving restart ###

# 2a. slskd self-applied the forwarded port as its listen port (check slskd UI/API/logs)
docker logs slskd 2>&1 | grep -i -E 'listen|forwarded|vpn' | tail -5
#    listen port should equal the /v1/portforward value from 1b
# 2b. RESTART TEST (success criterion #2 — "re-syncing after restart, not just first boot")
docker restart gluetun slskd && sleep 60
docker exec gluetun wget -qO- --header="X-Api-Key: $GLUETUN_API_KEY" http://localhost:8000/v1/portforward
docker logs slskd 2>&1 | grep -i listen | tail -2   # listen port re-tracks the (possibly new) PF port

### Criterion 3: reachability + CI + no secrets ###

# 3a. slskd API reachable from synobridge via gluetun's published port
docker run --rm --network synobridge curlimages/curl -s -o /dev/null -w "%{http_code}\n" http://gluetun:5030/
#    expect 200/302/401 (alive)
# 3b. Curator reaches *arr by name over synobridge (NOT via VPN)
docker exec curator sh -c 'curl -s -o /dev/null -w "%{http_code}\n" -H "X-Api-Key: $LIDARR_API_KEY" http://lidarr:8686/api/v1/system/status'  # 200
# 3c. Curator reaches slskd via gluetun
docker exec curator sh -c 'curl -s -o /dev/null -w "%{http_code}\n" http://gluetun:5030/'   # alive
# 3d. CI green: git push → Actions builds+pushes linux/amd64 → image on Docker Hub (check Actions tab + Hub)
# 3e. No secrets baked into the image
docker history --no-trunc ${DOCKERHUB_USER}/curator:latest | grep -iE 'PIA_|API_KEY|PASSWORD' && echo "LEAK FAIL" || echo "NO BAKED SECRETS OK"

### Criterion 4: single compose + /data + PUID/PGID + hardlink ###

# 4a. Single compose brings the stack online
docker compose up -d && docker compose ps      # all services Up/healthy
# 4b. Curator can read /data
docker exec curator sh -c 'curl -s localhost:8674/readyz'   # data_mount_present & data_readable == true
# 4c. Ownership matches PUID/PGID
docker exec slskd sh -c 'ls -ld /data && id'
# 4d. Hardlink works across /data (see /data verification block above)
```

**Go/No-Go:** All must pass. A LEAK on 1c, a US country on 1b, port `0` on 1b, a stale port
after 2b, baked secrets on 3e, or HARDLINK FAILED on 4d is a hard NO-GO.

---

## Sequencing & Gotchas (safest build/deploy order)

**Wave 0 — recon + verification (no deploy):**
1. On NAS: `docker network inspect synobridge` (CIDR), `id <media-user>` (PUID/PGID), inspect an existing *arr `/data` mount, run the hardlink smoke test.
2. Live PIA PF region confirmation (Verification Protocol below) → set `PIA_PF_REGION`.
3. Confirm a pinnable slskd stable tag ≥ 0.24.4; pin gluetun `v3.41.1`; capture amd64 digests.
4. `docker run --rm qmcgaw/gluetun genkey` → `GLUETUN_API_KEY`; create `.env`; add `.env` to `.gitignore`.

**Build:**
5. **gluetun alone:** confirm tunnel up, non-US public IP, non-zero PF port via the authed control server (1a/1b). Guards: Pitfall 1 (auth), 2 (US PF), 3 (`/gluetun` persisted).
6. **Add slskd (shared netns):** confirm kill-switch fail-closed (1c) and PF auto-sync surviving a restart (2a/2b). Guards: Pitfall 1, 3.
7. **Add Curator stub + reachability:** Curator→slskd via `gluetun:5030` (3c), Curator→*arr by name (3b). Guards: Pitfall 5, 8, `FIREWALL_OUTBOUND_SUBNETS`.
8. **Wire CI → Docker Hub:** push, confirm green build + image + no baked secrets (3d/3e). Guard: single-platform amd64.
9. **Full stack from one compose + /data checks** (4a–4d). Guards: Pitfall 4 (paths), 6 (PUID), 7 (secrets).
10. **Run the full smoke-test → Go/No-Go.**

**Top failure modes → catching check:**
| Failure mode | Check |
|--------------|-------|
| Control-server auth 401 (v3.40+) | 1b returns 401; slskd logs 401; no listen port |
| US region → no PF | 1b forwarded port == 0 / non-US assertion fails |
| IP leak / kill-switch open | 1c shows an IP after `docker stop gluetun` |
| PF port not persisting across restart | 2b listen port stale/missing |
| Subnet overlap / *arr unreachable | 3b non-200 from lidarr |
| Cross-device /data → no hardlinks | 4d HARDLINK FAILED |
| Secrets baked into image | 3e finds creds in `docker history` |
| Non-reproducible floating tags | image digest changes between pulls |

---

## Verification Protocol (resolve the two MEDIUM items before locking)

```bash
# A. Live PIA port-forward-capable regions (the list changes; US never has PF).
#    Authoritative source = PIA's own server list (look for "port_forward": true):
curl -sL https://serverlist.piaservers.net/vpninfo/servers/v6 | head -c 1 >/dev/null  # first line is JSON
curl -sL https://serverlist.piaservers.net/vpninfo/servers/v6 \
  | head -n1 | python3 -c 'import sys,json;d=json.load(sys.stdin);print([r["name"] for r in d["regions"] if r.get("port_forward")])'
#    Cross-check the gluetun PIA wiki "Port forwarding" section. Pick a non-US region (Canada = nearest).

# B. Confirm a pinnable stable slskd tag (>= 0.24.4 for native gluetun PF) and capture digest:
curl -s "https://hub.docker.com/v2/repositories/slskd/slskd/tags?page_size=100" | jq -r '.results[].name'
#    Then pin tag + @sha256 digest in the compose.

# C. (Already verified this session, re-confirm if time passes >30 days)
curl -s https://api.github.com/repos/qdm12/gluetun/releases/latest | jq -r '.tag_name'   # gluetun stable
```

---

## Environment Availability

> Probe these on the NAS in Wave 0 (could not probe from the research host).

| Dependency | Required By | Probe | Fallback |
|------------|------------|-------|----------|
| Docker / Container Manager | whole stack | `docker version` | none — blocking |
| `synobridge` network | INFRA-03 | `docker network inspect synobridge` | create external bridge (compose) |
| `/dev/net/tun` | gluetun | `ls -l /dev/net/tun` | none — blocking for VPN |
| PIA subscription w/ PF | INFRA-01 | owner has subscription | none — blocking |
| Docker Hub account + access token | INFRA-04/05 | login test | none — blocking |
| GitHub repo + Actions | INFRA-05 | repo exists | local build + manual push |
| Single hardlink-capable `/data` filesystem | INFRA-06 | hardlink smoke test | none — blocking |

**Blocking unknowns to confirm in Wave 0:** `/dev/net/tun` present, PIA PF region, single-FS `/data`, Docker Hub token, existing *arr `/data` mount shape, PUID/PGID.

---

## Validation Architecture

> `nyquist_validation: true` in config.json. Phase 1 is mostly infrastructure — the "suite" is
> the smoke-test checklist plus a tiny unit test for the Curator stub.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` (Curator stub) + shell smoke-test (infra) |
| Config file | none yet — Wave 0 creates `pyproject.toml`/`pytest.ini` |
| Quick run command | `pytest app/tests -x` |
| Full suite command | `scripts/smoke-test.sh` on the NAS |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Command | File Exists? |
|--------|----------|-----------|---------|-------------|
| INFRA-01 | VPN egress + kill-switch | smoke (NAS) | checklist 1a–1c | ❌ Wave 0 |
| INFRA-02 | PF → listen-port sync (+ restart) | smoke (NAS) | checklist 2a/2b | ❌ Wave 0 |
| INFRA-03 | reachability + *arr | smoke (NAS) | checklist 3a–3c | ❌ Wave 0 |
| INFRA-04 | single compose up | smoke (NAS) | checklist 4a | ❌ Wave 0 |
| INFRA-05 | CI build+push, no secrets | CI + smoke | Actions green + 3d/3e | ❌ Wave 0 |
| INFRA-06 | /data + PUID/PGID + hardlink | smoke (NAS) | checklist 4b–4d | ❌ Wave 0 |
| Curator stub | /healthz, /readyz | unit | `pytest app/tests/test_health.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest app/tests -x` (Curator stub).
- **Per wave merge:** relevant smoke-test section on the NAS.
- **Phase gate:** full smoke-test green (Go/No-Go) before `/gsd:verify-work`.

### Wave 0 Gaps
- [ ] `app/tests/test_health.py` — covers `/healthz` + `/readyz`
- [ ] `app/requirements.txt`, `pyproject.toml`/`pytest.ini`
- [ ] `scripts/smoke-test.sh` — the NAS checklist as a runnable script
- [ ] `.gitignore` includes `.env`

---

## Security Domain

> `security_enforcement` absent in config.json → treated as enabled.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | gluetun control-server apikey; slskd API key |
| V3 Session Management | no | Phase-1 stub has no sessions |
| V4 Access Control | yes | control-server role scoped to PF route; slskd not WAN-exposed (LAN/Tailscale only) |
| V5 Input Validation | minimal | FastAPI/pydantic on the stub |
| V6 Cryptography | yes (gluetun) | WireGuard/OpenVPN — never hand-roll |
| V14 Config | yes | no secrets in git/image; pinned digests; `NET_ADMIN` only on gluetun |

### Known Threat Patterns
| Pattern | STRIDE | Mitigation |
|---------|--------|-----------|
| VPN IP leak (kill-switch open) | Information Disclosure | gluetun fail-closed firewall; smoke-test 1c |
| Secrets in git/image | Information Disclosure | `.env` gitignored, GH repo secrets, Docker Hub token; `docker history` scan (3e) |
| Control-server unauthenticated | Tampering/Elevation | v3.40+ apikey auth, role scoped to PF route |
| Supply-chain image swap | Tampering | pin by digest; first-party images |
| Over-privileged container | Elevation | `NET_ADMIN` only on gluetun; Curator mounts `/data` read-only in Phase 1 |
| WAN exposure of slskd/Curator | Information Disclosure | never publish to WAN; LAN/Tailscale only; home firewall stays closed |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | A pinnable stable slskd tag ≥ 0.24.4 exists (native PF) | Std Stack | If only 0.23.1 is pinnable, native PF is unavailable → must use a port-sync sidecar |
| A2 | PIA WireGuard supports PF via gluetun (using OPENVPN_USER/PASSWORD for token) | compose | If WG PF flaky on the build, fall back to `VPN_TYPE: openvpn` |
| A3 | Existing *arr stack uses a single `/data` mount (TRaSH layout) | INFRA-06 | If split mounts, hardlinks fail → must realign *arr mounts |
| A4 | PUID=1026/PGID=100 (typical Synology) | compose | Wrong values → permission errors; confirm with `id` |
| A5 | slskd web/API port is 5030 | compose, smoke | If different, adjust gluetun `ports:` + `SLSKD_URL` |
| A6 | Live PIA PF region list still excludes US, includes Canada | Pitfall 2 | Verify live; pick a confirmed PF region |

**All A1–A6 are resolved by the Wave 0 Verification Protocol before the planner locks decisions.**

---

## Open Questions

1. **slskd stable tag ≥ 0.24.4** — confirm pinnable (A1). If not, decide sidecar vs `latest`-pin. (Wave 0)
2. **WireGuard vs OpenVPN for PIA PF** (A2) — recommend WG; confirm PF works on the chosen gluetun build, else OpenVPN. (Wave 0)
3. **Existing *arr `/data` mount + PUID/PGID** (A3/A4) — only resolvable on the NAS. (Wave 0)
4. **Live PIA PF region** (A6) — confirm via PIA server list + gluetun PIA wiki; pick Canada for latency. (Wave 0)

---

## State of the Art

| Old Approach | Current Approach | When | Impact |
|--------------|------------------|------|--------|
| External port-sync sidecar polling gluetun | slskd **native** `SLSKD_VPN_*` integration | slskd v0.24.4+ [VERIFIED] | One fewer container; runtime listen-port updates |
| gluetun control server open by default | ALL routes private; auth required | gluetun **v3.40.0** [VERIFIED] | Must set `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` or slskd 401s |
| `/v1/openvpn/portforwarded` | `/v1/portforward` | recent gluetun [VERIFIED: wiki] | Old path deprecated/redirects |
| Multi-arch QEMU builds | single `linux/amd64` | n/a | Faster CI on amd64-only NAS |

**Deprecated:** floating `:latest` for reproducibility; the old portforward route.

---

## Sources

### Primary (HIGH confidence — live-verified this session)
- slskd `docs/vpn.md` — native gluetun integration; exact `SLSKD_VPN_*` env vars; `GLUETUN_HTTP_CONTROL_SERVER_ENABLE`; `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` apikey form; wait-for-ready + port-forwarding behavior: https://github.com/slskd/slskd/blob/master/docs/vpn.md
- slskd `docs/config.md` — `SLSKD_SLSK_LISTEN_PORT`, API key format, integration.vpn YAML: https://github.com/slskd/slskd/blob/master/docs/config.md
- slskd issue #1660 — gluetun 401 without control-server auth: https://github.com/slskd/slskd/issues/1660
- gluetun control-server wiki — v3.40.0 breaking change (all routes private), `[[roles]]` toml, `genkey`, `/v1/portforward`, default-role JSON: https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/control-server.md
- gluetun PIA wiki — `VPN_PORT_FORWARDING=on`, `VPN_PORT_FORWARDING_PROVIDER`, `/gluetun` persists port 60 days, US not supported for PF: https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/private-internet-access.md
- gluetun release info — latest stable v3.41.1; auth role system introduced v3.40.0: https://github.com/qdm12/gluetun/releases
- Docker Hub tag APIs — `qmcgaw/gluetun:v3.41.1` and `slskd/slskd` tags confirmed; `docker/*` action latest majors via GitHub releases API (all queried 2026-05-30)
- PIA help docs — non-US servers support PF, US do not: https://helpdesk.privateinternetaccess.com/hc/en-us/articles/46701354754843-Next-Generation-Port-Forwarding

### Secondary (MEDIUM — verified against official sources)
- PIA PF regions (Canada etc.): top10vpn / vpnalert PIA port-forwarding guides
- Sibling research (live-verified 2026-05-29): `.planning/research/STACK.md`, `ARCHITECTURE.md`, `PITFALLS.md`, `SUMMARY.md` — full agreement on topology, env vars, and pitfalls

### Tertiary (LOW — needs Wave 0 live confirmation)
- Exact live PIA PF region list (changes over time) — confirm via `serverlist.piaservers.net/vpninfo/servers/v6`
- slskd stable tag ≥ 0.24.4 pinnability

---

## Metadata

**Confidence breakdown:**
- Topology / shared-netns / gluetun-on-synobridge: HIGH — re-verified + sibling-corroborated.
- slskd↔gluetun integration + control-server auth: HIGH — official slskd `docs/vpn.md` + gluetun wiki + issue #1660.
- Image/action versions: HIGH for gluetun v3.41.1 and docker actions; MEDIUM for the exact slskd stable tag (scheme requires Wave 0 selection).
- PIA PF region list: MEDIUM — principle (non-US yes, US no) verified; exact current list is Wave 0.
- `/data` layout + PUID/PGID: MEDIUM — standard pattern given; live NAS confirmation required.

**Research date:** 2026-05-30
**Valid until:** ~2026-06-29 (30 days; re-confirm gluetun/slskd tags and PIA region list if older).
