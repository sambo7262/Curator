# Project State

<!-- GSD reads this file first to understand where the project is. Keep it current. -->

## Current Status

**Phase**: 2 - State Ledger + *arr Adapter + Gap Detection — ⚙ IN PROGRESS
**Plan**: 1 of 4 complete (4 plans in 3 waves)
**Status**: 02-01 ✓ COMPLETE — Wave 0 offline test substrate landed: 6 recorded *arr JSON fixtures + conftest (tmp_db_path/load_fixture/offline httpx-mock), config.py (frozen Settings + DB_PATH), 3 package markers, and the dedicated /db mount + DB_PATH wired into compose/.env.example. Blocking package-legitimacy checkpoint RESOLVED — httpx==0.28.1 + respx==0.22.0 human-approved as pinned. Phase 1 remains ✓ COMPLETE (deployed & verified on NAS — slskd logged into Soulseek via PIA Vancouver, port 56034).
**Last action**: Executed + finalized 02-01 (continuation after checkpoint approval). Local verify = AST-parse + grep (all task `<automated>` blocks pass); behavioral pytest deferred to CI/NAS Python 3.12.
**Next action**: Execute Wave 2 (parallel): 02-02 (SQLite-WAL ledger, status-preserving dedup upsert, startup hook) + 02-03 (ArrAdapter Protocol + Lidarr/defensive-Readarr + breaker). Then Wave 3: 02-04 (gap_detector wiring + end-to-end dedup/Readarr-degradation proofs + one-shot trigger). NOTE: behavioral pytest verifies are non-fatal locally (Python-3.9 + offline sandbox) — the real green/red gate is `pytest app/tests -q` on Python 3.12 at CI/NAS; run it before marking the phase verified.

## Active Phase Detail

Phase 1 delivers the substrate: gluetun (PIA/OpenVPN, kill-switch, port forwarding, control-server auth) + slskd (shared netns, native PF sync, user 1031:65536) + Curator FastAPI health stub, all from one compose pulling a CI-built Docker Hub image, with a single identical /volume1/data mount for atomic hardlinks. All six INFRA requirements (01-06) addressed in code; final proof is the on-NAS Go/No-Go smoke test (criteria that need live VPN/NAS were NOT runnable in the dev sandbox).

## Recent Decisions

- **PIA region = CA Vancouver** — west-coast PF-capable (US has zero port forwarding; latency was the owner's concern but PF is mandatory for slskd).
- **Inline execution** of Phase 1 (not worktree subagents) — all owner-provided values were in context and 01-02/01-04 share docker-compose.yml; faster and conflict-free.
- **Owner deploys manually** in Container Manager (not auto-SSH) — DEPLOY.md is the handoff.
- **slskd-direct, not Soularr**; **v1 = music + books (Readarr best-effort)**; **staging→Manual-Import→auto-purge** cleanup (the 6th pain point).

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
