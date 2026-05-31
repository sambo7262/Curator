# Project State

<!-- GSD reads this file first to understand where the project is. Keep it current. -->

## Current Status

**Phase**: 2 - State Ledger + *arr Adapter + Gap Detection — ✓ COMPLETE
**Plan**: 4 of 4 complete (4 plans in 3 waves)
**Status**: 02-04 ✓ COMPLETE — Phase 2 closed end-to-end. `core/gap_detector.py` wires the seam to the spine: `detect_gaps(adapters, conn)` iterates `[LidarrAdapter (primary), CircuitBreaker(ReadarrAdapter) (best-effort)]` INDEPENDENTLY, calls `adapter.get_wanted()`, upserts each `GapItem` via `repo.upsert_gap`, returns per-app counts; `build_adapters()` constructs the live list; a `python -m core.gap_detector` `__main__` one-shot manual UAT trigger (NOT a scheduled loop — that's Phase 5). Firewall intact: gap_detector imports only `GapItem` + `ArrAdapter` Protocol + `state.repo` (firewall grep = 0 forbidden tokens), no scheduler/sleep/slskd. `test_gap_detector.py` proves all 4 success criteria together via in-test FakeAdapter/FaultyAdapter over a migrated tmp DB: end_to_end_counts (GAP-01/02), dedup_on_rerun + dedup_preserves_status_end_to_end (STATE-02), readarr_fault_does_not_gate_music (ARR-02). GAP-01/GAP-02/STATE-02/ARR-02 now proven end-to-end. 02-01/02-02/02-03 ✓ COMPLETE. Phase 1 ✓ COMPLETE (deployed & verified on NAS — slskd logged into Soulseek via PIA Vancouver, port 56034).
**Last action**: Deployed Phase 2 to the NAS and verified LIVE. `POST /detect` ran a real pass against live Lidarr — ledger climbed (1407→1493 in ~15s), proving real-Lidarr read + GapItem parse + /db write end-to-end; Readarr=0 (keyless/empty → gracefully skipped, music unaffected, ARR-02 live). UAT test 6 now PASS → 02-UAT.md status: complete (6/6). Earlier: added `POST /detect` (single-writer, 409 guard — fixed the docker-exec `database is locked`); reconciled repo docker-compose.yml to LAN-IP defaults (overridable) + .env.example + DEPLOY.md Phase-2 deltas (/db mount, non-root, /detect). Logged a Phase-2→Phase-5 perf follow-up: detect_gaps does one fsync/row (synchronous=FULL) → ~5-6 rows/sec, slow on bulk → batch the pass in one txn in Phase 5 (.planning/phases/phase-5/RESEARCH-SEED.md + memory). Suite 33 passed.
**Next action**: Phase 2 COMPLETE — all gates green (exec 4/4, tests 33, review 12-fixed, security 13/13, UAT 6/6 incl. live NAS). Two optional quick live confirms when the in-progress bulk pass finishes: re-POST /detect → count must NOT grow (live dedup); restart curator → rows persist (live durability). Then `/clear` → `/gsd:plan-phase 3` (Matching & Quality Gating — QUAL-01/02/03, MATCH-01/02; phase-3/RESEARCH-SEED.md has beets-distance-model + 1σ-threshold queued). Deferred to Phase 5: detection batch-fsync perf (phase-5/RESEARCH-SEED.md).

## Active Phase Detail

Phase 1 delivers the substrate: gluetun (PIA/OpenVPN, kill-switch, port forwarding, control-server auth) + slskd (shared netns, native PF sync, user 1031:65536) + Curator FastAPI health stub, all from one compose pulling a CI-built Docker Hub image, with a single identical /volume1/data mount for atomic hardlinks. All six INFRA requirements (01-06) addressed in code; final proof is the on-NAS Go/No-Go smoke test (criteria that need live VPN/NAS were NOT runnable in the dev sandbox).

## Recent Decisions

- **Phase 4 precondition: slskd sharing must be live BEFORE first real download (2026-05-30, owner-raised)** — Phase 4 does Curator's first slskd downloads, but SHARE automation is Phase 5. A zero-share Soulseek account gets leech-blocked. Resolution: do NOT reorder; configure slskd-level shares MANUALLY (point slskd at read-only `/data/media/music` + `/data/media/books`, container-internal paths, verify shared-file count > 0) before any Phase 4 live test. SHARE-01/02 (Curator auto-ensuring shares) stays in Phase 5. Full error-free setup procedure in `.planning/phases/phase-4/RESEARCH-SEED.md`. Phase 3 (matching/gating) is unaffected — never touches slskd.
- **PIA region = CA Vancouver** — west-coast PF-capable (US has zero port forwarding; latency was the owner's concern but PF is mandatory for slskd).
- **Inline execution** of Phase 1 (not worktree subagents) — all owner-provided values were in context and 01-02/01-04 share docker-compose.yml; faster and conflict-free.
- **Owner deploys manually** in Container Manager (not auto-SSH) — DEPLOY.md is the handoff.
- **slskd-direct, not Soularr**; **v1 = music + books (Readarr best-effort)**; **staging→Manual-Import→auto-purge** cleanup (the 6th pain point).
- **Status-preserving upsert (02-02)** — the `ON CONFLICT(arr_app, arr_id)` SET clause omits BOTH `status` and `discovered_at`, so a re-detect of an acted-on/first-seen row never clobbers its lifecycle status (the #1 STATE-02 pitfall, proven by `test_upsert_preserves_status`).
- **WAL + PRAGMA user_version migrations (02-02)** — idempotent versioned runner reading schema.sql relative to db.py; the only f-string-into-SQL is the loop-controlled `user_version` bump; all data queries use `?` placeholders.
- **One ArrAdapter Protocol + GapItem firewall (02-03)** — *arr field names (foreignAlbumId/profileId/records[]/X-Api-Key) live ONLY in app/adapters/; enforced by a comment-aware grep test over app/core + app/state. Phase 2 implements only get_wanted(); import/command/profile/queue methods are declared-and-stubbed to lock the seam shape.
- **Primary-vs-best-effort fault policy (02-03)** — LidarrAdapter raise_for_status surfaces hard faults (NOT breaker-wrapped, it's primary); ReadarrAdapter `_paged` swallows faults → [] and `_map` returns None (skip+log) on bad records, all behind a CircuitBreaker → [] when open. Makes "books never gate music" structural.
- **Phase-2 Protocol conformance via attribute/callable checks, not isinstance (02-03)** — a runtime_checkable isinstance() over-asserts because the later-phase methods are stubbed (not implemented) on concrete adapters; tests assert `.app` + `callable(get_wanted)` (plan-sanctioned). ReadarrAdapter tolerates both `qualityProfileId` and `profileId` (A-R2).
- **detect_gaps counts = items SEEN, not rows inserted (02-04)** — `counts[app] = len(get_wanted())` reports the true per-app gap count even on a dedup re-run (where 0 new rows are added), which is the semantically useful number for UAT/observability.
- **detect_gaps proven with in-test fakes, not the httpx mock (02-04)** — FakeAdapter/FaultyAdapter give deterministic, network-free control of adapter output, decoupling the integration proof from the recorded *arr fixture envelopes (already exercised by the adapter tests). Plan-sanctioned ("either is acceptable, keep it offline"). The breaker-wrapped FaultyAdapter proves ARR-02 (readarr:0, all Lidarr rows upserted, no raise) at the integration point.
- **One-shot `python -m` trigger, NOT a daemon (02-04)** — the `__main__` block is a manual UAT affordance only; the periodic scheduler/grace-window is Phase 5. Enforced by the verify grep forbidding while-True/sleep/apscheduler/slskd in gap_detector.

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
