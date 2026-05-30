# Feature Landscape

**Domain:** Autonomous, fallback-only Soulseek (slskd) gap-filler for Lidarr/Readarr — hands-off acquisition of music/books the Usenet pipeline can't get.
**Researched:** 2026-05-29
**Overall confidence:** MEDIUM (see note)

> **Research environment note:** External network (WebSearch/WebFetch/curl) was unavailable in this sandbox; all tool calls returned empty. Findings below are drawn from domain knowledge of the *arr ecosystem, slskd, Soularr, and the Soulseek protocol (training cutoff Jan 2026). Items that are **version-sensitive or fast-moving are explicitly flagged for verification** before requirements lock. The single most important verify-first item is the **Readarr deprecation status** (see Pitfall callout under "Books").

---

## Critical Cross-Cutting Finding: Readarr Is Retired — VERIFY FIRST

**[CONFIDENCE: MEDIUM — VERIFY BEFORE ROADMAP LOCK]** As of early 2025 the Servarr team announced Readarr is **no longer actively developed / archived**, primarily due to book metadata source instability. This directly threatens the "books" half of Curator.

Implications you must resolve before building:
- The Readarr API may still function against an existing instance, but **metadata refresh / search for new editions may be unreliable or broken**.
- Community forks exist (e.g. attempts to replace the metadata server). Their API compatibility varies.
- **Recommendation:** Treat **music (Lidarr) as the primary, must-work target** and **books (Readarr) as a secondary, best-effort target gated behind a feature flag.** Design the gap-detection and import-handoff layers as *-arr-agnostic adapters so a Readarr replacement can be swapped in. Do NOT let book-specific brokenness block the music path from shipping.

This reframes MVP: **ship music end-to-end first; books second.**

---

## Table Stakes

Features without which the hands-off goal fails. Each is mapped to the prior pain point(s) it addresses: **(P1)** redundant downloads, **(P2)** wrong matches, **(P3)** quality downgrades, **(P4)** complex supplementary config, **(P5)** import/sync friction.

| Feature | Why Expected (pain) | How it typically works | Complexity | Notes |
|---------|--------------------|------------------------|------------|-------|
| **Gap detection from *arr wanted/cutoff lists** | Core purpose | Poll Lidarr `GET /api/v1/wanted/missing` + `GET /api/v1/wanted/cutoff` (and Readarr equivalents). These are the canonical "monitored + not satisfied" lists — the *arr already computed the gap. | Low | Don't recompute "what's wanted" — defer entirely to *arr's own logic. Paginate; cache release IDs. |
| **Grace-then-fallback timing (Usenet wins first)** | Fallback-only mandate | Only treat an item as a *true gap* after it has been wanted for ≥ a configurable grace period (e.g. N days) AND the *arr's normal indexers have had their search cycle. Soulseek is last resort. | Medium | Implement as a per-item "first seen wanted" timestamp in Curator state; eligible only after grace elapses. Optionally read *arr history to confirm no recent successful/pending grab. **(P3 indirectly: avoids grabbing a worse P2P copy before a good Usenet copy lands.)** |
| **Map *arr item → Soulseek search** | Core (P2) | Build queries from artist+album (music) / author+title (books). slskd search is filename/folder-text based, not metadata-based — results are messy. Issue multiple query variants (with/without year, normalized punctuation/featuring tags). | Medium | Soulseek returns *file lists per user*, grouped by folder. You match on the *folder/file set*, not a clean record. |
| **Candidate validation & match scoring** | Avoid wrong matches (P2) | Validate against the *arr's known tracklist: expected track count, fuzzy-match track titles, album/edition, duration if available. Score candidates by (completeness × name similarity × quality × uploader health). Reject below a confidence threshold rather than grabbing the "best of bad." | High | This is the #1 differentiator-quality area but is *table stakes* to not regress Soularr. Use normalized string distance + track-count gating. **Refusing to download is a valid, correct outcome.** |
| **Quality enforcement via *arr profiles (no downgrades)** | No downgrades (P3) | Read the *arr Quality Profile + cutoff for the item; filter slskd candidate files by inferred format/bitrate from filename/extension (FLAC vs 320 vs V0 vs lossy). Never grab below the profile's allowed/cutoff quality. For cutoff-unmet, only grab if candidate *beats* current. | High | slskd exposes file size/extension/bitrate hints; bitrate is often inferable from extension+size or slsk metadata but is **not always reliable** — must be defensive. Defer the *policy* to *arr; Curator only enforces it pre-download. |
| **Redundant-download prevention (persistent state)** | Redundant downloads (P1) | Durable store keyed by *arr release/item ID with status: `pending` → `searching` → `downloading` → `imported` / `unavailable` / `failed`. Never re-search an `imported` or recently-`unavailable` item. | Medium | This is *the* fix for the most-cited Soularr pain. See "State Model" section below. |
| **Exponential backoff + "do not retry" memory** | Redundant downloads (P1) | Failed/unavailable items get a backoff timer (e.g. 1d → 3d → 7d → 30d) and a max-attempts cap after which they go `dormant` until *arr signals the want changed. | Medium | Avoids hammering Soulseek for genuinely unavailable obscure items. Reset on *arr edition/monitoring change. |
| **Clean import handoff to *arr** | Import/sync friction (P5) | Two viable approaches (see below). Drop completed files into a path the *arr watches, then trigger `POST /api/v1/command {name: DownloadedAlbumsScan / RescanFolders}`; or use the **Manual Import API** to map files explicitly. Confirm import succeeded by re-checking item state / *arr history. | High | Must let *arr do rename + move into `/volume1` so Plex sees correctly named files. **Confirm-import is mandatory** — fire-and-forget rescan is a known friction source. |
| **slskd share configuration (not a leecher)** | Sharing/give-back | Configure slskd `shares` so the account uploads. Soulseek community/servers penalize zero-share leechers (queue deprioritization, bans). Point shares at the imported library (read-only) or a dedicated share dir. | Low–Medium | One-time config + ensure upload slots/limits set so the account stays in good standing. **(operational survival — without it, searches degrade over time.)** |
| **VPN-routed source traffic (gluetun + PIA)** | Privacy | Run slskd's network egress through gluetun. Soulseek needs an inbound listening port → use PIA **port forwarding** and sync the forwarded port into slskd's listen port. Kill-switch ensures no traffic leaks if VPN drops. | Medium | Port-forward sync is the operationally tricky part (PIA's forwarded port can change on reconnect). slskd behind `network_mode: service:gluetun`. |
| **Status surface for Homepage** | Observability | Expose gap queue size, in-flight downloads, stuck/failed counts via a small HTTP endpoint (JSON) or Homepage's custom API widget. | Low–Medium | Homepage can read a custom JSON endpoint; design a stable `/status` schema early. |
| **Push notifications on grab / failure / blocked** | Observability | On state transitions (grabbed, imported, failed-final, VPN-down/blocked) send to a notifier (ntfy / Apprise / Discord webhook). | Low | Notify on *exceptions and successes worth knowing*, not every poll. |
| **Daemon loop + scheduling** | Hands-off operation | Long-running container with an internal scheduler (poll interval) OR cron-triggered run. Idempotent runs (state-driven) so overlapping/missed runs are safe. | Low–Medium | Must be safe to run repeatedly with no harm — state model guarantees idempotency. |
| **Self-recovery from failures** | Hands-off operation | Resume interrupted downloads, reconcile slskd transfer state vs Curator state on startup, handle slskd/*arr/VPN being temporarily down with retries, never crash-loop into duplicate grabs. | High | Startup reconciliation against slskd's live transfer list is essential to avoid double-grabbing across restarts. |

---

## Differentiators

What makes Curator meaningfully better than raw Soularr / manual slskd. These directly target the owner's stated frustrations.

| Feature | Value Proposition | Maps to | Complexity | Notes |
|---------|-------------------|---------|------------|-------|
| **Confidence-gated "refuse rather than guess"** | Soularr's biggest failing is grabbing *wrong* or *incomplete* matches. Curator treats a low-confidence match as a non-event (logged, backed off) rather than a download. | P2 | High | The single highest-value differentiator. Requires the strong validation/scoring engine above. |
| **Cutoff-aware upgrade-only logic** | For cutoff-unmet items, only grab when the P2P candidate *strictly beats* the current file per the *arr profile — never sidegrade or downgrade. | P3 | Medium | Depends on quality-inference + reading current item quality from *arr. |
| **First-class redundancy memory with provenance** | Persistent per-item ledger ("tried user X's copy on date Y, rejected: 9/12 tracks") so retries are smarter, not blind. | P1 | Medium | Goes beyond Soularr's thinner failed-list handling. |
| **End-to-end import verification (closed loop)** | Don't just trigger rescan — poll until *arr confirms the item is satisfied, then mark `imported`; if rescan didn't pick it up, fall back to Manual Import API and re-verify. | P5 | High | Eliminates the "downloaded but never imported" silent failure that creates *manual* cleanup labor. |
| **VPN/port-forward health as a first-class precondition** | Curator refuses to start downloads if kill-switch/port-forward isn't healthy, and auto-syncs PIA's forwarded port into slskd. | Privacy | Medium | Turns a fragile manual setup into a self-healing one. |
| **Rich Homepage widget + actionable alerts** | Single glance: how many true gaps remain, what's stuck and why, last successful grab. Alerts are actionable (blocked-by-VPN vs no-source-found are different signals). | Observability | Medium | Differentiates from Soularr's log-only UX. |
| ***-arr-agnostic adapter layer** | Music/books behind a common interface so a Readarr-replacement fork can be swapped without touching core logic. | Future-proofing | Medium | Directly hedges the Readarr-retirement risk. |
| **Uploader-health-aware selection** | Prefer candidates from users with free slots / good speed / not fully queued, to maximize completion likelihood and reduce stuck transfers. | P1/stuck | Medium | slskd surfaces queue/slot/speed hints per result. |

---

## Anti-Features

Features to explicitly NOT build. Building these reintroduces labor or scope creep — the opposite of the mandate.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|--------------------|
| **Any interactive "approve this match" prompt / UI for selection** | Reintroduces manual labor — the core thing being eliminated. | Confidence-gate automatically: high-confidence → grab; low-confidence → skip + back off + log. No human in the loop. |
| **Recomputing/curating "what is wanted"** | The owner already curated in *arr down to track/title. Duplicating that logic creates drift and config burden (P4). | Read wanted/cutoff lists from *arr as the single source of truth. |
| **Becoming a primary indexer / racing Usenet** | Violates fallback-only mandate; risks downgrades by grabbing P2P before a good Usenet copy lands. | Strict grace-then-fallback gating; Soulseek is always last resort. |
| **Scope creep into Radarr / Sonarr / Whisparr (video)** | Soulseek is poor for video; doubles surface area; out of mandate. | Music + books only. Hard scope boundary. |
| **Re-implementing rename/move/organize** | *arr already does this perfectly into `/volume1`; reimplementing causes Plex-naming friction (P5). | Hand files to *arr; let it rename/move. Curator never writes the final library path. |
| **Custom quality profile system / "supplementary config"** | Prior attempts suffered from complex extra config (P4). | Zero quality config in Curator — read the *arr profile/cutoff verbatim. |
| **Manual share curation / per-folder share toggling UI** | Manual labor; brittle. | Point slskd at the library (or a dedicated dir) once; leave it. |
| **Aggressive seeding/ratio gamification beyond "not a leecher"** | Soulseek isn't a ratio economy like private trackers; over-engineering wastes effort. | Just ensure shares exist + upload slots enabled. Good-citizen baseline, nothing more. |
| **Notification spam (every poll / every candidate)** | Noise defeats the point of hands-off. | Notify only on state transitions worth human awareness (final failure, blocked, success). |
| **Storing/serving media itself or a download UI** | Plex/*arr own presentation; slskd owns transfers. | Curator is a headless orchestrator + thin status surface. |
| **Auto-monitoring/auto-adding new wants** | Owner curates; auto-adding pollutes the library. | Never add to *arr; only fulfill what *arr already wants. |

---

## State Model (the redundancy fix — table-stakes detail)

Mature *arr-adjacent tools converge on a **durable per-item ledger** keyed by the *arr's stable release/item ID. Recommended states:

```
new → eligible (grace elapsed) → searching → candidate_selected → downloading
    → imported            (terminal-success; never re-touch)
    → unavailable         (no acceptable candidate; backoff timer)
    → failed              (download/import error; backoff + attempt counter)
    → dormant             (max attempts hit; wait for *arr change signal)
    → invalidated         (*arr no longer wants it / edition changed → purge)
```

Key rules:
- **Idempotency:** every daemon loop is safe to re-run; state, not timing, drives action.
- **Backoff schedule:** e.g. 1d → 3d → 7d → 30d, capped, then `dormant`.
- **Reconciliation on startup:** diff Curator state against slskd live transfers AND *arr satisfaction state to avoid double-grabs after a restart.
- **Invalidation:** if the *arr removes the want or the user changes edition/quality cutoff, purge or re-evaluate the ledger entry.
- **Provenance log:** record which uploader/copy was tried and why it was rejected (track-count mismatch, quality, stalled).

Persistence: SQLite is the pragmatic choice (single-file, transactional, container-friendly).

---

## Import Handoff: Two Approaches (decide explicitly)

| Approach | How | Pros | Cons | Verdict |
|----------|-----|------|------|---------|
| **Drop-folder + rescan command** | Drop completed files into the *arr's monitored "Downloads"/completed path, then `POST /api/v1/command {name: DownloadedAlbumsScan}` (Lidarr) / RescanFolders. *arr auto-imports + renames. | Simple; mirrors normal download-client flow; *arr handles edge cases. | Auto-import can silently skip ambiguous folders; needs verification loop. | **Default.** Mirrors how *arr expects completed downloads. |
| **Manual Import API** | Curator calls `GET /api/v1/manualimport?folder=...` to get proposed mappings, fixes/approves them, then `POST /api/v1/command {name: ManualImport, files: [...]}`. | Explicit control over track→file mapping; handles messy P2P folder structures the auto-scan rejects. | More API surface; must replicate some *arr matching. | **Fallback** when auto-rescan fails to import a confirmed-good download. |

**Recommended pattern:** drop-folder + rescan first; if the closed-loop verification shows the item still unsatisfied after N seconds, escalate to Manual Import API; re-verify; only then mark `imported`. This closed loop is the cure for "downloaded but not imported" (P5). *(Endpoint paths are version-sensitive — verify against the running Lidarr v1 / Readarr API.)*

---

## Feature Dependencies

```
Gap detection ──> Match & scoring ──> Quality enforcement ──> Download (slskd) ──> Import handoff ──> Import verification
     │                  │                      │                     │                    │
     └──────────────────┴──────────────────────┴────> State ledger <┘────────────────────┘   (every stage reads/writes state)

VPN health (gluetun+PIA port-forward) ──gates──> Download (slskd)   (no healthy VPN ⇒ no downloads)

slskd share config ──independent──> (operational health of search over time)

State ledger ──feeds──> Status surface (Homepage) ──and──> Notifications

Daemon loop ──orchestrates──> all of the above; Self-recovery reconciles State ledger vs slskd + *arr on startup
```

Critical path for MVP (music): **Gap detection → Match/scoring → Quality filter → slskd download (behind VPN) → drop-folder+rescan → import verification → state ledger**. Everything else (books, rich widget, fancy notifications) layers on after this loop closes reliably.

---

## MVP Recommendation

Build in this order (each builds on prior; state ledger underpins all):

1. **State ledger + gap detection (Lidarr)** — read wanted/cutoff, persist eligibility with grace timer. *(Fixes P1 foundation.)*
2. **slskd search + confidence-gated match/scoring + quality filter** — the correctness core; refuse-rather-than-guess. *(Fixes P2, P3.)*
3. **Download behind gluetun+PIA (with port-forward sync + kill-switch) → drop-folder → rescan → import verification.** *(Fixes P5 + privacy.)*
4. **slskd share config (not-a-leecher baseline).** *(Operational survival.)*
5. **Daemon loop + self-recovery + backoff.** *(Hands-off.)*
6. **Homepage status + push notifications.** *(Observability.)*
7. **Books (Readarr) via the *-arr-agnostic adapter — feature-flagged, best-effort.** *(Gated on Readarr viability.)*

**Defer / best-effort:** Books path (Readarr risk); uploader-health-aware selection (optimization, not correctness); rich widget polish.

---

## Sources

- Domain knowledge of the *arr ecosystem (Lidarr/Readarr/Servarr APIs), slskd, Soularr (mrusse/soularr), the Soulseek protocol, and gluetun/PIA port-forwarding patterns. **[CONFIDENCE: MEDIUM — training cutoff Jan 2026; external verification was blocked in this environment.]**
- **Verify before requirements lock:**
  - Readarr maintenance/retirement status and whether a viable fork exists. **[HIGH PRIORITY]**
  - Exact slskd REST API surface for search/download/shares + SignalR events (current release).
  - Lidarr v1 / Readarr API endpoint paths for `wanted/missing`, `wanted/cutoff`, `manualimport`, and `command` names (`DownloadedAlbumsScan`, `RescanFolders`).
  - slskd's reliability of bitrate/format metadata in search results (affects quality-filter design).
  - PIA port-forwarding behavior with gluetun (forwarded-port change on reconnect → sync mechanism).
