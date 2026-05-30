# Project State: Curator

**Last updated:** 2026-05-29
**Current phase:** Phase 1: VPN-Routed Networking Foundation
**Status:** Not started

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-29)

**Core value:** Anything already monitored in Lidarr (music) or Readarr (books) that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads, no leftover junk on the volume, and zero manual interaction.
**Current focus:** Stand up the gluetun+PIA / slskd / shared-`/data` networking foundation and the CI/compose deploy loop — the highest-risk layer that gates everything else.

## Current Position

Phase: 1 of 6 (VPN-Routed Networking Foundation)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-05-29 — Corrected roadmap created (horizontal layers, music + books scope)

Progress: [░░░░░░░░░░] 0%

Roadmap (6 horizontal layers, all 34 v1 requirements mapped):
1. **VPN-Routed Networking Foundation** ← next
2. State Ledger + *arr Adapter + Gap Detection
3. Matching & Quality Gating
4. Acquisition, Staging & Clean Import
5. Autonomy, Sharing & Self-Recovery
6. Observability & Notifications

Build bottom-up so the riskiest infra surfaces failures first; the state ledger is laid before the source engine (the spine that prevents the prior redundant-download pain). Value lands when the full acquire→stage→import→purge loop closes in Phase 4 and becomes hands-off in Phase 5. Next step: `/gsd:plan-phase 1`.

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: none yet
- Trend: n/a

*Updated after each plan completion*

## Accumulated Context

### Decisions

Full log in PROJECT.md Key Decisions. Recent decisions affecting current work:

- **Mode:** Horizontal Layers (standard granularity, yolo, parallelization on) — build complete technical layers and assemble; value lands when the loop closes.
- **Scope correction (supersedes earlier MVP/music-only run):** v1 = MUSIC (Lidarr, primary) + BOOKS (Readarr, best-effort, behind a `*-arr`-agnostic adapter so Readarr's retired status can't break music). slskd-DIRECT (no Soularr). Import & Cleanup is a first-class layer: download into isolated per-item staging/quarantine → import ONLY wanted files via `*arr` Manual Import → verify → AUTO-PURGE staging (owner's 6th pain: no leftover junk, no manual deletion). Spectral-FLAC is OUT of v1 (heuristic checks only).
- **Networking (verified):** gluetun on synobridge publishes slskd's ports; slskd `network_mode: service:gluetun`. Curator reaches slskd at `http://gluetun:5030`, NEVER `http://slskd` (#1 misconfig). slskd ≥0.24.4 has NATIVE gluetun PF — do NOT build port-sync. gluetun ctrl server (~v3.40+) needs an apikey, matched in `SLSKD_VPN_GLUETUN_API_KEY`. PIA PF = NON-US region only; bind-mount `/gluetun` (60-day port). `FIREWALL_OUTBOUND_SUBNETS` must include synobridge+LAN; kill-switch ON. Single identical `/data` tree across all four containers → atomic hardlinks; consistent PUID/PGID + umask 002.
- **Stack:** Python 3.12, httpx + pyarr + slskd-api, APScheduler, SQLModel/SQLite WAL, FastAPI :8674, Apprise. Pin slskd 0.25.1, gluetun v3.x dated tag.
- Quality defers entirely to `*arr` profiles; matching is precision-over-recall. Infra outages classified separately — never burn a per-item attempt.

### Pending Todos

None yet.

### Blockers/Concerns

Verify-live before/within the relevant phase (research open questions):
- [Phase 1] Exact `SLSKD_VPN_*` var casing + control-server apikey key name; gluetun `config.toml` role/apikey shape; current PF-capable PIA region list.
- [Phase 1] Existing `*arr` mount convention on this Synology — dictates the `/data` layout (settle before the importer).
- [Phase 2/4] Lidarr/Readarr v1 endpoint/command + ManualImport payload shape vs live `/swagger`.
- [Phase 5] Hands-off is a testable success criterion (SC5): N days, zero manual actions, gaps still fill.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Quality | QUAL-04 spectral/frequency-cutoff FLAC analysis | v2 | 2026-05-29 (init) |
| Sources | SRC-01 pluggable second source backend | v2 | 2026-05-29 (init) |

## Session Continuity

Last session: 2026-05-29
Stopped at: Corrected ROADMAP.md + REQUIREMENTS.md traceability + this STATE.md written. Books ride the same layers behind the adapter — enable best-effort only after the music loop is hands-off; never let books gate music.
Resume file: None — run `/gsd:plan-phase 1`

---
*State initialized: 2026-05-29 after corrected roadmap creation*
