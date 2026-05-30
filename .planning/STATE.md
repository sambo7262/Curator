# Project State

<!-- GSD reads this file first to understand where the project is. Keep it current. -->

## Current Status

**Phase**: 2 - State Ledger + *arr Adapter + Gap Detection — ⚙ IN PROGRESS
**Plan**: 2 of 4 complete (4 plans in 3 waves)
**Status**: 02-02 ✓ COMPLETE — the SQLite-WAL ledger (the persistent spine) landed: schema.sql (single `items` table, UNIQUE(arr_app, arr_id) dedup, 7-value status CHECK enum, foreign_id Phase-3 anchor, two indexes), db.py (WAL connect + idempotent PRAGMA user_version migration runner), repo.py (status-preserving ON CONFLICT upsert + get_gap/set_status/list_by_status), a FastAPI startup migration hook in main.py, and 5 offline pytest proofs (restart-durability, enum CHECK, dedup, status-preservation, idempotent migrations). The load-bearing STATE-02 rule is proven: a re-detect upsert never clobbers an acted-on row's status. STATE-01 + STATE-02 complete. 02-01 remains ✓ COMPLETE (Wave 0 test substrate + config + /db mount). Phase 1 remains ✓ COMPLETE (deployed & verified on NAS — slskd logged into Soulseek via PIA Vancouver, port 56034).
**Last action**: Executed + finalized 02-02 (3 atomic feat commits: 2169575 db+schema, e7a81b0 repo, c7cc311 startup-hook+tests). Local verify = AST-parse + grep + behavioral sqlite3 run; all 5 named pytest proofs pass locally on Python 3.9 (stdlib sqlite3 only, no new deps) — full suite 8 passed.
**Next action**: Execute the rest of Wave 2: 02-03 (ArrAdapter Protocol + GapItem, LidarrAdapter missing+cutoff, defensive ReadarrAdapter + circuit breaker). Then Wave 3: 02-04 (gap_detector wiring detect_gaps adapters→ledger + end-to-end dedup/Readarr-degradation proofs + one-shot trigger). NOTE: behavioral pytest verifies are non-fatal locally (Python-3.9 + offline sandbox) — the real green/red gate is `pytest app/tests -q` on Python 3.12 at CI/NAS; run it before marking the phase verified.

## Active Phase Detail

Phase 1 delivers the substrate: gluetun (PIA/OpenVPN, kill-switch, port forwarding, control-server auth) + slskd (shared netns, native PF sync, user 1031:65536) + Curator FastAPI health stub, all from one compose pulling a CI-built Docker Hub image, with a single identical /volume1/data mount for atomic hardlinks. All six INFRA requirements (01-06) addressed in code; final proof is the on-NAS Go/No-Go smoke test (criteria that need live VPN/NAS were NOT runnable in the dev sandbox).

## Recent Decisions

- **PIA region = CA Vancouver** — west-coast PF-capable (US has zero port forwarding; latency was the owner's concern but PF is mandatory for slskd).
- **Inline execution** of Phase 1 (not worktree subagents) — all owner-provided values were in context and 01-02/01-04 share docker-compose.yml; faster and conflict-free.
- **Owner deploys manually** in Container Manager (not auto-SSH) — DEPLOY.md is the handoff.
- **slskd-direct, not Soularr**; **v1 = music + books (Readarr best-effort)**; **staging→Manual-Import→auto-purge** cleanup (the 6th pain point).
- **Status-preserving upsert (02-02)** — the `ON CONFLICT(arr_app, arr_id)` SET clause omits BOTH `status` and `discovered_at`, so a re-detect of an acted-on/first-seen row never clobbers its lifecycle status (the #1 STATE-02 pitfall, proven by `test_upsert_preserves_status`).
- **WAL + PRAGMA user_version migrations (02-02)** — idempotent versioned runner reading schema.sql relative to db.py; the only f-string-into-SQL is the loop-controlled `user_version` bump; all data queries use `?` placeholders.

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
