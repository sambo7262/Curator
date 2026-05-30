# Plan 01-04 Summary — Assemble + Go/No-Go

**Status:** Complete (final NAS Go/No-Go pending deploy — see DEPLOY.md)
**Requirements:** INFRA-03, INFRA-04, INFRA-06

## What was done
- Completed `docker-compose.yml` with the `curator` service: plain synobridge member,
  `image: ${DOCKERHUB_USER}/curator:latest`, `SLSKD_URL=http://gluetun:5030` (NEVER http://slskd),
  Lidarr `8686` + Readarr `8787` env, `ports: 8674`, `/volume1/data:/data:ro` (Phase-1 read-only
  proof), and the single top-level `networks: synobridge: {external: true}`.
- One valid compose file: gluetun + slskd + curator, exactly one external network.
- `scripts/smoke-test.sh` — runnable go/no-go covering all four ROADMAP criteria with PASS/FAIL
  and non-zero exit on any hard NO-GO (IP leak, US region, port 0, stale port, baked secrets,
  hardlink fail). `bash -n` clean.
- `DEPLOY.md` — manual Container Manager deploy guide (owner deploys; not auto-SSH).

## Verification
- YAML sanity: 3 services, single external network, slskd has no ports/networks.
- Full-stack Go/No-Go runs on the NAS via `scripts/smoke-test.sh` after `docker compose up -d`.

## Self-Check: PASSED (file-level); NAS Go/No-Go deferred to deploy
