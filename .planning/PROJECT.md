# Curator

## What This Is

Curator is an autonomous, fallback-only media gap-filler for a Synology homelab. When an item is monitored/wanted in **Lidarr (music)** or **Readarr (books)** and the existing Usenet pipeline can't acquire it within a grace window, Curator automatically sources it from a P2P network (Soulseek/slskd leading; exact engine decided in research), validates the correct release at the configured quality, imports it cleanly into the library and Plex, and satisfies the network's give-back/sharing obligation — all with zero day-to-day interaction from the owner.

It is best understood as a **reliable, hands-off process** rather than a UI-driven app. The owner has already curated what they want down to the track/title level inside Lidarr/Readarr; Curator's only job is to *get it* without manual labor.

## Core Value

**Anything already monitored in Lidarr/Readarr that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads and zero manual interaction.**

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. -->

- [ ] Detect monitored/wanted gaps in Lidarr (music) and Readarr (books) that the Usenet pipeline has not satisfied within a grace window ("grace then fallback")
- [ ] Acquire those gaps automatically from a P2P source (Soulseek/slskd leading; engine TBD in research)
- [ ] Match correctly — pull the right release/edition/track, not a mislabeled or wrong item
- [ ] Enforce quality by deferring to existing Lidarr/Readarr quality profiles/cutoffs (no downgrades)
- [ ] Prevent redundant downloads via persistent state memory (track attempted / succeeded / unavailable, with backoff)
- [ ] Hand files off so Lidarr/Readarr import cleanly into the `/volume1` library and Plex reflects them — no manual file mapping
- [ ] Satisfy the give-back/sharing obligation automatically (share library so peers don't block as a leecher) without manual effort
- [ ] Route source traffic through a VPN sidecar (existing PIA subscription) so the real home IP stays hidden and the home firewall stays closed
- [ ] Run fully hands-off — no day-to-day interaction once configured
- [ ] Surface status (gap queue, in-flight, stuck) via a Homepage-compatible widget/endpoint, plus push notifications on grabs/failures/blocks
- [ ] Deploy as a docker-compose YAML pulling a Docker Hub image (built/pushed by GitHub Actions), running on the `synobridge` network with bind-mounts to `/volume1`, `linux/amd64`

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- Radarr/Sonarr (movies/TV) — that media is genuinely unavailable on these sources, not just missing from indexers; Soulseek is weak outside music/books
- Replacing the primary Usenet pipeline — Curator is strictly supplementary; Usenet always wins first
- Pure public torrents — owner prefers SSL/private posture; ruled out
- A standalone dashboard UI — status surfaces through the owner's existing Homepage instead
- Changing the owner's day-to-day curation workflow — curation stays in Lidarr/Readarr; Curator only acquires

## Context

- **Homelab:** Synology DS423+ (Intel Celeron J4125, `linux/amd64`, ~18GB RAM — ample headroom). Docker via Container Manager. Plex runs as the native Synology app (not Docker); Plexamp for audio.
- **Existing media stack** (all on the `synobridge` Docker network): Plex, Radarr, Sonarr, Lidarr, Prowlarr (indexer manager), SABnzbd (download client). Usenet is the primary pipeline. Indexers: AnimeTosho, NZB, NZBgeek, NZBPlanet, Usenet Crawler. Owner also runs/uses Readarr for books.
- **Access model:** Tailscale VPN gates everything to the LAN — apps are only reachable on the local network. (Note: Tailscale secures inbound LAN access; it does NOT anonymize outbound P2P traffic — hence the separate PIA sidecar for the source.)
- **Library state:** ~60 artists, roughly 70% complete vs. what's curated in Lidarr; frequent gaps. Music and books are "missing from indexers" rather than "unavailable," which is why a P2P gap-filler helps.
- **Prior attempt:** Owner has run slskd + Soularr before. It "worked" but required heavy manual labor — its weaknesses were redundant downloads, incorrect matches, quality downgrades, complex supplementary configuration, and import/sync friction with Lidarr/Plex. Removing that labor is the entire point of Curator.
- **Soulseek reality:** P2P network, primarily music with decent book coverage. Transfers are direct peer connections — peers see your IP; there is no SSL tunnel like Usenet. Lower-profile than public torrents but not private. Leechers (non-sharers) get blocked, so sharing is mandatory for reliable access. slskd (not Soularr) manages the shares.
- **Readarr caveat:** Upstream Readarr development was halted in 2024 — it still runs but is unmaintained. Acceptable for now; flagged for awareness.
- **Dev/deploy loop:** Owner tests locally via NAS deployment, manages repos on GitHub, uses GitHub Actions to build and push an image to Docker Hub, and deploys via a YAML that pulls the image — iterating by tearing down/rebuilding.

## Constraints

- **Platform:** Synology DS423+, Container Manager, `linux/amd64` images only.
- **Networking:** Must run on the `synobridge` Docker network to reach Lidarr/Readarr/Plex/SABnzbd by container name.
- **Persistence:** Bind-mounts to NAS volume paths (`/volume1/docker/<app>` style); app may run its own SQL (SQLite/Postgres) — space/compute are not a concern.
- **Deployment:** Single docker-compose YAML pulling a Docker Hub image; CI/CD via GitHub Actions (build → push Docker Hub → YAML pull).
- **Privacy:** Source traffic must route through the existing PIA VPN (gluetun-style sidecar) with port forwarding; home firewall must stay closed. PIA port forwarding is unavailable on US servers — use a supported non-US region.
- **Quality:** Defer to existing Lidarr/Readarr quality profiles/cutoffs — no separate quality logic that could downgrade.
- **Behavior:** Strictly supplementary and fallback-only; must not race or override the Usenet pipeline.
- **Observability:** Integrate with existing Homepage dashboard (custom widget/API) rather than building a separate UI.
- **Operation:** Fully hands-off after initial setup — no recurring manual interaction.

## Key Decisions

<!-- Decisions that constrain future work. Add throughout project lifecycle. -->

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Scope = music (Lidarr) + books (Readarr) only | Soulseek excels at music, decent for books; video is genuinely unavailable, not indexer-missing | — Pending |
| Fallback-only, "grace then fallback" trigger | Keep it strictly supplementary; let Usenet win first, avoid racing | — Pending |
| Quality defers to Lidarr/Readarr profiles | Reuse existing curation; avoid downgrade bugs from separate logic | — Pending |
| Source engine decided in research (slskd-direct vs Soularr+slskd vs alternatives) | Owner is outcome-focused ("greater access"), not tool-attached; wants the root solution | — Pending |
| Privacy via VPN sidecar reusing existing PIA | Consistent with owner's Usenet-SSL preference; port forwarding keeps home firewall closed; no new cost | — Pending |
| Share library to satisfy give-back, automated | Soulseek blocks leechers; sharing is mandatory for reliable access | — Pending |
| Status via existing Homepage, not standalone UI | Owner already runs Homepage; one pane of glass, less overhead | — Pending |
| Build whatever it takes for true hands-off operation | Owner's barrier was manual labor, not code volume — autonomy is the priority | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-29 after initialization*
