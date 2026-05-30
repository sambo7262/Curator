# Roadmap: Curator

**Created:** 2026-05-29
**Mode:** mvp (Vertical MVP)
**Granularity:** standard
**Core Value:** Anything already monitored in Lidarr that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads and zero manual interaction.

## North Star

Sequence toward one real gap **acquired → imported → visible in Plex** as early as feasible (a thin end-to-end vertical slice through the risky networking foundation), then harden and expand into a fully hands-off daemon.

## Phases

- [ ] **Phase 1: VPN-Routed Source Foundation** - gluetun+PIA tunnel with port-forward, slskd routed through it and reachable on synobridge, deployed from compose pulling a CI-built image
- [ ] **Phase 2: First Vertical Slice (manual-trigger acquire → import → Plex)** - one chosen Lidarr gap is searched, downloaded via slskd, imported by Lidarr, and reflected in Plex, end-to-end
- [ ] **Phase 3: Correct Matching & Quality Gating** - candidates are scored against MusicBrainz identity and filtered by quality/cutoff (incl. fake-FLAC heuristics) so only the right release at the right quality is grabbed
- [ ] **Phase 4: Autonomous Fallback Loop with State Memory** - a continuous daemon detects gaps after a grace window, dedups via persistent SQLite state, and retries with backoff — no manual triggering
- [ ] **Phase 5: Sharing, Resilience & Observability** - automated slskd sharing keeps the account unblocked, the daemon self-recovers and surfaces stuck items, and status/notifications flow to Homepage + Apprise

## Phase Details

### Phase 1: VPN-Routed Source Foundation
**Goal**: A privacy-safe Soulseek source exists — slskd's traffic exits only through a gluetun+PIA tunnel with a forwarded inbound port synced to slskd's listen port — and the whole stack deploys reproducibly from a compose file pulling a CI-built Docker Hub image.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-04, INFRA-05, INFRA-06
**Success Criteria** (what must be TRUE):
  1. With the VPN up, slskd's outbound traffic exits via the PIA tunnel; if gluetun drops, slskd has no network path (kill-switch verified — no IP leak).
  2. The gluetun forwarded port (non-US PF region) is read and applied to slskd's listen port automatically, and re-applied when the forwarded port changes.
  3. A `git push` triggers GitHub Actions to build and push a `linux/amd64` image to Docker Hub.
  4. `docker compose up` from a single YAML (pulling the Docker Hub image) brings the stack online, with config/state/downloads on `/volume1` bind-mounts owned by the correct PUID/PGID.
**Plans**: TBD

### Phase 2: First Vertical Slice (manual-trigger acquire → import → Plex)
**Goal**: Prove the full acquisition pipeline end-to-end for a single, hand-picked Lidarr gap: Curator (running on synobridge) reaches Lidarr and slskd, searches Soulseek, downloads a candidate to completion, hands it to Lidarr for a real import into `/volume1`, and Plex reflects the new media.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: INFRA-03, GAP-01, ACQ-01, ACQ-02, IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-04
**Success Criteria** (what must be TRUE):
  1. Curator, on the `synobridge` network, reads Lidarr's wanted/missing list by container name and reaches slskd via gluetun's published port.
  2. For one selected gap, Curator triggers an slskd search and downloads a chosen candidate, watching it to completion.
  3. The completed download lands at a path identical across containers, and Curator triggers a Lidarr Manual Import that succeeds (verified present in the `/volume1` library).
  4. Plex shows the newly imported album after Curator triggers/verifies a library scan.
**Plans**: TBD

### Phase 3: Correct Matching & Quality Gating
**Goal**: Curator only acquires the right release at the right quality — candidates are scored against the item's MusicBrainz identity and rejected below a confidence threshold, and filtered against the Lidarr quality profile/cutoff (including heuristic fake/transcoded-FLAC checks) before any download.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: MATCH-01, MATCH-02, QUAL-01, QUAL-02, QUAL-03
**Success Criteria** (what must be TRUE):
  1. Given multiple slskd candidates, Curator scores them on MusicBrainz identity (artist, album, track-count completeness, edition/year, format) and selects the best match.
  2. When no candidate clears the configurable confidence threshold, Curator declines to download rather than grabbing a wrong match.
  3. Curator reads the item's Lidarr quality profile/cutoff and never enqueues a candidate below cutoff (no downgrades).
  4. A FLAC candidate failing bitrate/size/source-tag sanity heuristics is rejected before download.
**Plans**: TBD

### Phase 4: Autonomous Fallback Loop with State Memory
**Goal**: Curator runs the acquisition loop hands-off: a scheduled poll detects missing and cutoff-unmet gaps only after a grace window (Usenet first), persistent SQLite state prevents redundant or in-flight re-downloads, and exponential backoff plus permanent "unavailable" memory govern retries.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: GAP-02, GAP-03, STATE-01, STATE-02, STATE-03, REL-01
**Success Criteria** (what must be TRUE):
  1. Curator runs continuously as a daemon on a scheduled poll loop, detecting both wanted/missing and cutoff-unmet items with no manual triggering.
  2. Curator acts on a gap only after its configurable grace window elapses, leaving the Usenet pipeline first crack.
  3. Each tracked item's status (pending/searching/grabbed/imported/unavailable/blacklisted) persists in SQLite, and an already-satisfied or in-flight item is never re-downloaded.
  4. Failed attempts back off exponentially, and genuinely-unavailable items are permanently remembered so Curator stops retrying them.
**Plans**: TBD

### Phase 5: Sharing, Resilience & Observability
**Goal**: Curator is production-hands-off: slskd automatically shares real library content so the account is never blocked as a leecher, the daemon self-recovers from transient failures and surfaces stuck/failed items instead of failing silently, and status flows to Homepage while events push via Apprise.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: SHARE-01, SHARE-02, ACQ-03, IMPORT-05, REL-02, REL-03, OBS-01, OBS-02
**Success Criteria** (what must be TRUE):
  1. slskd shares point at real library content and stay active across restarts with no manual intervention, so the account isn't leech-blocked.
  2. Curator self-recovers from Lidarr/slskd/VPN restarts and network blips, and cancels/marks/backs-off partial/failed/stalled downloads rather than hanging.
  3. Import failures and stuck items (exceeded retries / blocked / unresolved) are reconciled or surfaced — never silently dropped.
  4. A JSON status endpoint exposes gap-queue/in-flight/stuck counts consumable by a Homepage `customapi` widget, and Apprise pushes notifications on grab/import/failure/blocked events.
**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. VPN-Routed Source Foundation | 0/0 | Not started | - |
| 2. First Vertical Slice | 0/0 | Not started | - |
| 3. Correct Matching & Quality Gating | 0/0 | Not started | - |
| 4. Autonomous Fallback Loop with State Memory | 0/0 | Not started | - |
| 5. Sharing, Resilience & Observability | 0/0 | Not started | - |

## Coverage

All 32 v1 requirements mapped to exactly one phase. No orphans, no duplicates.

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1 | INFRA-01, INFRA-02, INFRA-04, INFRA-05, INFRA-06 | 5 |
| 2 | INFRA-03, GAP-01, ACQ-01, ACQ-02, IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-04 | 8 |
| 3 | MATCH-01, MATCH-02, QUAL-01, QUAL-02, QUAL-03 | 5 |
| 4 | GAP-02, GAP-03, STATE-01, STATE-02, STATE-03, REL-01 | 6 |
| 5 | SHARE-01, SHARE-02, ACQ-03, IMPORT-05, REL-02, REL-03, OBS-01, OBS-02 | 8 |
| **Total** | | **32** |

## Notes on UI

No phase carries a `**UI hint**: yes`. Per project scope, there is no standalone dashboard UI — status surfaces through the owner's existing Homepage via a `customapi` JSON endpoint (OBS-01), which is an API contract, not a user-facing interface. slskd ships its own web UI but Curator does not build or own it.

## v2 (Out of Roadmap)

Deferred — not covered by these phases: BOOK-01..04 (Readarr/books), QUAL-04 (spectral FLAC analysis).

---
*Roadmap created: 2026-05-29*
