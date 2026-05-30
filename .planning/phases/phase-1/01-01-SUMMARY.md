# Plan 01-01 Summary — Recon + Secrets Bootstrap

**Status:** Complete
**Requirements:** INFRA-06

## What was done
- Recorded all NAS-local values (owner-provided) in `NAS-RECON.md`: synobridge `172.20.0.0/16`,
  PUID/PGID `1031/65536`, `/volume1/data:/data` single tree, PIA `CA Vancouver`, ports, image.
- Created `.env.example` with every compose-interpolated key (real non-secret defaults baked:
  region, CIDR, PUID/PGID, DOCKERHUB_USER, TZ; secrets left blank).
- Created `.gitignore` (ignores `.env`, keeps `.env.example`) — no secret enters git.
- Documented the remaining on-NAS confirm steps (tun device, hardlink proof, genkey) in
  `NAS-RECON.md` and `DEPLOY.md`.

## Deviations
- The two "open" items (A3 *arr mount, A4 PUID/PGID) were resolved by the owner directly rather
  than via on-NAS `docker inspect` — confirm-at-deploy steps retained in NAS-RECON.md.
- gluetun/slskd `@sha256` digest pinning deferred to deploy (needs NAS/registry access); compose
  pins by version tag now with a note to add digests.

## INFRA-06
Single identical `/volume1/data:/data` mount across slskd/curator/*arr → atomic hardlinks.
Proof command shipped in `scripts/smoke-test.sh` (4d) and `DEPLOY.md`.

## Self-Check: PASSED
.env.example has all keys; .gitignore ignores .env; no real .env tracked.
