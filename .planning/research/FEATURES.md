# Feature Research

**Domain:** Autonomous, fallback-only P2P (Soulseek/slskd) gap-filler for Lidarr (music) + Readarr (books), fully hands-off
**Researched:** 2026-05-29
**Confidence:** MEDIUM-HIGH (behavior verified against Soularr/r:soul/slskd source & issue trackers; exact *arr API payloads flagged needs-validation)

This domain has one widely-used reference implementation — **Soularr** (`mrusse/soularr`, Lidarr↔slskd bridge) — plus its Readarr fork **r:soul** (`insanemal/rsoul`), the **slskd** daemon (Soulseek client with REST API), and the **\*arr** family. "Differentiator" here means *better than running raw Soularr on a cron*, because Soularr is exactly what the owner already tried and abandoned. **Every prior pain point traces to a documented Soularr weakness**, so the literature directly maps onto requirements.

**Prior pain points (referenced as PP#):**
- **PP1** Redundant downloads — *confirmed Soularr bug: it keeps re-matching and re-downloading the same album, filling `failed_imports/` repeatedly* ([soularr #164](https://github.com/mrusse/soularr/issues/164), [#179](https://github.com/mrusse/soularr/discussions/179))
- **PP2** Wrong matches — *Soularr's loose filename-ratio matching (default 0.5) grabs wrong editions; fails when Lidarr monitors a specific edition uncommon on Soulseek* ([soularr #164](https://github.com/mrusse/soularr/issues/164), [#68](https://github.com/mrusse/soularr/issues/68))
- **PP3** Quality downgrades — *Soularr quality prioritization is weak; users request better ranking* ([soularr #161](https://github.com/mrusse/soularr/issues/161))
- **PP4** Complex supplementary config — many fragile knobs (match ratio, formats, countries, interval) in `config.ini`
- **PP5** Import/sync friction — *partial imports leave the album still "wanted," so it re-downloads forever; Lidarr import threshold rejects "reasonable" matches* ([soularr #68](https://github.com/mrusse/soularr/issues/68), [#164](https://github.com/mrusse/soularr/issues/164))

**The single root cause behind PP1 + PP5:** Soularr keeps **no persistent attempt-state**. An item that fails to import stays "wanted" in *arr, so the next 300-second loop re-grabs it. r:soul already proves the fix (persistent state + reconcile-on-restart). Curator must adopt that model as its spine.

---

## Feature Landscape

### Table Stakes (Without these, the hands-off goal fails)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Gap detection from *arr wanted lists** | Curator's whole job is acquiring what *arr says is missing | MEDIUM | Read Lidarr `/api/v1/wanted/missing` + `/api/v1/wanted/cutoff`; Readarr equivalent. Only **monitored** items appear — monitoring is the gate ([servarr wiki](https://wiki.servarr.com/lidarr/wanted)). These two lists ARE the gap universe; do NOT maintain a parallel wishlist. (PP4) |
| **Fallback-only timing ("grace then fallback")** | Usenet must win first; P2P is the gap-filler | MEDIUM-HIGH | An item is a *true gap* only after a grace window where the Usenet path had its chance. See "Gap qualification." This is the core mission feature Soularr lacks (Soularr grabs immediately, default 300s loop). (PP1) |
| **Map *arr item → Soulseek query** | Soulseek has no metadata IDs — you search free-text filenames | MEDIUM | Build queries from artist+album / author+title. Need fallback query forms (drop punctuation, `artist - album`, title-only). (PP2) |
| **Candidate validation (artist/album/edition/track)** | Wrong matches are the #1 Soularr complaint | HIGH | r:soul's pattern: pre-filter on **length ratio + Jaccard token overlap**, match **author/title separately** (not one combined string), validate **embedded file metadata** (epub/mobi/azw3 for books) ([rsoul README](https://github.com/insanemal/rsoul)). Reject below threshold rather than grabbing "close enough." (PP2) |
| **Tracklist completeness check (music)** | A folder with 8/12 tracks → Lidarr import threshold rejects it → infinite re-queue | HIGH | Compare candidate file/track count to the release's expected tracklist *before* downloading. Documented Soularr failure mode ([#68](https://github.com/mrusse/soularr/issues/68)). (PP1, PP5) |
| **Quality enforcement via *arr profiles** | Owner curated quality intentionally; downgrades unacceptable | MEDIUM-HIGH | Read the item's quality profile; filter candidates by inferred format/bitrate (FLAC/320/V0; epub/azw3/pdf). Cutoff-unmet items only replaced by something *above* current quality. Defer to the profile — don't invent rules. (PP3, PP4) |
| **Persistent attempt state (attempted/succeeded/unavailable)** | Prevents re-grabbing/re-searching every loop — the root fix for PP1+PP5 | MEDIUM | SQLite store keyed by *arr entity id. r:soul persists state to disk and reconciles with slskd on restart ([rsoul](https://github.com/insanemal/rsoul)). Soularr's biggest gap. (PP1) |
| **Backoff + "do not retry" memory** | Genuinely-unavailable items must stop consuming cycles/uploads | MEDIUM | Exponential backoff on transient failures; terminal `unavailable` after N exhausted searches. (PP1) |
| **Clean import handoff to *arr** | Files that don't import are invisible to Plex = mission failure | HIGH | Prefer the **ManualImport command API** over drop-folder for deterministic file→release mapping. Note: payload is undocumented and finicky ([lidarr #5647](https://github.com/Lidarr/Lidarr/issues/5647)). (PP5) |
| **Import success confirmation** | "Downloaded" ≠ "in library" — Soularr's blind spot | MEDIUM-HIGH | Re-query *arr after import; only mark `succeeded` when the item left the wanted list. Closes the loop that causes PP1's re-download spiral. (PP5) |
| **slskd share config (anti-leech)** | Soulseek ghosts pure leechers; account dies → all future grabs fail | LOW-MEDIUM | slskd requires sharing **≥1 directory with ≥1 file** to avoid leecher status ([slskd config](https://github.com/slskd/slskd/blob/master/docs/config.md)). Share the read-only library mount. |
| **Daemon/scheduled loop** | Hands-off = no human triggering runs | LOW | Periodic poll of wanted lists, process gaps, sleep (Soularr uses `SCRIPT_INTERVAL`, default 300s). |
| **Self-recovery from failures** | A crash/stuck download must not need human rescue | MEDIUM | Idempotent loop + restart-safe state + reconcile in-flight downloads on boot (r:soul pattern). |
| **VPN-routed source traffic (gluetun+PIA)** | Soulseek peers see your IP; stated infra requirement | MEDIUM | slskd joins gluetun's network namespace. |
| **VPN port-forward sync** | PIA's forwarded port is random per gluetun restart; slskd needs a reachable listen port or peers can't connect | MEDIUM | Read `/tmp/gluetun/forwarded_port` and set `SLSKD_SLSK_LISTEN_PORT` (or update via slskd HTTP API / `VPN_PORT_FORWARDING_UP_COMMAND`) ([gluetun #1966](https://github.com/qdm12/gluetun/discussions/1966), [slskd #1432](https://github.com/slskd/slskd/issues/1432)). A closed port = degraded peering = unhealthy account. |
| **Fail-safe when VPN down** | Kill-switch must not poison state with false `unavailable` marks | MEDIUM | If gluetun is down, detect, back off, resume — never mark items unavailable due to a VPN outage. |

#### Gap qualification ("grace then fallback") — deciding a *true gap*

Soularr's naive rule ("anything wanted = grab now") fights/duplicates the Usenet pipeline (PP1). Fallback-only logic:
1. **Source of truth:** `wanted/missing` (never acquired) + `wanted/cutoff` (have it, below cutoff). Monitored-only ([servarr wiki](https://wiki.servarr.com/lidarr/wanted)).
2. **Grace window:** item must have been wanted ≥ configurable age before Curator acts — the "Usenet wins first" guarantee.
3. **Not in flight elsewhere:** skip if *arr already has an active grab/queue entry from the Usenet client (avoid racing).
4. **Not already `succeeded`/`unavailable`** in Curator's state.

#### Import handoff — ManualImport API vs drop-folder

| Approach | How | Trade-off |
|----------|-----|-----------|
| **Drop folder + rescan** (Soularr-style) | Move files to a watched folder, trigger `DownloadedAlbumsScan`/`RescanFolder` | Simple but *arr's auto-matcher mis-imports/rejects (threshold) → source of PP5 ([#68](https://github.com/mrusse/soularr/issues/68)) |
| **ManualImport command API** (recommended) | POST explicit file→release mapping to `/api/v1/command` ManualImport, then confirm | Deterministic; you tell *arr exactly which release — but payload undocumented, validate per version ([lidarr #5647](https://github.com/Lidarr/Lidarr/issues/5647)) |

Recommend **ManualImport primary, drop-folder fallback**, then poll to confirm. *arr renames into `/volume1` and notifies Plex via its own Plex connection — Curator must NOT talk to Plex directly (anti-feature).

#### Redundant-download state model

SQLite keyed by *arr entity id (+ quality target). Per key: `status` (`pending|searching|downloading|importing|succeeded|unavailable|failed`), `attempts`, `last_attempt_at`, `next_eligible_at` (backoff), `failure_reason` (no-results vs incomplete-tracklist vs quality-too-low vs import-rejected), `slskd_download_id` (resume/cleanup). On boot, **reconcile in-flight downloads with slskd** before acting (r:soul). "Do not retry" = terminal `unavailable`, but with a **dormant long-TTL re-check** since Soulseek catalogs change as users come online.

---

### Differentiators (Better than raw Soularr — why this project exists)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **True fallback ordering (grace window)** | Stops fighting/duplicating the Usenet pipeline | MEDIUM-HIGH | Headline differentiator. Soularr has no concept of this. (PP1) |
| **Strict reject-over-grab matching** | Eliminates wrong-album/edition acquisitions | HIGH | Adopt r:soul's multi-layer validation (Jaccard + length-ratio pre-filter, separate author/title, embedded-metadata check) over Soularr's single filename ratio. Err toward "skip, retry later." (PP2) |
| **Tracklist-complete-only downloads** | No half-albums that *arr rejects and re-queues forever | HIGH | Pre-validate completeness; prefer single-folder full-album candidates. (PP1, PP5) |
| **Quality-aware candidate ranking** | Always the best profile-acceptable file, never a downgrade | MEDIUM-HIGH | Rank by profile-preferred order, not first-found — fixes the gap behind [soularr #161](https://github.com/mrusse/soularr/issues/161). (PP3) |
| **Durable do-not-retry w/ dormant re-check** | Unavailable gaps stop wasting cycles but aren't lost forever | MEDIUM | Soulseek catalog changes over time; periodic re-check beats hard-permanent. (PP1) |
| **Verified closed-loop import** | "Succeeded" means *actually in the Plex-visible library*, not just downloaded | MEDIUM-HIGH | Closes the loop Soularr leaves open and which causes PP1's spiral. (PP5) |
| **Automated share/give-back management** | Account stays healthy with zero owner effort | MEDIUM | Beyond minimal share: keep free upload slots + non-zero upload speed so the account isn't ghosted. Combined with port-forward sync = good-citizen with no manual tuning. |
| **Homepage observability surface** | Glanceable gap queue / in-flight / stuck without logging in | MEDIUM | JSON status endpoint for Homepage's custom API widget (slskd itself has a Homepage widget for precedent — [gethomepage](https://gethomepage.dev/widgets/services/slskd/)). |
| **Push notifications (grab/failure/blocked)** | Hands-off but not blind — pinged only on events worth knowing | LOW-MEDIUM | Notify on import success, repeated-failure, VPN-down/blocked, account-health. Not on routine actions. |
| **Zero-supplementary-config defaults** | Reuses *arr profiles/paths; minimal Curator knobs | MEDIUM | Discover paths/profiles from *arr APIs vs re-declaring. Directly attacks PP4 (Soularr's many `config.ini` knobs). |
| **Stuck-download detection + auto-cleanup** | Self-heals hung slskd transfers (common on Soulseek) | MEDIUM | Timeout, cancel, blacklist that source/candidate, try next. |
| **Pluggable backend seam (optional)** | r:soul proved value of a second source (Stacks) for books | MEDIUM | Not required, but architecting the search/match interface to allow a future source is cheap insurance. Keep OFF by default (scope discipline). |

#### Search & match on Soulseek — how it works

slskd search returns per-user file/folder listings (free text, no IDs) via the search API (`searchText`, `fileLimit`, `responseLimit`, `searchTimeout`); downloads enqueued with filename+size ([slskd-api docs](https://slskd-api.readthedocs.io/en/v0.1.4/api.html)). Pipeline:
1. **Query construction** with fallbacks (punctuation-stripped, `artist - album`, title-only).
2. **Group results by user+folder** (an album lives in one folder).
3. **Score each folder:** artist/title fuzzy match, year/edition hints, format/quality, **track-count vs expected**, peer health (free slots, speed, queue length).
4. **Threshold reject** below confidence — no best-of-bad.
5. **Pick highest-scoring, profile-acceptable, complete** candidate; prefer users with free slots to avoid stuck queues.

Editions are the hard part (deluxe/standard/remaster) — Soularr's documented failure when Lidarr monitors a specific edition ([#164](https://github.com/mrusse/soularr/issues/164)). Year + track-count are the main disambiguating signals.

#### slskd share / give-back specifics
- Configure + scan ≥1 shared directory containing ≥1 file ([slskd config](https://github.com/slskd/slskd/blob/master/docs/config.md)).
- Pure leechers get ghosted/banned over time → source starvation.
- Practical: share a read-only mount of the library Curator builds, keep some free upload slots, non-zero upload speed, reachable port (port-forward sync). Hands-off "good citizen."

---

### Anti-Features (Commonly tempting, but they reintroduce labor or scope creep)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Manual approval / interactive prompts** | "Let me confirm matches" | Reintroduces the exact labor Curator removes | Fully automatic; surface via notifications/Homepage |
| **Curator-owned wishlist / "what to get" UI** | Central control | Owner already curated in *arr; duplication → drift + PP4 | *arr wanted lists are the only source of truth |
| **Grab immediately (Soularr default)** | Faster fills | Fights/duplicates the Usenet primary path → PP1 | Enforce grace window; fallback-only |
| **"Close enough"/first-match download** | More hits | Direct cause of PP2 wrong matches | Reject below threshold; retry later |
| **Half-album / partial downloads** | Partial > nothing | *arr won't import; infinite re-queue → PP1/PP5 | Tracklist-complete-only |
| **Curator-defined quality rules** | Fine-grained control | Diverges from *arr profiles → PP3/PP4 | Defer entirely to *arr quality profiles/cutoffs |
| **Direct Plex API integration** | Instant library refresh | Out of scope; *arr already notifies Plex; extra moving part | Let *arr import + Plex's own watch reflect changes |
| **Radarr/Sonarr/video/Whisparr** | "Do it all" | Scope creep; video genuinely unavailable on Soulseek (per PROJECT.md) | Music (Lidarr) + Books (Readarr) only |
| **Acting as primary acquirer** | Maximize fills | It's a gap-filler, not a Usenet replacement | Only act post-grace on items the primary path missed |
| **Re-implementing rename/library layout** | Custom paths | *arr already renames into `/volume1` | Hand files to *arr |
| **Aggressive infinite retry on unavailable** | Eventually it'll appear | Wastes cycles, hammers Soulseek, hurts account → PP1 | Backoff → dormant `unavailable` + periodic re-check |
| **Bypassing VPN "for speed"** | Faster transfers | Leaks Soulseek IP; violates infra requirement | All source traffic via gluetun; fail-safe if down |
| **Manual share/ratio tuning** | Optimize ratio | Reintroduces labor | Automate share + free upload slots |
| **Storing media in a non-*arr-managed path** | Simpler drop | Causes PP5 import failures | ManualImport API into *arr-managed root |

---

## Feature Dependencies

```
*arr API client (read wanted/profiles/paths)
    └──> Gap detection
            └──> Gap qualification (grace window)
                    └──requires──> Persistent state store  [THE SPINE]

slskd API client
    └──> Search ──> Candidate validation (match + tracklist + quality filter)
                        └──> Download (slskd)
                                └──> Import handoff (ManualImport API)
                                        └──> Import confirmation
                                                └──> State update (succeeded) ──> Notify

gluetun+PIA ──> (slskd netns) ──> Port-forward sync ──> Share/give-back config
    └──> VPN-health detection ──enhances──> fail-safe loop control

Persistent state store ──> Observability endpoint ──> Homepage widget
Persistent state store ──> Backoff/do-not-retry ──> Daemon loop ──> Self-recovery (reconcile)
All terminal/error events ──> Push notifications

Candidate validation ──conflicts──> "first-match/close-enough" grabbing (anti-feature)
Grace window ──conflicts──> "grab immediately" (anti-feature)
```

### Dependency Notes
- **Persistent state store is the spine.** Gap qualification, backoff, observability, and self-recovery all read/write it. Build first — it's the root fix for PP1+PP5 that Soularr lacks.
- **Candidate validation gates everything downstream** and is the highest-complexity, highest-risk item (fixes PP2). Spike it early against real Soulseek results.
- **Import confirmation depends on the ManualImport payload shape** — undocumented; validate before committing the design (PP5).
- **VPN-health detection must wrap the slskd client** — otherwise a VPN drop poisons state with false `unavailable`.
- **Share/give-back depends on port-forward sync** for peer reachability.
- **Self-recovery depends on reconcile-on-restart** against slskd (r:soul pattern) so restarts don't duplicate in-flight downloads.

---

## MVP Definition

### Launch With (v1)
- [ ] **State store + *arr read client + gap detection** — the spine; "what would I work on" (PP1)
- [ ] **Gap qualification (grace window)** — proves fallback-only on real data (PP1)
- [ ] **slskd search + candidate validation (match + tracklist + quality)** — the hard, risky core (PP1/PP2/PP3); spike heavily
- [ ] **Download + ManualImport handoff + import confirmation** — closes the loop (PP5)
- [ ] **Backoff / do-not-retry + daemon loop + reconcile-on-restart** — makes it hands-off (PP1)
- [ ] **VPN integration (gluetun+PIA, kill-switch-safe, port-forward sync) + minimal share** — privacy + account survival

### Add After Validation (v1.x)
- [ ] **Observability endpoint + Homepage widget** — trigger: core pipeline reliably importing
- [ ] **Push notifications** — trigger: enough volume that polling logs is tedious
- [ ] **Dormant re-check of `unavailable`** — trigger: confirmed false-negatives appearing later
- [ ] **Stuck-download auto-cleanup tuning** — trigger: observed hung transfers

### Future Consideration (v2+)
- [ ] **Pluggable second backend** (e.g. Stacks for books, per r:soul) — defer until Soulseek coverage gaps are proven
- [ ] **Edition disambiguation refinement** — defer; start with artist+album+trackcount+year, refine after observing real mismatches

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Persistent state store | HIGH | MEDIUM | P1 |
| Gap detection + qualification (grace) | HIGH | MEDIUM | P1 |
| Candidate validation (match/tracklist/quality) | HIGH | HIGH | P1 |
| ManualImport handoff + confirmation | HIGH | HIGH | P1 |
| Backoff / do-not-retry + daemon loop | HIGH | MEDIUM | P1 |
| VPN + port-forward sync + share | HIGH | MEDIUM | P1 |
| Self-recovery / reconcile-on-restart | HIGH | MEDIUM | P1 |
| Observability (Homepage endpoint) | MEDIUM | MEDIUM | P2 |
| Push notifications | MEDIUM | LOW | P2 |
| Dormant re-check of unavailable | MEDIUM | LOW | P2 |
| Stuck-download auto-cleanup | MEDIUM | MEDIUM | P2 |
| Pluggable second backend | LOW | MEDIUM | P3 |
| Edition disambiguation refinement | MEDIUM | HIGH | P3 |

## Competitor Feature Analysis

| Feature | Soularr (Lidarr) | r:soul (Readarr fork) | Curator's Approach |
|---------|------------------|------------------------|--------------------|
| Trigger | Grab immediately, 300s loop | Same lineage | **Grace-then-fallback** (Usenet wins first) |
| Matching | Single filename ratio (0.5 default) → PP2 | Jaccard + length-ratio + separate author/title + metadata validation | Adopt r:soul-style multi-layer; reject-over-grab |
| Tracklist completeness | Weak → partial imports (PP5) | Better (book = single file) | Pre-validate full tracklist (music) |
| Quality ranking | Weak (PP3) | Configurable | Defer to *arr profile, rank by preferred order |
| Persistent state | **None → re-downloads (PP1)** | Persists + reconcile on restart | SQLite spine + reconcile (the core fix) |
| Import | Drop/scan, blind | Auto-import w/ path mapping | ManualImport API + **confirmation** |
| Share/give-back | Manual (slskd) | Manual (slskd) | Automated share + port-forward sync |
| Observability | 3rd-party dashboard | Minimal | Homepage endpoint + push notifications |
| Scope | Music | Books | **Both**, video explicitly excluded |

## Sources

- Soularr: [repo](https://github.com/mrusse/soularr), [README](https://github.com/mrusse/soularr/blob/main/README.md) — MEDIUM-HIGH (primary reference impl)
- Soularr pain-point issues: [#164 partial imports/redundant](https://github.com/mrusse/soularr/issues/164), [#179](https://github.com/mrusse/soularr/discussions/179), [#68 import threshold rejection](https://github.com/mrusse/soularr/issues/68), [#161 download priorities/quality](https://github.com/mrusse/soularr/issues/161) — HIGH (direct evidence of PP1/PP2/PP3/PP5)
- r:soul (Readarr fork, better matching/state model): [repo](https://github.com/insanemal/rsoul) — MEDIUM-HIGH
- slskd: [config docs](https://github.com/slskd/slskd/blob/master/docs/config.md), [repo](https://github.com/slskd/slskd), [python API docs](https://slskd-api.readthedocs.io/en/v0.1.4/api.html), [Homepage widget](https://gethomepage.dev/widgets/services/slskd/) — MEDIUM-HIGH (leecher rule, search/download API)
- Lidarr wanted/import: [servarr wiki — wanted](https://wiki.servarr.com/lidarr/wanted), [import troubleshooting](https://wiki.servarr.com/lidarr/import-troubleshooting), [ManualImport API gap #5647](https://github.com/Lidarr/Lidarr/issues/5647) — MEDIUM (UI documented; exact API payload **needs-validation**)
- VPN/port-forward: [gluetun+slskd #1966](https://github.com/qdm12/gluetun/discussions/1966), [slskd listen-port-at-runtime #1432](https://github.com/slskd/slskd/issues/1432) — MEDIUM-HIGH (three established sync patterns)
- SoulSync (alt music tool, context): [repo](https://github.com/Nezreka/SoulSync) — LOW (could not fully retrieve internals)

**Needs-validation in an early spike:** exact ManualImport command payload per *arr version; exact `wanted/cutoff` + "in-flight elsewhere" detection fields; slskd share/port HTTP-API endpoints; PIA forwarded-port retrieval mechanics with current gluetun.

---
*Feature research for: Autonomous Soulseek/slskd gap-filler for Lidarr+Readarr*
*Researched: 2026-05-29*
