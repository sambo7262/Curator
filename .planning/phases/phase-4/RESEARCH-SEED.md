# Phase 4 Research Seed — Acquisition, Staging & Clean Import

> Pre-planning directives captured before Phase 4 research/discuss runs.
> The Phase 4 researcher (`gsd-phase-researcher`) and planner MUST read and address these.

## PRECONDITION (BLOCKING for live testing): slskd sharing must be live BEFORE any real download

**Sequencing risk (raised by owner 2026-05-30):** Phase 4 performs Curator's *first real slskd
downloads*. But automated share management (SHARE-01/SHARE-02) is not built until Phase 5. On
Soulseek a zero-share ("leech") account gets queue-deprioritized, throttled, or blocked — so a
live Phase 4 download test against real peers with nothing shared is genuinely unreliable and
risks the account.

**Resolution (owner-approved):** Do NOT reorder phases and do NOT pull Phase 5's automation
forward. Instead, **basic slskd sharing is configured MANUALLY in slskd itself before the first
live Phase 4 download test.** This is slskd *config* (a Phase 1 infra concern), needs zero Curator
orchestration, and is distinct from SHARE-01/02 (Curator *programmatically ensuring* shares exist,
stay scanned, count > 0, survive restarts — that stays in Phase 5).

Two distinct things, do not conflate:
1. **slskd-level shares (THIS precondition, manual, before Phase 4 live test)** — point slskd at
   the library dirs so the account is a sharer, not a leecher.
2. **SHARE-01/02 (Phase 5, Curator-automated)** — self-healing: verify count > 0, re-scan,
   survive restarts, surface if sharing breaks.

### Clarifying "read-only" (two different axes — do NOT confuse them)
- **slskd → peers: shares are download-only to peers, structurally.** The Soulseek protocol has no
  "write into someone else's share" capability. Peers can browse/download FROM a shared dir; they
  can never write INTO it via slskd. So "share the library" = slskd serves it out only; sharing
  never lets a remote peer modify your library. This is inherent to slskd — nothing to configure.
- **Filesystem writability: the library dirs ARE writable, and that is correct.** Lidarr/Readarr
  import media INTO `/data/media/music` + `/data/media/books`. That write path is internal (your
  own *arr import process, local volume) and is untouched by sharing. Do NOT chmod the library
  read-only — that would break *arr imports. slskd needs only READ access to serve peers; the dir
  staying writable for *arr is expected.
- **Therefore:** share the *clean library*. Do **NOT** share the download/staging tree
  (`/data/downloads/soulseek` + the per-item quarantine dirs Phase 4 creates) — that is the
  incoming/unverified landing zone; sharing it would re-share junk/in-progress transfers.

### Manual slskd share setup — clear, error-free procedure

Context (from DEPLOY.md + owner env): slskd runs `network_mode: service:gluetun`, mounts the
shared tree `/volume1/data` → `/data` (identical path across all containers), user `1031:65536`,
PIA Vancouver with port forwarding live (listen port synced, was 56034). Library roots inside the
tree: `/data/media/music` (Lidarr), `/data/media/books` (Readarr).

**Golden rules to avoid errors:**
- Share the **container-internal path** (`/data/media/music`), NOT the host path
  (`/volume1/data/media/music`). slskd only sees `/data`.
- Share the **library**, NOT the download/staging tree. slskd serves shares download-only to peers
  (they can never write into them), so the library being writable by *arr is fine — leave it as-is.
- Shares must live **inside the already-mounted `/data` tree** so no new bind-mount is needed.
- Port forwarding must already be working (Phase 1 ✓) or peers can't reach you even with shares.

**KNOWN GOTCHA — the slskd UI is read-only by default.** slskd ships with remote configuration
DISABLED, so the web UI settings area is view-only and has NO "add share" button. This is expected,
not a broken image. Two consequences:
- Prefer **Option A (slskd.yml)** — works regardless of UI lock state, matches our declarative stack.
- If you want the UI editable, add `SLSKD_REMOTE_CONFIGURATION: "true"` to the slskd service env and
  recreate the container. BUT: config precedence is **env vars > slskd.yml > UI**. Anything already
  set via `SLSKD_*` env stays greyed/locked in the UI even when remote config is on (the env wins).
  So do NOT set shares via env if you intend to manage them in the UI — keep shares in slskd.yml
  (or let the UI write them), never both.

**Option A — slskd.yml (preferred, declarative, survives restarts):**
In slskd's config file (`/volume1/docker/slskd/slskd.yml`, mounted to `/app/slskd.yml`):
```yaml
shares:
  directories:
    - "/data/media/music"     # clean library — music (slskd serves it download-only to peers)
    - "/data/media/books"     # clean library — books (best-effort)
  # Share the LIBRARY only. Do NOT add /data/downloads/soulseek or staging dirs — that's the
  # incoming/unverified landing zone. slskd never lets peers write into shares regardless.
```
Then restart slskd: `docker restart slskd` (or recreate via compose).

**Option B — slskd web UI (quick, but confirm it persists to slskd.yml):**
1. Open the slskd web UI (reached via gluetun's published port — `http://<NAS-IP>:5030`, per
   INFRA-03; never `http://slskd`).
2. **System → Shares** → add `/data/media/music` (and `/data/media/books`).
3. **Save**, then **Rescan Shares**.

**Verify BEFORE running any Phase 4 live download (must pass):**
- slskd web UI **Shares** page shows **shared file count > 0** after the scan completes.
- API check: `GET /api/v0/shares` (with `X-API-Key: $SLSKD_API_KEY`) returns the directories with
  a non-zero file count.
- Sanity: the shared paths resolve inside the container (`docker exec slskd ls /data/media/music`
  lists real albums). If empty here, the mount/path is wrong — fix the mount, not the share config.

**Common failure modes (and the fix):**
- *Shared count stays 0* → wrong path (used host path instead of `/data/...`), or scan not
  triggered, or the dir is genuinely empty inside the container. Verify with `docker exec`.
- *Peers still can't download from you* → port forwarding not actually applied to slskd's listen
  port (re-check Phase 1 PF sync), or kill-switch dropped the tunnel.
- *Permission errors on scan* → PUID/PGID mismatch; slskd must run as `1031:65536` to read the
  library. Read access is all slskd needs; leave the library writable so *arr can still import —
  do NOT chmod it read-only.

The Phase 4 plan MUST include a verification step / precondition note: "slskd shares configured
and shared-file count > 0" gates the first live download test. Phase 4 code itself does not
configure shares (that's Phase 5) — it only assumes/verifies they exist for live testing.

---
*Seed created 2026-05-30. Phase 4 = ACQ-01/02/03 + IMPORT-01..05.*
