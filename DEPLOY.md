# Curator — Phase 1 Deploy Guide (Synology Container Manager)

Phase 1 stands up the **substrate**: gluetun (PIA/OpenVPN, kill-switch, port forwarding) +
slskd (Soulseek, routed through gluetun) + a Curator FastAPI health stub — all from one
`docker-compose.yml`, with the image built by GitHub Actions and pulled from Docker Hub.

> No application logic yet (gap detection, matching, slskd search, import, sharing) — those are
> Phases 2-6. Phase 1 only proves the substrate deploys and the stack comes online.

> **Phase 2 deploy deltas (State Ledger + *arr Adapter + Gap Detection).** If you already ran
> Phase 1, three things changed and must be reflected in your Container Manager compose:
> 1. **New `/db` bind-mount** — `/volume1/docker/curator/db:/db` holds the SQLite ledger. **Its
>    absence is the #1 cold-start crash** (`unable to open database file`): the app opens
>    `/db/curator.sqlite` on boot regardless of `DB_PATH`. Create the host dir (Step 2) AND add
>    the mount to the curator service.
> 2. **Curator now runs non-root** — `user: "${PUID}:${PGID}"` on the curator service (matches
>    slskd; security T-02-05). So `/volume1/docker/curator/db` MUST be owned `1031:65536` or the
>    non-root process can't write the ledger.
> 3. **Trigger detection by URL, not a second process** — `POST /detect` runs a detection pass on
>    the app's single DB connection. Do NOT `docker exec … python -m core.gap_detector` while the
>    container is running — a second process opening the same WAL DB gets `database is locked`.
>
> *arr/slskd URLs default to the NAS LAN IP (`192.168.86.37`) + published ports, since the *arr
> stack isn't reachable from curator by synobridge container name here. Override via `.env`
> (`LIDARR_URL`/`READARR_URL`/`SLSKD_URL`) if yours differ.

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
# 1. directories  (curator/db is the Phase-2 SQLite-ledger mount — REQUIRED, or curator crashes on boot)
sudo mkdir -p /volume1/docker/{gluetun,slskd,curator/config,curator/db} /volume1/data/{downloads/soulseek,media/music,media/books}
sudo chown -R 1031:65536 /volume1/docker/gluetun /volume1/docker/slskd /volume1/docker/curator /volume1/data
# (the -R chown above covers curator/db → owned 1031:65536, which the non-root curator container needs to write the ledger)

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

## Step 7 — Phase 2 verification (State Ledger + Gap Detection)
After redeploying the Phase 2 image:
```bash
# 1. cold start — app boots + startup migration runs
curl -s http://192.168.86.37:8674/healthz        # -> {"status":"ok","phase":2,"version":"0.2.0-phase2"}

# 2. trigger one detection pass against live Lidarr (single-connection; NOT docker-exec)
curl -s -X POST http://192.168.86.37:8674/detect  # -> {"status":"ok","detected":{"lidarr":N,...}}

# 3. dedup — re-run; the row count must NOT grow (STATE-02). Reads don't contend, so exec is fine:
sudo docker exec curator python -c "import sqlite3;print(sqlite3.connect('/db/curator.sqlite').execute('select status,count(*) from items group by status').fetchall())"
```
Pass = Lidarr reached, real missing/cutoff gaps recorded at `pending`, re-run adds zero rows, and
a Readarr fault (if any) logs + yields `readarr: 0` without taking the run down. Phase 2 **detects
and records** only — no downloading/importing yet (that's Phase 4).

## Step 8 — Phase 4 setup (Acquisition, Staging & Clean Import) — do on the Phase-4 teardown/redeploy

Phase 4 performs the first real downloads + imports. Files move like this:
**slskd downloads into a staging dir on `/data` → Curator calls the *arr Manual Import API → the
*arr hardlink-Moves the wanted files into its library root folder.** Curator never moves bytes
itself. For the Move to be an atomic hardlink (not a slow cross-volume copy — the #1 import-failure
cause), slskd, curator, and the *arr must all see the SAME `/data` tree at identical paths.

**4 setup items (all owner-side; Phase 4 code does NOT configure these — it assumes/verifies them):**

```bash
# 1. slskd DOWNLOAD directory -> inside /data  (this is SEPARATE from the shares you already set;
#    shares = slskd.yml `shares.directories`; downloads = slskd.yml `directories.downloads`).
#    Edit /volume1/docker/slskd/slskd.yml:
#      directories:
#        downloads:  /data/downloads/soulseek
#        incomplete: /data/downloads/soulseek/.incomplete
#    Must MATCH curator's STAGING_ROOT (default /data/downloads/soulseek). Then: sudo docker restart slskd

# 2. pre-create the staging root owned by the shared uid/gid (slskd writes here, the *arr reads/moves it)
sudo mkdir -p /volume1/data/downloads/soulseek/.quarantine
sudo chown -R 1031:65536 /volume1/data/downloads/soulseek      # slskd UMASK 002 keeps it group-writable for the *arr

# 3. curator /data mount: flip read-only -> read-write in docker-compose.yml
#      - /volume1/data:/data        # was :ro (Phase-1 stub). Phase 4 writes/purges staging dirs here.
#    then: sudo docker compose up -d curator   (recreate)

# 4. VERIFY path identity (make-or-break): the *arr must mount the FULL /volume1/data:/data
#    (not just a /music sub-path) AND have its root folder at /data/media/music (resp. /data/media/books),
#    so it can SEE /data/downloads/soulseek for the ManualImport and Move it as a hardlink.
sudo docker exec lidarr ls /data/downloads/soulseek            # must succeed (same path the *arr sees)
#    Lidarr UI -> Settings -> Media Management -> Root Folders should list /data/media/music.
#    (Phase-1 Go/No-Go already proved slskd->*arr hardlink capability on /data; this just confirms the
#     download path is visible to the *arr too.)
```

Then the D-11 share precondition (already done — shares live, count > 0) gates the first live download.
Plan **04-05** pauses at this gate and runs the live probes (slskd transfer-state strings, the real
ManualImport POST envelope, batchId routing) before pinning the offline fixtures to reality.
