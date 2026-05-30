# Phase 1 — NAS Recon Record

**Resolved:** 2026-05-30 (values owner-provided; CONFIRM on the NAS before/at first deploy)

This file records the NAS-local values that Plan 01-01 was to discover. The owner supplied them
directly, so they are baked into `.env.example` and `docker-compose.yml`. The owner should still
run the confirm commands below at deploy time — if `docker inspect` disagrees, the inspected
value wins.

## Resolved values

| Item | Value | Source | Confirm command |
|------|-------|--------|-----------------|
| synobridge subnet (A-CIDR) | `172.20.0.0/16` (gw `172.20.0.1`) | owner | `docker network inspect synobridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'` |
| PUID / PGID (A4) | `1031` / `65536` | owner | `id <media-user>` |
| *arr `/data` mount (A3) | `/volume1/data:/data` (single tree — Lidarr confirmed) | owner | `docker inspect lidarr --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'` |
| Lidarr API | `http://lidarr:8686` | owner | — |
| Readarr API | `http://readarr:8787` | owner | — |
| PIA region | `CA Vancouver` (PF-capable, west-coast) | owner | live list: see RESEARCH Verification Protocol |
| Docker Hub image | `sambo7262/curator` (public) | owner | — |
| CI secrets | `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` — already added to repo | owner | GitHub repo → Settings → Secrets |
| FIREWALL_OUTBOUND_SUBNETS | `172.20.0.0/16` (does NOT overlap PIA's 10.x tunnel CIDR ✓) | derived | — |

## Still to do on the NAS (Wave-0 confirm, at deploy)

- [ ] `ls -l /dev/net/tun` exists (gluetun precondition).
- [ ] Hardlink smoke test prints OK:
  ```
  mkdir -p /volume1/data/downloads/soulseek /volume1/data/media/music && \
  touch /volume1/data/downloads/soulseek/_hl && \
  ln /volume1/data/downloads/soulseek/_hl /volume1/data/media/music/_hl && echo HARDLINK_OK || echo HARDLINK_FAILED; \
  rm -f /volume1/data/downloads/soulseek/_hl /volume1/data/media/music/_hl
  ```
- [ ] Generate the gluetun control-server API key: `docker run --rm qmcgaw/gluetun genkey`
      → put it in `.env` as BOTH `GLUETUN_API_KEY` (it's referenced by gluetun and slskd).
- [ ] Generate an slskd API key (format `role=...;cidr=...;<16-255 char secret>`) → `.env` `SLSKD_API_KEY`.
- [x] amd64 digests captured and pinned in compose (2026-05-30, verified via `docker manifest inspect`):
      - gluetun `v3.41.1@sha256:2f33c71e5e164fcd51a962cb950134df25155593edf0c3e1201f888d027049b4`
      - slskd `0.25.1@sha256:4fe0c6f628efaa9218bf2fa5cc779cac4fe261e74ff617514587af1dc36c8d54`

## Hardlink / INFRA-06 note
`/volume1/data:/data` is a single tree mounted identically into slskd, curator, and the *arr →
atomic hardlink imports work (no cross-device EXDEV copies). This is the #1 import-failure cause,
avoided by construction.
