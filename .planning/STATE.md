# Project State

<!-- GSD reads this file first to understand where the project is. Keep it current. -->

## Current Status

**Phase**: 2 - State Ledger + *arr Adapter + Gap Detection — ✓ COMPLETE
**Plan**: 4 of 4 complete (4 plans in 3 waves)
**Status**: 02-04 ✓ COMPLETE — Phase 2 closed end-to-end. `core/gap_detector.py` wires the seam to the spine: `detect_gaps(adapters, conn)` iterates `[LidarrAdapter (primary), CircuitBreaker(ReadarrAdapter) (best-effort)]` INDEPENDENTLY, calls `adapter.get_wanted()`, upserts each `GapItem` via `repo.upsert_gap`, returns per-app counts; `build_adapters()` constructs the live list; a `python -m core.gap_detector` `__main__` one-shot manual UAT trigger (NOT a scheduled loop — that's Phase 5). Firewall intact: gap_detector imports only `GapItem` + `ArrAdapter` Protocol + `state.repo` (firewall grep = 0 forbidden tokens), no scheduler/sleep/slskd. `test_gap_detector.py` proves all 4 success criteria together via in-test FakeAdapter/FaultyAdapter over a migrated tmp DB: end_to_end_counts (GAP-01/02), dedup_on_rerun + dedup_preserves_status_end_to_end (STATE-02), readarr_fault_does_not_gate_music (ARR-02). GAP-01/GAP-02/STATE-02/ARR-02 now proven end-to-end. 02-01/02-02/02-03 ✓ COMPLETE. Phase 1 ✓ COMPLETE (deployed & verified on NAS — slskd logged into Soulseek via PIA Vancouver, port 56034).
**Last action**: Executed all 4 plans, then ran the code-review gate (02-REVIEW.md: 2 Blockers + 2 Criticals + 6 Warnings + Info). User chose fix-all — gsd-code-fixer resolved 12 findings across atomic commits (pagination empty-page guard BL-01; retained app-lifetime DB connection + shutdown close BL-02; API-key validation/fail-fast, Lidarr fatal + Readarr graceful-skip CR-01; httpx client close CR-02; breaker half-open cooldown WR-04; lazy Settings.from_env WR-01; atomic migration txn WR-02; defensive Lidarr _map WR-03; synchronous=FULL WR-06; healthz phase:2 IN-01; unused import IN-04). Deferred IN-02 (:ro /data mount = Phase 4 staging decision) + IN-03 (raw_json, acceptable). Suite now **31 passed** (+10 tests), exit 0. 02-REVIEW.md status: fixed.
**Next action**: Phase 2 execution + review complete. Remaining gates: (1) `/gsd:secure-phase 2` — security_enforcement is ON and no 02-SECURITY.md exists yet (threat_model blocks are in all 4 plans but not retroactively verified); (2) `/gsd:verify-work` for Phase 2 UAT. Then `/gsd:plan-phase 3` (Matching & Quality Gating — QUAL-01/02/03, MATCH-01/02; RESEARCH-SEED.md already captures the beets-distance-model + 1σ-threshold direction). NOTE: authoritative green/red gate is `pytest app/tests -q` on Python 3.12 at CI/NAS (CI pins httpx 0.28.1); local sandbox ran on httpx 0.27.2.

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
