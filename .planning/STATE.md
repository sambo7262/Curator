# Project State

<!-- GSD reads this file first to understand where the project is. Keep it current. -->

## Current Status

**Phase**: 2 - State Ledger + *arr Adapter + Gap Detection — ⚙ IN PROGRESS
**Plan**: 3 of 4 complete (4 plans in 3 waves)
**Status**: 02-03 ✓ COMPLETE — the *arr-agnostic adapter seam (the firewall) landed: base.py (frozen `GapItem` + `ArrAdapter` Protocol — get_wanted implemented, import/command/profile/queue methods declared-and-stubbed for Phases 3-5), lidarr.py (paged httpx GET of wanted/missing+cutoff with X-Api-Key; maps `profileId` NOT qualityProfileId + `foreignAlbumId` MBID identity; raise_for_status surfaces — primary, NOT breaker-wrapped), readarr.py (defensive `_map`→None on bad records, fault-swallowing `_paged`→[], includeAuthor, tolerates both profile-id spellings), breaker.py (drop-in CircuitBreaker → [] when open/on-exception, resets on success). 3 offline test files: lidarr mapping+two-page pagination (GAP-01/02), readarr empty/garbage/5xx/breaker degradation (ARR-02), Protocol surface + comment-aware firewall grep (ARR-01). ARR-01/ARR-02/GAP-01/GAP-02 complete. 02-01 + 02-02 remain ✓ COMPLETE (Wave 0 substrate + the SQLite-WAL ledger; STATE-01/STATE-02). Phase 1 remains ✓ COMPLETE (deployed & verified on NAS — slskd logged into Soulseek via PIA Vancouver, port 56034).
**Last action**: Executed + finalized 02-03 (3 atomic commits: 956c489 base+lidarr, f0ad22e readarr+breaker, 5d68b3d test suite). Sandbox turned out to have httpx 0.27.2, so the behavioral pytest actually ran: 9/9 adapter tests pass, full suite 17 passed. Firewall grep clean (zero *arr field names in app/core + app/state).
**Next action**: Execute Wave 3: 02-04 (core/gap_detector.py — `detect_gaps(adapters, repo)` iterating [LidarrAdapter, CircuitBreaker(ReadarrAdapter)] → repo.upsert_gap per GapItem; end-to-end dedup + Readarr-fault-does-not-gate-music proofs; manual one-shot trigger) [GAP-01, GAP-02, STATE-02, ARR-02]. GapItem is duck-type-compatible with 02-02's upsert_gap. NOTE: the real green/red gate is `pytest app/tests -q` on Python 3.12 at CI/NAS (CI pins httpx 0.28.1).

## Active Phase Detail

Phase 1 delivers the substrate: gluetun (PIA/OpenVPN, kill-switch, port forwarding, control-server auth) + slskd (shared netns, native PF sync, user 1031:65536) + Curator FastAPI health stub, all from one compose pulling a CI-built Docker Hub image, with a single identical /volume1/data mount for atomic hardlinks. All six INFRA requirements (01-06) addressed in code; final proof is the on-NAS Go/No-Go smoke test (criteria that need live VPN/NAS were NOT runnable in the dev sandbox).

## Recent Decisions

- **PIA region = CA Vancouver** — west-coast PF-capable (US has zero port forwarding; latency was the owner's concern but PF is mandatory for slskd).
- **Inline execution** of Phase 1 (not worktree subagents) — all owner-provided values were in context and 01-02/01-04 share docker-compose.yml; faster and conflict-free.
- **Owner deploys manually** in Container Manager (not auto-SSH) — DEPLOY.md is the handoff.
- **slskd-direct, not Soularr**; **v1 = music + books (Readarr best-effort)**; **staging→Manual-Import→auto-purge** cleanup (the 6th pain point).
- **Status-preserving upsert (02-02)** — the `ON CONFLICT(arr_app, arr_id)` SET clause omits BOTH `status` and `discovered_at`, so a re-detect of an acted-on/first-seen row never clobbers its lifecycle status (the #1 STATE-02 pitfall, proven by `test_upsert_preserves_status`).
- **WAL + PRAGMA user_version migrations (02-02)** — idempotent versioned runner reading schema.sql relative to db.py; the only f-string-into-SQL is the loop-controlled `user_version` bump; all data queries use `?` placeholders.
- **One ArrAdapter Protocol + GapItem firewall (02-03)** — *arr field names (foreignAlbumId/profileId/records[]/X-Api-Key) live ONLY in app/adapters/; enforced by a comment-aware grep test over app/core + app/state. Phase 2 implements only get_wanted(); import/command/profile/queue methods are declared-and-stubbed to lock the seam shape.
- **Primary-vs-best-effort fault policy (02-03)** — LidarrAdapter raise_for_status surfaces hard faults (NOT breaker-wrapped, it's primary); ReadarrAdapter `_paged` swallows faults → [] and `_map` returns None (skip+log) on bad records, all behind a CircuitBreaker → [] when open. Makes "books never gate music" structural.
- **Phase-2 Protocol conformance via attribute/callable checks, not isinstance (02-03)** — a runtime_checkable isinstance() over-asserts because the later-phase methods are stubbed (not implemented) on concrete adapters; tests assert `.app` + `callable(get_wanted)` (plan-sanctioned). ReadarrAdapter tolerates both `qualityProfileId` and `profileId` (A-R2).

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-29)

**Core value:** Anything monitored in Lidarr/Readarr that the Usenet pipeline can't get is acquired automatically — correctly matched, right quality, no redundant downloads, no junk, zero manual interaction.
**Current focus:** Phase 1 complete ✓ — next is Phase 2 (State Ledger + *arr Adapter + Gap Detection)

## Notes / Blockers

- **Unverified locally:** the dev sandbox has Python 3.9 + no network (offline pip), so `pytest` for the Curator stub was NOT run here. YAML structure for compose + CI workflow WAS validated (3 services, slskd has no ports/networks, single external net, CI targets linux/amd64). Tests + full smoke run execute on the NAS / in CI.
- Config: YOLO, standard granularity, parallel, quality models, research + plan-check on, verifier off, docs committed.
- Phase 1 NAS deploy needs from owner: `.env` with PIA_USER/PIA_PASSWORD, `GLUETUN_API_KEY` (genkey), `SLSKD_API_KEY`, LIDARR/READARR API keys. Everything else pre-filled.
- Deploy-time TODO (optional but recommended): pin gluetun/slskd `@sha256` digests in compose.
- Owner environment in memory: synobridge 172.20.0.0/16 (gw .1), PUID/PGID 1031/65536, /volume1/data, lidarr:8686, readarr:8787, curator:8674 (LAN/Tailscale-only firewall rule).
