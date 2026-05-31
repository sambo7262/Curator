# Roadmap: Curator

**Created:** 2026-05-29
**Granularity:** standard
**Core Value:** Anything already monitored in Lidarr (music) or Readarr (books) that the Usenet pipeline can't get is acquired automatically — correctly matched, at the right quality, with no redundant downloads, no leftover junk on the volume, and zero manual interaction.

## North Star

A daemon that runs untouched for N days and keeps filling Lidarr/Readarr gaps from Soulseek — correctly matched, profile-quality, deduped, VPN-private, with every download staged-then-cleaned so nothing unwanted ever lands on `/volume1`, and status visible on Homepage. Built as horizontal layers (privacy/networking foundation → state + adapter + gap detection → matching + quality → acquisition + staging + clean import + auto-purge → autonomy/sharing/resilience → observability) so value lands when the full loop closes. Music is the reliability backbone; books ride the same loop behind a pluggable adapter, best-effort, without ever gating music.

## Phases

- [x] **Phase 1: VPN-Routed Networking Foundation** - gluetun+PIA tunnel (kill-switch, non-US PF, firewall subnets), slskd routed through it and reachable on synobridge, single shared `/data` mount, deployed from compose pulling a CI-built image ✓ *deployed & verified on NAS 2026-05-30 (slskd logged into Soulseek via PIA Vancouver, port 56034)*
- [x] **Phase 2: State Ledger + *arr Adapter + Gap Detection** - SQLite spine plus a `*-arr`-agnostic adapter that detects monitored missing/cutoff gaps and dedups them, with Readarr isolated behind the seam ✓ *all 4 plans complete 2026-05-30; detection wired end-to-end with dedup + Readarr-no-gate proven*
- [x] **Phase 3: Matching & Quality Gating** - candidates scored against authoritative identity and filtered by `*arr` profile/cutoff (incl. fake-FLAC heuristics), rejecting wrong/low-quality matches before any download ✓ *all 5 plans complete 2026-05-30; gate.evaluate composes the corpus end-to-end (QUAL-02 both directions), firewall holds over all 8 core modules*
- [x] **Phase 4: Acquisition, Staging & Clean Import** - eligible gaps are searched, downloaded into an isolated quarantine dir, imported (wanted files only) via Manual Import, verified into the library, then the staging dir is auto-purged ✓ *all 5 plans complete 2026-05-31; the single-item acquisition loop is pinned to the live slskd/Lidarr reality (A1 ManualImport `move` envelope, A2 remote-folder-leaf landing, A3 `Completed, Succeeded` terminal rule), offline suite 205 passed, firewall intact*
- [ ] **Phase 5: Autonomy, Sharing & Self-Recovery** - the grace-gated daemon runs hands-off with backoff/do-not-retry, slskd shares real library content to stay unblocked, and the loop self-recovers and surfaces stuck items
- [ ] **Phase 6: Observability & Notifications** - a Homepage-consumable JSON status endpoint and Apprise push notifications make the hands-off system glanceable and event-aware

## Phase Details

### Phase 1: VPN-Routed Networking Foundation
**Goal**: A privacy-safe, reproducibly-deployed source substrate exists — slskd's traffic exits only through a gluetun+PIA tunnel with an auto-synced forwarded port, the `*arr` stack stays reachable from inside the tunnel, all four containers share one identical `/data` tree for atomic imports, and the stack comes up from a single compose file pulling a CI-built Docker Hub image.
**Depends on**: Nothing (first phase)
**Requirements**: INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06
**Success Criteria** (what must be TRUE):
  1. From inside slskd's namespace the public IP is the PIA IP (not the home IP) with no DNS leak; if gluetun drops, slskd has zero network path (fail-closed, kill-switch verified).
  2. gluetun (non-US PF region) obtains a forwarded port and slskd self-applies it as its Soulseek listen port — re-syncing automatically after a container/NAS restart (verified, not just on first boot).
  3. Curator on `synobridge` reaches Lidarr/Readarr/Plex by container name AND slskd via `http://gluetun:5030` (FIREWALL_OUTBOUND_SUBNETS confirmed correct); a `git push` builds and pushes a `linux/amd64` image to Docker Hub with no secrets baked in.
  4. `docker compose up` from one YAML brings the stack online with config/state/downloads on `/volume1`, and a file written by slskd into the shared `/data` tree is hardlink-capable (not a cross-FS copy) and readable/movable by the `*arr` PUID/PGID.
**Plans**: 4 plans
- [ ] 01-01-PLAN.md — Wave 0 NAS recon (CIDR/PUID-PGID/*arr-mount/hardlink/tun) + secrets bootstrap (.env.example/.gitignore) + image digests + gluetun API key [INFRA-06]
- [ ] 01-02-PLAN.md — gluetun+slskd VPN stack: PIA OpenVPN, kill-switch, control-server auth, native PF auto-sync; egress/fail-closed/PF-restart smoke [INFRA-01, INFRA-02, INFRA-03]
- [ ] 01-03-PLAN.md — Curator FastAPI health/status stub + Dockerfile + GitHub Actions linux/amd64 -> Docker Hub (no baked secrets) [INFRA-05]
- [ ] 01-04-PLAN.md — single-compose assembly (curator + synobridge) + runnable smoke-test.sh + Go/No-Go gate [INFRA-03, INFRA-04, INFRA-06]

### Phase 2: State Ledger + *arr Adapter + Gap Detection
**Goal**: The persistent spine and the integration seam exist before any acquisition: a SQLite (WAL) ledger is the source of truth for "should I act on this gap?", and a `*-arr`-agnostic adapter detects monitored missing and cutoff-unmet items from Lidarr (and, behind the same isolated interface, Readarr) and upserts them deduped — so the same gap is never tracked twice and Readarr's quirks can't reach the core.
**Depends on**: Phase 1
**Requirements**: STATE-01, STATE-02, ARR-01, ARR-02, GAP-01, GAP-02
**Success Criteria** (what must be TRUE):
  1. Each tracked gap persists in SQLite with a lifecycle status keyed on stable `*arr` identity, surviving a container restart intact.
  2. Curator detects both monitored missing (wanted/missing) and cutoff-unmet (wanted/cutoff) items from Lidarr through the adapter.
  3. The adapter exposes item identity + quality profile/cutoff uniformly and treats Readarr as a pluggable module — feeding Readarr garbage/empty metadata degrades gracefully (book item skipped, logged) without crashing or stalling the music loop.
  4. Re-running gap detection on an already-tracked or already-satisfied item does not create a duplicate ledger entry (dedup proven).
**Plans**: 4 plans
- [x] 02-01-PLAN.md — Wave 0: offline *arr JSON fixtures + conftest + dev deps, config.py/package markers, /db mount + DB_PATH wiring [STATE-01] ✓ 2026-05-30
- [x] 02-02-PLAN.md — SQLite-WAL ledger: schema (items, UNIQUE dedup, status CHECK) + idempotent migrations + status-preserving upsert repo + startup hook [STATE-01, STATE-02] ✓ 2026-05-30
- [x] 02-03-PLAN.md — *-arr-agnostic adapter seam: ArrAdapter Protocol + GapItem, LidarrAdapter (missing+cutoff), defensive ReadarrAdapter + circuit breaker [ARR-01, ARR-02, GAP-01, GAP-02] ✓ 2026-05-30
- [x] 02-04-PLAN.md — gap_detector wiring: detect_gaps adapters→ledger, end-to-end dedup + Readarr-fault-does-not-gate-music proofs, manual one-shot trigger [GAP-01, GAP-02, STATE-02, ARR-02] ✓ 2026-05-30

### Phase 3: Matching & Quality Gating
**Goal**: Curator decides what is worth grabbing before spending a download: it scores slskd candidates against the item's authoritative identity (artist/album, track-count completeness, edition/year, format; author/title/format for books), reads the `*arr` quality profile/cutoff, filters candidates to profile-acceptable formats/bitrates, applies fake/transcoded-FLAC heuristics, and refuses anything below the confidence threshold — precision over recall.
**Depends on**: Phase 2
**Requirements**: QUAL-01, QUAL-02, QUAL-03, MATCH-01, MATCH-02
**Success Criteria** (what must be TRUE):
  1. Given multiple slskd candidates for a gap, Curator scores them on authoritative identity (incl. track-count completeness) and selects the best match.
  2. When no candidate clears the configurable confidence threshold, Curator declines rather than grabbing a wrong/incomplete match.
  3. Curator reads the item's `*arr` quality profile/cutoff and filters out any candidate below cutoff before downloading (no downgrades).
  4. A FLAC candidate failing bitrate/size/source-tag sanity heuristics is rejected before download.
**Plans**: 5 plans
- [x] 03-01-PLAN.md — Wave 0: Candidate/CandidateFile/Manifest contract dataclasses + pure release_parse tokenizer + labeled fixture corpus (test-first) [MATCH-01] ✅ complete
- [x] 03-02-PLAN.md — Wave 0: rapidfuzz package-legitimacy human-verify + pin + Settings.from_env() threshold/weight/fake-FLAC-floor tunables [MATCH-02, QUAL-03] ✅ complete
- [x] 03-03-PLAN.md — Wave 1: ported beets weighted-distance matcher (score + rec-gap recommend) + zero-false-accept corpus calibration [MATCH-01, MATCH-02] ✅ complete
- [x] 03-04-PLAN.md — Wave 1: neutral Profile/QualityRank + no-downgrade cutoff gate (both QUAL-02 directions) + coarse fake-FLAC heuristics (skip-on-missing) [QUAL-01, QUAL-02, QUAL-03] ✅ complete
- [x] 03-05-PLAN.md — Wave 2: gate.py composition + dumb selector + adapter get_quality_profile/get_manifest normalization + extended firewall grep + end-to-end corpus proof [QUAL-01, MATCH-01, MATCH-02] ✅ complete

### Phase 4: Acquisition, Staging & Clean Import
**Goal**: Close the loop for a real gap: Curator triggers an slskd search, downloads the chosen candidate into an isolated per-item staging/quarantine dir on the shared tree, imports ONLY the wanted files via the `*arr` Manual Import API, verifies the item left the wanted list into `/volume1`, confirms Plex reflects it — then auto-purges the staging dir so no leftover/unwanted files ever reach the library or need manual deletion. Partial/stalled transfers are timed out and cleaned.
**Depends on**: Phase 3
**Requirements**: ACQ-01, ACQ-02, ACQ-03, IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-04, IMPORT-05
**Success Criteria** (what must be TRUE):
  1. For a selected gap, Curator searches slskd, downloads the chosen candidate into an isolated per-item staging dir (identical path across containers, hardlink-capable), and watches it to completion.
  2. Curator imports only the wanted files via the `*arr` Manual Import/command API (not a blind rescan) and verifies the item actually imported into the `/volume1` library and is reflected in Plex.
  3. After a verified import — or a terminal failure — the per-item staging/quarantine dir is auto-purged, leaving zero leftover/unwanted files on `/volume1` and requiring no manual deletion.
  4. A partial/stalled/failed download is timed out, cancelled, its staging dir cleaned, and the failure recorded/surfaced rather than hanging a slot or silently dropping.
**Plans**: 5 plans (3 build waves + 1 live-verification wave)
- [x] 04-01-PLAN.md — Wave 0: migration 0002 (status enum widen + staged_files) + staged_files DAOs + Phase-4 config tunables + offline slskd/manualimport fixtures [IMPORT-05, ACQ-02] ✓ (139 passed)
- [x] 04-02-PLAN.md — Wave 0: thin SlskdClient (search/enqueue/watch/cancel, X-API-Key) + staging.py path-traversal guard + purge/quarantine/TTL helpers [ACQ-01, ACQ-02, ACQ-03, IMPORT-01, IMPORT-05] ✓ (172 passed)
- [x] 04-03-PLAN.md — Wave 1: *arr-agnostic import methods (Lidarr ManualImport-Move + verify-by-requery; Readarr best-effort swallow->safe-default). NO Plex (D-04 revised — Curator does not call Plex; IMPORT-04 is the owner's external auto-scan precondition) [IMPORT-02, IMPORT-03, IMPORT-05] ✓ (185 passed)
- [x] 04-04-PLAN.md — Wave 2: core/acquire.py composition loop (search→gate→download→stall-watch→import→verify→purge/quarantine, D-01..D-10; NO Plex — IMPORT-04 external per revised D-04) + firewall grep over acquire.py [ACQ-01/02/03, IMPORT-01/02/03/05] ✓ (201 passed)
- [x] 04-05-PLAN.md — Wave 3: D-11 slskd-share precondition checkpoint + live NAS probes (A1 ManualImport envelope / A2 batchId / A3 transfer-state strings) + reconcile fixtures to reality [ACQ-02, ACQ-03, IMPORT-01, IMPORT-02] ✓ (205 passed) — A3 success=`Completed, Succeeded`+robust rule; A2 no-batchId→staging_root/<leaf-of-remote-folder>; A1 importMode lowercase `move`, no per-file downloadId

### Phase 5: Autonomy, Sharing & Self-Recovery
**Goal**: Make the closed loop run itself, indefinitely, and politely: a scheduled daemon processes only grace-elapsed, Usenet-clear gaps; exponential backoff plus permanent "unavailable" memory govern retries; slskd shares real read-only library content so the account is never leech-blocked; and the system self-recovers from transient Lidarr/Readarr/slskd/VPN outages (infra failures never burn an attempt) and surfaces genuinely-stuck items.
**Depends on**: Phase 4
**Requirements**: GAP-03, STATE-03, SHARE-01, SHARE-02, REL-01, REL-02, REL-03
**Success Criteria** (what must be TRUE):
  1. Curator runs continuously as a daemon and acts on a gap only after its configurable grace window elapses AND no active/queued Usenet grab exists (fallback-only, never races; a SABnzbd-active item is not grabbed).
  2. Failed attempts back off exponentially and genuinely-unavailable items are permanently remembered (dormant long-TTL re-check) so retries stop hammering the network.
  3. slskd shares real (read-only) library content with shared-file count > 0 and stays active/scanned across restarts with no manual intervention.
  4. After a forced VPN/slskd/`*arr` outage or a kill mid-transfer, Curator resumes on its own with no orphaned in-flight items and no double-import (infra outage consumed no per-item attempt), and any item that exceeds retries / is blocked is surfaced rather than failing silently.
  5. Hands-off test: across N consecutive days with zero manual actions, gaps continue to fill and no manual file mapping, junk cleanup, or container babysitting is ever required.
**Plans**: 5 plans (4 build waves)
- [x] 05-01-PLAN.md — Wave 0: migration_0003 (attempt/backoff/dormant cols + permanently-unavailable status) + repo eligibility/backoff/counts DAOs + Phase-5 config tunables + state-side test scaffolds [STATE-03, GAP-03] ✓ (18 new 05-01 tests; 296 passed excl. out-of-scope scheduler leak)
- [ ] 05-02-PLAN.md — Wave 0: slskd shares ensure/self-heal client methods + Lidarr/Readarr get_queue_status (D-02 race check) + acquire.py INFRA_EXC classifier (A1) + FakeSlskd/fixtures [SHARE-01, SHARE-02, GAP-03, REL-02]
- [ ] 05-03-PLAN.md — Wave 1: core/shares.py ensure/self-heal cycle + core/reconcile.py startup orphan reset with verify-by-requery double-import guard (infra burns no attempt) [SHARE-01, SHARE-02, REL-02]
- [ ] 05-04-PLAN.md — Wave 2: detect_gaps batch transaction (D-15) + core/scheduler.py daemon loop (kill-switch/dry-run/MAX_CONCURRENT, eligibility dispatch, backoff->permanently-unavailable, single-writer LockedConn) [REL-01, GAP-03, STATE-03]
- [ ] 05-05-PLAN.md — Wave 3: scheduler+reconcile lifecycle wiring + GET /status HTML (escaped) + /status.json + A2/A3 live-confirm checkpoint + DEPLOY.md staged-rollout/kill-switch [REL-03, REL-01, SHARE-02, GAP-03]

### Phase 6: Observability & Notifications
**Goal**: Give the owner a glanceable, event-aware view of an otherwise invisible system without reintroducing a control surface: a flat JSON status endpoint feeds the existing Homepage `customapi` widget (gap queue / in-flight / stuck / imported-24h / VPN+slskd health), and Apprise pushes notifications only on meaningful state transitions (grab / import / failure / blocked / stuck).
**Depends on**: Phase 5
**Requirements**: OBS-01, OBS-02
**Success Criteria** (what must be TRUE):
  1. A JSON status endpoint exposes gap-queue, in-flight, and stuck counts (plus VPN/slskd health) and renders correctly in a Homepage `customapi` widget.
  2. Apprise pushes a notification on grab/import/failure/blocked/stuck events and stays quiet during routine, uneventful polling (no spam).
**Plans**: TBD

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. VPN-Routed Networking Foundation | 4/4 | ✓ Complete (deployed & verified on NAS) | 2026-05-30 |
| 2. State Ledger + *arr Adapter + Gap Detection | 4/4 | ✓ Complete | 2026-05-30 |
| 3. Matching & Quality Gating | 5/5 | ✓ Complete | 2026-05-31 |
| 4. Acquisition, Staging & Clean Import | 5/5 | ✓ Complete (live-probe reconciliation pinned A1/A2/A3; 205 passed) | - |
| 5. Autonomy, Sharing & Self-Recovery | 1/5 | In progress (05-01 ✓; Wave 0) | - |
| 6. Observability & Notifications | 0/0 | Not started | - |

## Coverage

All 34 v1 requirements mapped to exactly one phase. No orphans, no duplicates.

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1 | INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, INFRA-06 | 6 |
| 2 | STATE-01, STATE-02, ARR-01, ARR-02, GAP-01, GAP-02 | 6 |
| 3 | QUAL-01, QUAL-02, QUAL-03, MATCH-01, MATCH-02 | 5 |
| 4 | ACQ-01, ACQ-02, ACQ-03, IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-04, IMPORT-05 | 8 |
| 5 | GAP-03, STATE-03, SHARE-01, SHARE-02, REL-01, REL-02, REL-03 | 7 |
| 6 | OBS-01, OBS-02 | 2 |
| **Total** | | **34** |

## Notes on UI

No phase carries a `**UI hint**: yes`. Per project scope there is no standalone dashboard or user-facing interface. Status surfaces through the owner's existing Homepage via a `customapi` JSON endpoint (OBS-01) — an API contract, not a traditional UI. slskd ships its own web UI, but Curator neither builds nor owns it.

## Books (Readarr) — best-effort, never gates music

Books are in v1 scope but are not a separate phase. They ride the same horizontal layers through the `*-arr`-agnostic adapter (ARR-01/ARR-02): once the music loop is closed and hands-off, the Readarr branch is enabled best-effort behind the seam. Per the Readarr-retirement hedge, books must degrade gracefully and never block, slow, or destabilize the music backbone.

## v2 (Out of Roadmap)

Deferred — not covered by these phases: QUAL-04 (spectral/frequency-cutoff FLAC analysis), SRC-01 (pluggable second source backend).

---
*Roadmap created: 2026-05-29 (corrected: horizontal-layers mode, music + books scope, staging/auto-purge cleanup layer, *-arr-agnostic adapter)*
