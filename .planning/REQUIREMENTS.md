# Requirements: Curator

**Defined:** 2026-05-29
**Core Value:** Anything already monitored in Lidarr that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads and zero manual interaction.

## v1 Requirements

Autonomous, fallback-only **music** gap-filling (Lidarr) via slskd, fully hands-off. Books (Readarr) deferred to v2.

### Infrastructure & Deployment

- [ ] **INFRA-01**: slskd traffic routes through a gluetun + PIA VPN sidecar with port forwarding on a non-US region, with a kill-switch that prevents IP leak if the VPN drops
- [ ] **INFRA-02**: The gluetun forwarded port is synced to slskd's listen port automatically whenever it changes
- [ ] **INFRA-03**: The Curator orchestrator runs on the `synobridge` network, reaching Lidarr/Plex by container name and slskd via gluetun's published port
- [ ] **INFRA-04**: The whole stack deploys from a single docker-compose YAML that pulls a Docker Hub image
- [ ] **INFRA-05**: GitHub Actions builds and pushes a `linux/amd64` image to Docker Hub on commit
- [ ] **INFRA-06**: Config, state DB, and downloads use `/volume1` bind-mounts with correct PUID/PGID ownership

### Gap Detection

- [ ] **GAP-01**: Curator detects monitored missing items from Lidarr (wanted/missing)
- [ ] **GAP-02**: Curator detects monitored cutoff-unmet items from Lidarr (wanted/cutoff)
- [ ] **GAP-03**: Curator only acts on an item after a configurable grace window elapses, so the Usenet pipeline gets first crack (fallback-only)

### Match Validation

- [ ] **MATCH-01**: Curator scores slskd candidates against the item's MusicBrainz identity (artist, album, track-count completeness, edition/year, format)
- [ ] **MATCH-02**: Curator rejects candidates below a configurable confidence threshold rather than downloading a wrong match

### Quality Enforcement

- [ ] **QUAL-01**: Curator reads the item's Lidarr quality profile and cutoff via the API
- [ ] **QUAL-02**: Curator filters slskd candidates by format/bitrate BEFORE downloading and never grabs below cutoff (no downgrades)
- [ ] **QUAL-03**: Curator applies heuristic fake/transcoded-FLAC checks (bitrate/size/source-tag sanity) before accepting a FLAC candidate

### Persistent State & Dedup

- [ ] **STATE-01**: Curator persists each tracked item's status (pending/searching/grabbed/imported/unavailable/blacklisted) in SQLite
- [ ] **STATE-02**: Curator never re-downloads an item that is already satisfied or in-flight (dedup)
- [ ] **STATE-03**: Curator applies exponential backoff to retries and permanently remembers genuinely-unavailable items so it stops retrying them

### Acquisition

- [ ] **ACQ-01**: Curator triggers slskd searches via REST API for eligible gaps
- [ ] **ACQ-02**: Curator initiates the chosen candidate's download via slskd and watches it to completion
- [ ] **ACQ-03**: Curator handles partial/failed/stalled downloads (cancel, mark, back off)

### Import Handoff

- [ ] **IMPORT-01**: Completed downloads land where Lidarr can import them, with identical paths as seen by each container
- [ ] **IMPORT-02**: Curator triggers Lidarr import/rename via the Manual Import / command API rather than blind drop-folder
- [ ] **IMPORT-03**: Curator verifies the item imported successfully into the `/volume1` library
- [ ] **IMPORT-04**: Curator confirms Plex reflects the new media (triggers/verifies a library scan)
- [ ] **IMPORT-05**: Curator reconciles or surfaces import failures instead of silently dropping them

### Automated Sharing

- [ ] **SHARE-01**: Curator configures slskd shares pointing at real library content so the account isn't blocked as a leecher
- [ ] **SHARE-02**: Sharing stays active with no manual intervention

### Reliability & Hands-Off Operation

- [ ] **REL-01**: Curator runs continuously as a daemon with a scheduled poll loop and requires no manual triggering
- [ ] **REL-02**: Curator self-recovers from transient failures (Lidarr/slskd/VPN restarts, network blips) without manual intervention
- [ ] **REL-03**: Curator surfaces stuck items (exceeded retries / blocked / unresolved) rather than failing silently

### Observability

- [ ] **OBS-01**: Curator exposes a JSON status endpoint (gap queue, in-flight, stuck counts) consumable by a Homepage `customapi` widget
- [ ] **OBS-02**: Curator sends push notifications via Apprise on grab/import/failure/blocked events

## v2 Requirements

Deferred to future release. Tracked but not in the current roadmap.

### Books (Readarr)

- **BOOK-01**: Detect monitored missing/cutoff-unmet book items from Readarr
- **BOOK-02**: Match book candidates by author + title + format/edition
- **BOOK-03**: Import handoff to Readarr → library → Plex
- **BOOK-04**: Isolate Readarr behind an adapter so its unmaintained status (or replacement) doesn't ripple into the core loop

### Quality (deeper)

- **QUAL-04**: Spectral/frequency-cutoff analysis to detect upscaled/transcoded FLAC with high confidence

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Radarr/Sonarr / video support | Soulseek is weak for video; that media is genuinely unavailable, not indexer-missing |
| Soularr dependency | slskd-direct chosen to fix dedup/match/quality at the root (Soularr's weaknesses are the prior pain points) |
| Pure public torrents | Owner prefers SSL/private posture; ruled out |
| Standalone dashboard UI | Status surfaces through the owner's existing Homepage widget instead |
| Web search/browse UI | Reintroduces manual interaction; contradicts hands-off core value |
| Manual approval queue / day-to-day interaction | Contradicts hands-off core value |
| Becoming a primary downloader | Strictly supplementary by design — Usenet always wins first |
| Manual file mapping | The exact labor Curator exists to eliminate |

## Traceability

Which phases cover which requirements. Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (populated by roadmapper) | — | Pending |

**Coverage:**
- v1 requirements: 32 total
- Mapped to phases: (pending roadmap)
- Unmapped: (pending roadmap)

---
*Requirements defined: 2026-05-29*
*Last updated: 2026-05-29 after initial definition*
