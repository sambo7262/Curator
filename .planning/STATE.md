# State: Curator

<!-- Project memory. Updated at phase/plan transitions. -->

## Project Reference

**Core Value:** Anything already monitored in Lidarr that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads and zero manual interaction.

**Current Focus:** Phase 1 — VPN-Routed Source Foundation (establish the gluetun+PIA → slskd → synobridge networking base and reproducible deploy).

**Mode:** mvp (Vertical MVP) — thread a thin end-to-end slice early, then harden.

## Current Position

- **Milestone:** v1 (music-only, Lidarr)
- **Phase:** 1 — VPN-Routed Source Foundation
- **Plan:** None yet (run `/gsd:plan-phase 1`)
- **Status:** Roadmap created; not started

**Progress:** Phase 0/5 complete

```
[ ][ ][ ][ ][ ]  0/5 phases
```

## Performance Metrics

| Metric | Value |
|--------|-------|
| Phases complete | 0/5 |
| v1 requirements mapped | 32/32 |
| v1 requirements satisfied | 0/32 |

## Accumulated Context

### Key Decisions (carried from PROJECT.md / research)

- Drive slskd **directly** via its REST API; do NOT adopt Soularr as a runtime dependency (reuse its matching heuristics as reference only).
- Orchestrator stack: Python 3.12, httpx, pydantic/pydantic-settings, APScheduler, FastAPI+uvicorn (status endpoint), SQLAlchemy/stdlib sqlite3, apprise, tenacity. Pin versions at build (research tool layer could not live-verify).
- Networking pattern: slskd uses `network_mode: "service:gluetun"`; slskd's `5030` published on gluetun; Curator/Homepage reach slskd at `http://gluetun:5030/api/v0`.
- PIA port forwarding requires a **non-US** region (e.g. CA Toronto/Montreal); US has no PF.
- Persistence: SQLite (WAL), bind-mounted to `/volume1`.
- Status via Homepage `customapi` widget; notifications via Apprise.
- Automated slskd sharing is MANDATORY (leech-block avoidance).
- v1 is MUSIC ONLY (Lidarr). Books (Readarr) and spectral-FLAC are v2.

### Open TODOs / Verify-Before-Build (from research)

1. Pin exact versions: slskd, gluetun (and confirm v3 env var names), Python libs, GitHub Action majors.
2. Confirm current PIA PF region list; pick non-US PF region.
3. Confirm slskd listen-port config key and whether it can be set without container restart (drives INFRA-02 mechanism).
4. Confirm gluetun control-server path `/v1/openvpn/portforwarded` for the current major.
5. Confirm Homepage `customapi` mapping schema (`mappings`, `additionalField`).
6. Confirm Lidarr is API **v1** (not v3) and lock the manualimport/command path against the live `/swagger`.

### Blockers

None.

## Session Continuity

**Last action:** Roadmap and STATE initialized from PROJECT.md + REQUIREMENTS.md + research/STACK.md (2026-05-29).

**Next action:** Run `/gsd:plan-phase 1` to decompose Phase 1 (VPN-Routed Source Foundation) into executable plans.

**Notes:** During this session the sandbox tool layer intermittently returned empty output for some Read/Bash calls (matches the warning in research/STACK.md). The two research docs available are STACK.md and FEATURES.md; the ARCHITECTURE/PITFALLS/SUMMARY files referenced in planning context were not present on disk — phasing was derived directly from REQUIREMENTS.md, PROJECT.md, and STACK.md.

---
*State initialized: 2026-05-29*
