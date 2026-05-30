# Project Research Summary

**Project:** Curator
**Domain:** Autonomous, fallback-only P2P (Soulseek/slskd) media gap-filler orchestrating Lidarr (music) + Readarr (books) on a Synology homelab
**Researched:** 2026-05-29
**Confidence:** HIGH

## Executive Summary

Curator is a headless, hands-off automation daemon — not a UI app — whose only job is to acquire what Lidarr/Readarr already mark "wanted" but the existing Usenet pipeline can't get, source it correctly from Soulseek, validate it, and hand it cleanly to the *arr stack so Plex reflects it. The owner has run this category of tool before (**slskd + Soularr**) and abandoned it because it became a part-time job: redundant downloads, wrong matches, quality downgrades, fragile config, and import/sync friction. The research is unanimous and pointed: **every one of those five prior pain points maps to a documented, structural Soularr weakness**, and the entire literature of this domain (Soularr, its Readarr fork r:soul, slskd) reads as a catalog of how to fix them.

**Headline decision: build slskd-DIRECT — Curator owns the search→match→download→import→state loop and drives slskd through its REST API. Do NOT adopt Soularr.** Soularr is Lidarr-only (a hard blocker for the books requirement), is a stateless cron script (the root cause of redundant downloads and the "downloaded-but-not-imported" spiral), uses loose filename-ratio matching (the wrong-match cause), and applies its own quality logic (the downgrade cause). Adopting it re-imports exactly the labor Curator exists to delete. Read Soularr/r:soul as reference implementations; build on the only stable primitive — slskd. The recommended stack is **slskd (pinned) behind a gluetun/PIA VPN sidecar, a Python 3.12 / FastAPI orchestrator, SQLite (WAL) as the state spine, Apprise for notifications, a Homepage `customapi` widget for observability, and GitHub Actions → Docker Hub for the build/deploy loop.**

The dominant risks are all infrastructure-shaped and concentrated at the very start. The networking topology is the make-or-break: gluetun joins `synobridge` and publishes slskd's ports; slskd runs `network_mode: service:gluetun` to inherit the VPN namespace; `FIREWALL_OUTBOUND_SUBNETS` must allow `synobridge` so the *arr APIs stay reachable; and PIA port forwarding **fails silently on US regions** — a non-US PF-capable region is mandatory. A single identical `/volume1/data` mount across slskd/curator/Lidarr/Readarr enables atomic hardlink imports (the fix for the #1 import-failure cause). The SQLite state ledger is the spine that kills redundant downloads, retry storms, and import friction in one stroke. Matching is deliberately **precision-over-recall** — reject wrong/incomplete candidates rather than guess — because a miss is cheap (Usenet may still get it) while a wrong import is the exact manual cleanup labor Curator must eliminate. Books (Readarr, retired upstream) are best-effort behind an *-arr-agnostic adapter; music ships first and is the reliability backbone. Spectral/fake-FLAC content analysis is OUT of scope — the owner prioritizes getting the *right music* over policing fake quality, and defers all quality judgment to the *arr profiles.

## Key Findings

### Recommended Stack

The whole ecosystem is Python, so Curator is Python — it maximizes leverage (`pyarr`, `slskd-api`, `apprise` are first-class) and minimizes glue. The source engine is slskd driven directly; persistence is embedded SQLite (single writer, zero ops); status surfaces through the owner's existing Homepage rather than a new UI. The single most important integration detail is the gluetun/slskd VPN wiring (see Architecture). A notable simplification surfaced in research: **slskd has NATIVE gluetun port-forwarding integration since v0.24.4 — Curator does NOT need to build a port-sync loop**; slskd polls gluetun's control server, waits for VPN-ready, and self-applies PIA's (rotating) forwarded port. See `STACK.md` for pinned versions and the full compose wiring.

**Core technologies:**
- **slskd** (pin `slskd/slskd:0.25.1`): headless Soulseek daemon — search, download, native share management (satisfies give-back), native gluetun VPN/PF integration — the only stable, maintained primitive in this space.
- **gluetun** (`qmcgaw/gluetun`, pin a dated v3.x): PIA VPN sidecar; slskd shares its netns so all Soulseek egress is tunneled and fail-closed; control server feeds the forwarded port to slskd.
- **Python 3.12 + FastAPI/Uvicorn**: orchestrator runtime + the single JSON status endpoint for Homepage; async, pydantic-native, coexists with the background poller.
- **SQLite (WAL) via SQLModel/SQLAlchemy**: the dedup/state ledger — single service, single writer, embeddable, zero admin (Postgres is unjustified).
- **Supporting libs**: `pyarr` (one client for both Lidarr v1 + Readarr v1), `slskd-api` (typed slskd client, raw `httpx` as escape hatch), `APScheduler` (the poll loop), `Apprise` (one-config multi-target notifications), `pydantic-settings` (typed env/YAML config).
- **CI/CD**: GitHub Actions (`build-push-action@v6` et al.) → Docker Hub, `linux/amd64` only (DS423+ is amd64; skip QEMU).

### Expected Features

This domain has one widely-used reference (Soularr) the owner already rejected, so "differentiator" literally means *better than running raw Soularr on cron*. The single root cause behind redundant downloads (PP1) and import friction (PP5) is that **Soularr keeps no persistent attempt-state** — a failed-import item stays "wanted" and gets re-grabbed forever. Curator's state ledger is the fix. See `FEATURES.md`.

**Must have (table stakes):**
- Gap detection from *arr `wanted/missing` + `wanted/cutoff` (monitored-only is the gate; no parallel wishlist) — PP4.
- **Grace-then-fallback timing** — act only after a grace window and only if Usenet hasn't satisfied/isn't in-flight — PP1, PP15.
- **Persistent attempt-state + backoff + do-not-retry memory** (the spine) — PP1.
- **Precision-over-recall candidate validation** — token (Jaccard) overlap + length ratio, separate artist/title matching, short-title protection, tracklist-completeness gate; **reject below threshold rather than grab "close enough"** — PP2.
- Quality enforcement by deferring entirely to *arr profiles/cutoffs (no parallel quality logic) — PP3.
- **Clean import handoff via ManualImport API with confirmation** — only mark `succeeded` after the item leaves the *arr wanted list — PP5.
- slskd share config (anti-leech give-back), VPN-routed traffic, VPN-down fail-safe (never mark items `unavailable` due to an outage).

**Should have (competitive):**
- Staging → selective Manual-Import → auto-purge cleanup flow so leftover/unwanted files never reach the library and never need manual deletion.
- Durable do-not-retry with dormant long-TTL re-check (Soulseek catalogs change over time).
- Homepage observability surface + push notifications (grab/failure/blocked/stuck only — no routine spam).
- Stuck-download detection + auto-cleanup; startup reconciliation against slskd live transfers.

**Defer (v2+):**
- Pluggable second backend (e.g. a books source beyond Soulseek) — keep OFF by default; architect the seam, don't build it.
- Edition-disambiguation refinement — start with artist+album+trackcount+year, refine after observing real mismatches.

### Architecture Approach

Curator is a **three-container unit** deployed by one `docker-compose.yml`, layered onto the existing `synobridge` network: `gluetun` (on synobridge, publishes slskd's ports), `slskd` (`network_mode: service:gluetun`, no own network — only Soulseek traffic is tunneled), and `curator` (a normal synobridge member). Curator is a **modular monolith** (single Python process: APScheduler loop + FastAPI status API + SQLite), using a **pull/reconcile loop** (no webhooks) for trivial restart recovery. The critical, live-verified networking pattern: Curator reaches slskd at **`http://gluetun:5030` (never `http://slskd`)**; slskd reads gluetun's control server at `localhost:8000` inside the shared netns and self-sets its listen port; `FIREWALL_OUTBOUND_SUBNETS` must include synobridge so the *arr APIs stay reachable through the kill-switch. See `ARCHITECTURE.md`.

**Major components:**
1. **scheduler + gap-detector** — poll *arr wanted/missing+cutoff, apply the grace+fallback gate, upsert items.
2. **matcher/scorer + downloader** — build Soulseek queries, rank candidates (completeness, track-count, format, peer health), refuse-rather-than-guess, enqueue to slskd via gluetun:5030.
3. **state/dedup store (SQLite + WAL)** — the single mutable surface and source of truth (items, attempts, peers, share_stats, events); every core module is stateless between ticks.
4. **importer + verifier/reconciler** — staged handoff into the shared `/data` tree, ManualImport trigger, closed-loop confirmation, startup reconciliation, partial cleanup.
5. **share-manager, notifier (Apprise), status API (FastAPI :8674)** — good-citizen baseline, event push, Homepage JSON.
6. **books adapter** — Readarr behind a *-arr-agnostic interface (single-adapter swap), feature-flagged, layered on a working music loop.

### Critical Pitfalls

1. **PIA US-region port-forwarding failure** — US PIA servers silently provide no forwarded port → crippled transfers + leecher status. *Avoid:* `VPN_PORT_FORWARDING=on` + a verified non-US PF region (e.g. CA Toronto); assert a non-zero port before declaring the stack healthy.
2. **`network_mode: service:gluetun` vs synobridge collision** — a netns-sharing container can't also join synobridge, breaking *arr name resolution. *Avoid:* put gluetun on synobridge, let slskd inherit it, publish slskd's ports on gluetun, and set `FIREWALL_OUTBOUND_SUBNETS` to include synobridge + LAN so the kill-switch doesn't blackhole *arr API calls.
3. **Download/import path identity** — the #1 near-universal import-failure cause is slskd and *arr seeing the same folder at different paths. *Avoid:* one identical `/volume1/data` mount across slskd/curator/Lidarr/Readarr → atomic hardlink imports, no remote-path-mapping, no cross-filesystem copies. Verify the existing *arr mount convention before coding the importer.
4. **Redundant downloads from no persistent state** — deriving "should I act?" from the *arr wanted list alone re-grabs failed/in-flight items every loop. *Avoid:* the SQLite ledger is the source of truth; gate every action on persisted state; treat "downloaded-but-not-imported" as a distinct state.
5. **Racing the Usenet pipeline + retry storms** — grabbing without a grace window / Usenet-activity check violates fallback-only; naive loops hammer slskd with no backoff. *Avoid:* grace-then-fallback gate (check SABnzbd/*arr queue+history, re-check just before grab), per-item exponential backoff with jitter, transfer timeouts, and a hard infra-vs-item-failure separation (a VPN blip never burns an attempt or marks items `unavailable`).

(See `PITFALLS.md` for all 18, the "Looks Done But Isn't" checklist, and the pitfall-to-phase mapping. Note: fake-FLAC/spectral content validation appears in PITFALLS as a defense but is explicitly OUT of scope per owner priority — quality is deferred entirely to *arr profiles.)

## Implications for Roadmap

Research strongly favors a **Horizontal-Layers** build order: get the riskiest infrastructure proven first, then build the state spine, then the acquisition pipeline, then close the loop, then layer observability and books. The final ROADMAP.md consolidates the research's 8-stage dependency analysis into 6 standard-granularity phases (see ROADMAP.md); the ordering rationale below is preserved from the research.

### Build-order rationale (from research)
- **Riskiest-first:** the networking topology (netns/synobridge, PIA PF, path identity) is the dominant failure mode and gates everything — it must be a smoke-test go/no-go before any feature code.
- **State before engine:** the ledger is the spine that fixes PP1/PP5/PP13; the acquisition engine must query state, not the *arr wanted list alone — so it cannot precede the store.
- **Detect/trigger before acquire:** the grace-then-fallback gate is the product's reason for existing (supplementary-only); it must constrain the engine, not be bolted on after.
- **Match/quality is the high-risk core** and is isolated so it can be spiked against real Soulseek data without blocking infra work.
- **Close the loop (import+verify+cleanup) before scaling out observability** — "succeeded" must mean "in the Plex-visible library, with no junk left behind."
- **Music ships before books** — Readarr's retirement makes books a flagged, adapter-isolated best-effort path on top of a proven music loop.

### Research Flags

Phases likely needing deeper research during planning (`--research-phase`):
- **Networking/VPN (Phase 2):** verify-live — exact `SLSKD_VPN_*` var casing for the pinned slskd build, gluetun control-server `config.toml` auth shape (~v3.40+), current PIA PF-capable region list, and the existing *arr mount convention on this NAS. Resolve empirically in the smoke test.
- **Search/Matching (Phase 4):** wrong-match precision is the highest-risk feature; spike scoring/thresholds against real Soulseek results before committing the design.
- **Import handoff (Phase 5):** the *arr ManualImport command payload is undocumented and version-finicky — validate against live `/swagger` and model off Soularr/the *arr UI network calls.
- **Books/Readarr:** unmaintained API drift + messy book metadata — flag before building book support.

Standard patterns (skip research-phase): SQLite/WAL state store; Homepage `customapi` + Apprise; GitHub Actions → Docker Hub `linux/amd64`.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Source-engine decision, gluetun+PIA wiring (incl. slskd native VPN integration + control-server auth), *arr APIs, persistence, Homepage, notifications, CI/CD all verified against official docs/PyPI/project pages; versions pinned. |
| Features | MEDIUM-HIGH | Behavior verified against Soularr/r:soul/slskd source & issue trackers; every pain point maps to a documented weakness. Exact *arr API payloads (esp. ManualImport) flagged needs-validation. |
| Architecture | HIGH | Container topology + the gluetun/slskd/synobridge networking live-verified; component design HIGH; exact *arr endpoint/command names MEDIUM (confirm against live `/swagger`). |
| Pitfalls | MEDIUM-HIGH | Grounded in established slskd/Soularr/gluetun/Synology/*arr community knowledge AND the owner's first-hand prior experience (the five pain points are validated, not hypothetical). Specific PIA region behavior + some slskd API details flagged verify-live. |

**Overall confidence:** HIGH

### Gaps to Address

- **slskd/gluetun version + var specifics** (`SLSKD_VPN_*` casing, control-server apikey key name, gluetun `config.toml` auth shape): resolve empirically in the networking smoke test before component work.
- **PIA PF-capable region list** (changes over time): verify live and pin a non-US region (CA Toronto/Montreal likely).
- **Existing *arr mount convention on this NAS** (single `/data` vs split): inspect the running containers before coding the importer — dictates the entire volume layout.
- **Lidarr/Readarr v1 endpoint/command names + ManualImport payload:** confirm against live `/swagger`; keep raw-`httpx` fallbacks (esp. for Readarr).
- **slskd-as-download-client vs Curator-managed handoff:** two viable integration models — decide empirically.
- **Readarr longevity:** unmaintained; isolate behind the adapter and degrade gracefully.

## Sources

### Primary (HIGH confidence)
- slskd repo + releases + config/vpn docs (`slskd/slskd` 0.25.1; API-key auth; native `integration.vpn.gluetun` PF since 0.24.4; control-server-auth 401 gotcha, issue #1660) — engine, VPN wiring.
- gluetun wiki (connect-a-container netns sharing; PIA provider + `VPN_PORT_FORWARDING`; control server `/v1/portforward`, US-no-PF; control-server auth ~v3.40+) — networking topology.
- Lidarr/Readarr/Servarr v1 API docs + wiki (`wanted/missing`, `wanted/cutoff`, `qualityprofile`, `command`, `manualimport`; Readarr retired) — *arr integration.
- PyPI/project pages: `slskd-api` 0.1.5, `pyarr` 6.x, `apprise` 1.9.x, `httpx` 0.28.1, FastAPI/Uvicorn/SQLModel; Homepage `customapi`/slskd widgets; docker/* GitHub Actions (build-push@v6 etc.).

### Secondary (MEDIUM confidence)
- Soularr (`mrusse/soularr`) + pain-point issues (#164, #179, #68, #161): Lidarr-only, stateless, filename-ratio matching — direct evidence of PP1/PP2/PP3/PP5 and the reference implementation to read-not-adopt.
- r:soul (`insanemal/rsoul`): persistent state + reconcile-on-restart + Jaccard/length-ratio/metadata validation — the matching + state model to emulate.
- gluetun↔slskd port-sync discussions (gluetun #1966, slskd #1432) and fallback tools (glueforward) — escape hatches if native PF misbehaves.
- TRaSH-guide single-`/data`-mount / hardlink conventions; Homepage gluetun widget auth.

### Tertiary (LOW confidence)
- SoulSync (alt music tool) — context only; internals not fully retrieved.
- Owner's first-hand prior slskd+Soularr experience — HIGH for the problem statements, MEDIUM for downstream API-stability implications (e.g., Readarr drift).

---
*Research completed: 2026-05-29*
*Ready for roadmap: yes*
