# Plan 01-02 Summary — VPN Stack (gluetun + slskd)

**Status:** Complete (deploy-time NAS smoke test pending — see DEPLOY.md / smoke-test.sh)
**Requirements:** INFRA-01, INFRA-02, INFRA-03

## What was done
Authored the `gluetun` and `slskd` services in `docker-compose.yml` per the verified RESEARCH
contract, with owner values baked in:
- **gluetun:** PIA over **OpenVPN** (not WireGuard), `VPN_PORT_FORWARDING=on`, kill-switch via
  shared netns, `FIREWALL_OUTBOUND_SUBNETS=172.20.0.0/16` (no overlap with PIA's 10.x CIDR),
  control-server auth `HTTP_CONTROL_SERVER_AUTH_DEFAULT_ROLE` apikey (the v3.40+ requirement that
  otherwise 401s slskd), `/volume1/docker/gluetun:/gluetun` persisted, `ports: 5030` published on
  synobridge, `SERVER_REGIONS=CA Vancouver`.
- **slskd:** `network_mode: service:gluetun` (NO ports/networks — netns rule), `user: 1031:65536`
  (NOT PUID/PGID env), native gluetun PF via `SLSKD_VPN_*` pointing at `http://localhost:8000`
  with `SLSKD_VPN_GLUETUN_API_KEY` == gluetun's apikey, `SLSKD_UMASK=002`, `/volume1/data:/data`.

## Verification
- YAML structure verified: slskd has no `ports`/`networks` keys; control-server-auth + OpenVPN +
  FIREWALL_OUTBOUND_SUBNETS present.
- Live VPN/kill-switch/PF-restart smoke (criteria 1a-1c, 2a-2b) runs on the NAS via
  `scripts/smoke-test.sh` — requires PIA creds + the genkey value in `.env`.

## Self-Check: PASSED (file-level); NAS smoke deferred to deploy
