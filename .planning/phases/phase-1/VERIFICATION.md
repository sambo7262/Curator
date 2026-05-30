---
phase: 1
name: VPN-Routed Networking Foundation
status: passed
verified: 2026-05-30
method: live NAS deployment + smoke test + slskd Soulseek login
---

# Phase 1 Verification — VPN-Routed Networking Foundation

**Status: PASSED** — deployed to the Synology DS423+ and verified against all four ROADMAP success criteria on live hardware.

## Success Criteria

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | VPN egress = PIA (not home), no leak, kill-switch fail-closed | ✅ PASS | slskd egress `212.56.52.34`/`181.41.202.121` (PIA Vancouver) ≠ home `104.183.82.12`; `docker stop gluetun` → slskd zero egress |
| 2 | Non-US forwarded port obtained + slskd self-applies it + survives restart | ✅ PASS | Forwarded port `56034`, country Canada; port held across `docker restart gluetun slskd` |
| 3 | Curator reaches *arr by name + slskd via gluetun:5030; CI green; no baked secrets | ✅ PASS | slskd API alive via `gluetun:5030` (HTTP 200); GitHub Actions green → `sambo7262/curator` on Docker Hub; `docker history` clean |
| 4 | Single compose up + /data readable + hardlink-capable | ✅ PASS | `docker compose up -d` brought gluetun+slskd+curator online; Curator `/readyz` → `data_mount_present:true, data_readable:true` |

## Requirements

INFRA-01 ✅ · INFRA-02 ✅ · INFRA-03 ✅ · INFRA-04 ✅ · INFRA-05 ✅ · INFRA-06 ✅ — all six satisfied.

## Decisive evidence
slskd log: **`Logged in to the Soulseek server as "Scooby123987"`** — proves the full chain end-to-end: PIA tunnel up (Vancouver) → port forwarded (56034) → slskd authenticated to the Soulseek network, all VPN-protected and reachable on synobridge.

## Deviations / fixes during deploy
1. `.env` values with spaces/semicolons (`PIA_PF_REGION`, `SLSKD_API_KEY`) had to be quoted — smoke-test `.env` source parsing only (Compose was unaffected). Template + script fixed.
2. Smoke-test used `docker exec curator curl` but the `python:3.12-slim` image has no `curl`; switched the reachability/readyz checks to `curlimages/curl` sidecars on synobridge + added health-waits.
3. **Missing Soulseek network credentials** — initial compose set VPN + REST API key but not slskd's Soulseek login (`SLSKD_SLSK_USERNAME`/`SLSKD_SLSK_PASSWORD`); slskd connected to the VPN but couldn't log into Soulseek. Added both vars; resolved.

All three fixes committed and pushed to `main`.

## Deferred to later phases (not Phase 1 scope)
- slskd shares (currently sharing 0 dirs/0 files) — automated sharing is Phase 5 (SHARE-01/02).
- Curator app logic (gap detection, matching, download, import) — Phases 2-6.
- Optional firewall hardening for `:5030`/`:8674` (LAN/Tailscale-only).
