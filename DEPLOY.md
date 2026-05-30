# Curator — Phase 1 Deploy Guide (Synology Container Manager)

Phase 1 stands up the **substrate**: gluetun (PIA/OpenVPN, kill-switch, port forwarding) +
slskd (Soulseek, routed through gluetun) + a Curator FastAPI health stub — all from one
`docker-compose.yml`, with the image built by GitHub Actions and pulled from Docker Hub.

> No application logic yet (gap detection, matching, slskd search, import, sharing) — those are
> Phases 2-6. Phase 1 only proves the substrate deploys and the stack comes online.

## Your environment (already wired into the files)
- synobridge subnet `172.20.0.0/16` → `FIREWALL_OUTBOUND_SUBNETS`
- PUID/PGID `1031/65536`, single `/volume1/data:/data` mount
- PIA region **CA Vancouver** (PF-capable; US has no port forwarding)
- Image `sambo7262/curator` (public); CI secrets already set in the repo
- Ports: Curator `8674`, slskd web `5030` (published via gluetun) — **LAN/Tailscale only, never WAN**

## Step 1 — Let CI build the image
The repo already has `.github/workflows/docker-publish.yml` and the `DOCKERHUB_USERNAME` /
`DOCKERHUB_TOKEN` secrets. On the next push to `main`, Actions builds `linux/amd64` and pushes
`sambo7262/curator:latest`. Confirm the run is green and the tag appears on Docker Hub.

## Step 2 — Prep the NAS (one-time)
SSH to the Synology and:
```bash
# 1. directories
sudo mkdir -p /volume1/docker/{gluetun,slskd,curator/config} /volume1/data/{downloads/soulseek,media/music,media/books}
sudo chown -R 1031:65536 /volume1/docker/gluetun /volume1/docker/slskd /volume1/docker/curator /volume1/data

# 2. preconditions
ls -l /dev/net/tun                         # must exist
docker network inspect synobridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'   # expect 172.20.0.0/16

# 3. hardlink proof (INFRA-06)
touch /volume1/data/downloads/soulseek/_hl && ln /volume1/data/downloads/soulseek/_hl /volume1/data/media/music/_hl \
  && echo HARDLINK_OK || echo HARDLINK_FAILED; rm -f /volume1/data/downloads/soulseek/_hl /volume1/data/media/music/_hl

# 4. gluetun control-server API key (used by BOTH gluetun and slskd)
docker run --rm qmcgaw/gluetun genkey
```

## Step 3 — Create `.env` (host-local, never committed)
Copy `.env.example` to `.env` next to the compose file and fill in the blanks:
- `PIA_USER` / `PIA_PASSWORD` — your PIA OpenVPN credentials
- `GLUETUN_API_KEY` — the genkey output from Step 2 (same value used by gluetun + slskd)
- `SLSKD_API_KEY` — any `role=...;cidr=...;<16+ char secret>` value
- `LIDARR_API_KEY` / `READARR_API_KEY` — from each *arr's Settings → General
- region/CIDR/PUID/PGID/DOCKERHUB_USER/TZ are pre-filled with your values
```bash
chmod 600 .env
```

## Step 4 — Deploy
In Container Manager: **Project → Create**, point it at this folder's `docker-compose.yml`
(it reads the sibling `.env`). Or via SSH:
```bash
docker compose config        # validate
docker compose pull
docker compose up -d
docker compose ps            # gluetun + slskd + curator should be Up/healthy
```

## Step 5 — Firewall rule
Open Curator's `8674` (and slskd's `5030` if you want the web UI) in the Synology firewall,
scoped to your **LAN (`192.168.86.0/24`) and/or Tailscale range only — never the internet**.
The VPN privacy model depends on no inbound WAN ports at home.

## Step 6 — Go/No-Go
```bash
bash scripts/smoke-test.sh
```
Must print `GO`. Hard NO-GO conditions: IP leak when gluetun is stopped, US VPN country,
forwarded port 0, stale port after restart, baked secrets in the image, or hardlink failure.

When it's GO, Phase 1 is complete → next is `/gsd:plan-phase 2`.
