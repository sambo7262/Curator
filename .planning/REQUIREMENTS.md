# Requirements: Curator

**Defined:** 2026-05-29
**Core Value:** Anything already monitored in Lidarr (music) or Readarr (books) that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads, no leftover junk on the volume, and zero manual interaction.

## v1 Requirements

Autonomous, fallback-only gap-filling via slskd (direct, no Soularr), fully hands-off.
**Scope = MUSIC (Lidarr, primary) + BOOKS (Readarr, best-effort).** Both ship in v1. Readarr is isolated
behind a `*-arr`-agnostic adapter so its retired/unmaintained status (or a future replacement) can never break
the music path. Music must work end-to-end before the books adapter is layered in.

### *arr Adapter (the integration seam)

- [x] **ARR-01**: Curator talks to Lidarr and Readarr through a single `*-arr`-agnostic adapter interface (wanted/missing, cutoff, records, command/import), so Readarr is a pluggable, replaceable module and never couples to the core loop
- [x] **ARR-02**: The adapter exposes each item's identity + quality profile/cutoff uniformly to the core, defends against missing/empty Readarr metadata (degrade gracefully, never crash the loop), and lets books be added best-effort without gating music

### Infrastructure & Deployment

- [ ] **INFRA-01**: slskd traffic routes through a gluetun + PIA VPN sidecar with port forwarding on a non-US PF-capable region, kill-switch ON (fail-closed — no IP/DNS leak if the VPN drops), and `FIREWALL_OUTBOUND_SUBNETS` set so the `*arr` API stays reachable from inside the tunnel
- [ ] **INFRA-02**: The gluetun forwarded port is synced to slskd's listen port automatically whenever it changes (slskd native gluetun integration; control-server apikey configured), surviving restarts
- [ ] **INFRA-03**: The Curator orchestrator runs on the `synobridge` network, reaching Lidarr/Readarr/Plex by container name and slskd via gluetun's published port (`http://gluetun:5030`, never `http://slskd`)
- [ ] **INFRA-04**: The whole stack deploys from a single docker-compose YAML that pulls a Docker Hub image
- [ ] **INFRA-05**: GitHub Actions builds and pushes a `linux/amd64` image to Docker Hub on commit, with no secrets baked into the image
- [ ] **INFRA-06**: Config, state DB, and downloads use `/volume1` bind-mounts with a single identical `/data` tree across slskd/curator/Lidarr/Readarr (atomic hardlink imports) and correct PUID/PGID + umask ownership

### Gap Detection

- [x] **GAP-01**: Curator detects monitored missing items from the `*arr` (wanted/missing) via the adapter
- [x] **GAP-02**: Curator detects monitored cutoff-unmet items from the `*arr` (wanted/cutoff) via the adapter
- [x] **GAP-03**: Curator only acts on an item after a configurable grace window elapses AND no active/queued Usenet grab exists, so the Usenet pipeline gets first crack (fallback-only, never races)

### Match Validation

- [x] **MATCH-01**: Curator scores slskd candidates against the item's authoritative identity (artist/album, track-count completeness, edition/year, format for music; author/title + format/edition for books)
- [x] **MATCH-02**: Curator rejects candidates below a configurable confidence threshold (precision over recall) rather than downloading a wrong match

### Quality Enforcement

- [x] **QUAL-01**: Curator reads the item's `*arr` quality profile and cutoff via the adapter
- [x] **QUAL-02**: Curator filters slskd candidates by format/bitrate BEFORE downloading and never grabs below cutoff (no downgrades)
- [x] **QUAL-03**: Curator applies heuristic fake/transcoded-FLAC checks (bitrate/size/source-tag sanity) before accepting a FLAC candidate

### Persistent State & Dedup

- [x] **STATE-01**: Curator persists each tracked item's lifecycle status (pending/searching/grabbed/downloaded/imported/unavailable/blacklisted) in SQLite (WAL) — the spine, built before the source engine *(02-02: items table with the 7-value status CHECK enum + WAL durability + startup migration hook; restart-durability proven by `test_persists_across_reconnect`)*
- [x] **STATE-02**: Curator never re-downloads an item that is already satisfied or in-flight (dedup keyed on stable `*arr` identity) *(02-02: UNIQUE(arr_app, arr_id) + status-preserving ON CONFLICT upsert; dedup + no-status-clobber proven by `test_dedup_no_duplicate` + `test_upsert_preserves_status`)*
- [ ] **STATE-03**: Curator applies exponential backoff to retries and permanently remembers genuinely-unavailable items (long-TTL dormant re-check) so it stops retrying them

### Acquisition

- [x] **ACQ-01**: Curator triggers slskd searches via REST API for eligible gaps
- [x] **ACQ-02**: Curator initiates the chosen candidate's download via slskd into an isolated per-item staging/quarantine dir and watches it to completion
- [x] **ACQ-03**: Curator handles partial/failed/stalled downloads (timeout, cancel, mark, back off) and never holds a slot forever

### Import & Cleanup

- [x] **IMPORT-01**: Completed downloads land in an isolated per-item staging/quarantine dir on the shared `/data` tree, at a path addressed identically by slskd, Curator, and the `*arr` (hardlink-capable, no cross-FS copy)
- [x] **IMPORT-02**: Curator imports ONLY the wanted files via the `*arr` Manual Import / command API (never a blind drop-folder rescan), telling the `*arr` exactly which release/files to take
- [x] **IMPORT-03**: Curator verifies the item actually imported into the `/volume1` library (re-queries the `*arr`; "downloaded" never counts as "imported")
- [ ] **IMPORT-04**: New media is reflected in Plex — satisfied by the owner's existing Plex "scan on new media" (inotify) auto-scan, an external precondition (Phase-4 decision D-04, 2026-05-31). Curator does NOT call Plex (redundant with auto-scan; avoids a Plex secret in the stack). Revisit with an env-gated trigger only if imports are observed not appearing promptly.
- [x] **IMPORT-05**: After a verified import (or terminal failure) Curator AUTO-PURGES the per-item staging/quarantine dir, so leftover/unwanted files never reach `/volume1` and never need manual deletion; unresolved import failures are reconciled or surfaced, never silently dropped

### Automated Sharing

- [x] **SHARE-01**: Curator configures slskd shares pointing at real (read-only) library content so the account isn't blocked as a leecher
- [x] **SHARE-02**: Sharing stays active and scanned (shared-file count > 0) with no manual intervention

### Reliability & Hands-Off Operation

- [x] **REL-01**: Curator runs continuously as a daemon with a scheduled poll loop and requires no manual triggering
- [x] **REL-02**: Curator self-recovers from transient failures (Lidarr/Readarr/slskd/VPN restarts, network blips) without manual intervention, classifying infra outages separately so they never burn a per-item attempt, and reconciles state on startup (no orphaned in-flight, no double-import)
- [x] **REL-03**: Curator surfaces stuck items (exceeded retries / blocked / unresolved) rather than failing silently

### Observability

- [ ] **OBS-01**: Curator exposes a JSON status endpoint (gap queue, in-flight, stuck counts, VPN/slskd health) consumable by a Homepage `customapi` widget
- [ ] **OBS-02**: Curator sends push notifications via Apprise on grab/import/failure/blocked events (event-driven only, no routine spam)

## v2 Requirements

Deferred to future release. Tracked but not in the current roadmap.

### Quality (deeper)

- **QUAL-04**: Spectral/frequency-cutoff analysis to detect upscaled/transcoded FLAC with high confidence (OUT of v1 scope — heuristic checks only in QUAL-03)

### Sources

- **SRC-01**: Pluggable second source backend (e.g. for book coverage gaps) behind the search/match seam

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Radarr/Sonarr / video support | Soulseek is weak for video; that media is genuinely unavailable, not indexer-missing |
| Soularr dependency | slskd-direct chosen to fix dedup/match/quality at the root (Soularr's weaknesses are the prior pain points); also Lidarr-only, can't do books |
| Spectral-FLAC analysis (v1) | Deferred to QUAL-04; v1 uses heuristic sanity checks only |
| Pure public torrents | Owner prefers SSL/private posture; ruled out |
| Standalone dashboard UI | Status surfaces through the owner's existing Homepage widget (an API contract, not a UI) |
| Web search/browse UI | Reintroduces manual interaction; contradicts hands-off core value |
| Manual approval queue / day-to-day interaction | Contradicts hands-off core value |
| Becoming a primary downloader | Strictly supplementary by design — Usenet always wins first |
| Manual file mapping / manual junk cleanup | The exact labor Curator exists to eliminate (auto-purge handles cleanup) |

## Traceability

Which phases cover which requirements.

| Requirement | Phase | Status |
|-------------|-------|--------|
| INFRA-01 | Phase 1 | Pending |
| INFRA-02 | Phase 1 | Pending |
| INFRA-03 | Phase 1 | Pending |
| INFRA-04 | Phase 1 | Pending |
| INFRA-05 | Phase 1 | Pending |
| INFRA-06 | Phase 1 | Pending |
| STATE-01 | Phase 2 | Complete (02-02 SQLite-WAL ledger + restart-durability) |
| STATE-02 | Phase 2 | Complete (02-02 dedup UNIQUE + status-preserving upsert; 02-04 dedup + status-preservation proven end-to-end through detect_gaps) |
| ARR-01 | Phase 2 | Complete (02-03) |
| ARR-02 | Phase 2 | Complete (02-03; 02-04 Readarr-fault-does-not-gate-music proven end-to-end through detect_gaps) |
| GAP-01 | Phase 2 | Complete (02-03; 02-04 missing detected→persisted end-to-end) |
| GAP-02 | Phase 2 | Complete (02-03; 02-04 cutoff detected→persisted end-to-end) |
| QUAL-01 | Phase 3 | Complete (03-05; adapters return neutral Profile/Manifest, firewall holds over all 8 core modules) |
| QUAL-02 | Phase 3 | Complete (03-04; no-downgrade gate, both REJECT+PERMIT directions proven) |
| QUAL-03 | Phase 3 | Complete (03-04; coarse fake-FLAC heuristics, skip-on-missing-data) |
| MATCH-01 | Phase 3 | Complete (03-03 scorer; 03-05 composed end-to-end through gate.evaluate over the labeled corpus) |
| MATCH-02 | Phase 3 | Complete (03-03 rec-gap recommend; 03-05 composed end-to-end, zero false-accepts over the corpus) |
| ACQ-01 | Phase 4 | Complete (04-04: collection-window search + gate-once; live A1/A3 pin in 04-05) |
| ACQ-02 | Phase 4 | Complete (04-04: enqueue + per-item staging + stall watch composed; 04-05: staging resolved to slskd's real landing dir — staging_root/<leaf-of-remote-folder>, A2 live-pinned) |
| ACQ-03 | Phase 4 | Complete (04-04: no-progress stall cancel + next-candidate fallback + exhausted-stuck, fake-clock proven; 04-05: A3 terminal-state pinned to live `Completed, Succeeded` + robust rule) |
| IMPORT-01 | Phase 4 | Complete (04-04: deterministic per-item staging; hardlink path-identity proven 04-02; 04-05: import+purge now target slskd's real remote-folder-leaf landing dir, A2 live-pinned — no batchId) |
| IMPORT-02 | Phase 4 | Complete (04-04: composed the adapter's pre-filtered subset → execute_import; no blind rescan; 04-05: ManualImport envelope pinned live — importMode lowercase `move`, full QualityModel, no per-file downloadId, A1) |
| IMPORT-03 | Phase 4 | Complete (04-04: verify-by-requery gates the purge; verify-False quarantines) |
| IMPORT-04 | Phase 4 | Precondition (external Plex auto-scan; Curator does not call Plex — D-04 revised 2026-05-31) |
| IMPORT-05 | Phase 4 | Complete (04-04: purge-on-success / quarantine-with-reason on every failure branch) |
| GAP-03 | Phase 5 | Complete (05-04 eligibility grace + 05-02/05-04 per-item queue race check, driven by the live daemon wired in 05-05) |
| STATE-03 | Phase 5 | Complete (05-01 backoff/attempt/dormant DAOs + 05-04 apply_result -> permanently-unavailable, driven by the live daemon wired in 05-05) |
| SHARE-01 | Phase 5 | Complete |
| SHARE-02 | Phase 5 | Complete |
| REL-01 | Phase 5 | Complete (05-04 scheduler daemon + 05-05 lifecycle wiring — the daemon actually starts on app boot) |
| REL-02 | Phase 5 | Complete (05-03 reconcile + INFRA_EXC classifier, wired into _startup in 05-05) |
| REL-03 | Phase 5 | Complete (05-05 GET /status HTML + /status.json, html.escape XSS defense) |
| OBS-01 | Phase 6 | Pending |
| OBS-02 | Phase 6 | Pending |

**Coverage:**

- v1 requirements: 34 total
- Mapped to phases: 34
- Unmapped: 0

---
*Requirements defined: 2026-05-29*
*Last updated: 2026-05-29 after roadmap creation (corrected scope: music + books, staging/auto-purge cleanup layer, *-arr-agnostic adapter; traceability repopulated)*
