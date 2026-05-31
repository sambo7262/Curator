# 04-05 Live Probe — Observed NAS Behavior

Recorded against the live Synology NAS to pin the three load-bearing assumptions
(A1 ManualImport envelope, A2 batchId routing, A3 transfer-state strings) before
the offline fixtures/constants are reconciled to reality in Task 3.

- NAS IP: `192.168.86.37`
- slskd web/API: `http://192.168.86.37:5030`
- Lidarr web/API: `http://192.168.86.37:8686`
- slskd downloads dir (container path): `/data/downloads/soulseek`  (host: `/volume1/data/downloads/soulseek`)

---

## Precondition — D-11 shares

- Shared file count (slskd UI → System → Shares): confirmed **> 0** (D-11 confirmed live 2026-05-31;
  a real download completed without leech-block, corroborating active shares).
- D-12 path-identity: ✅ `/data/downloads/soulseek` owned `1031:65536` mode `775`, same filesystem as
  `/data/media/music` (stat device `45 == 45`) → ManualImport Move is a hardlink/rename, not a copy.

---

## Observed

### A3 — slskd terminal transfer-state strings
The exact `state` value reported by slskd's API at each stage of a transfer.

- In-progress: `________________`  (not yet captured — optional)
- Terminal SUCCESS: `Completed, Succeeded`   ✅ observed 2026-05-31 (ZHU – BLACK MIDAS, 14 FLAC tracks)
- Terminal FAILED/ERRORED: `________________`  (expected family: "Completed, Errored")
- Terminal CANCELLED (if captured): `________________`  (expected family: "Completed, Cancelled")

Note: slskd `state` is a compound flag string `"<phase>, <completion>"`. Robust interpretation:
terminal iff it contains `Completed`; success iff it contains `Succeeded`; any other terminal
`Completed, *` is a failure → fall to next candidate. Remote filenames use `\` separators and a
`music\<artist>\<album>\...` layout (peer-side path).

### A2 — download landing path (batchId vs remote-folder)
Where slskd actually puts the finished files under the downloads dir.

- Did a `batchId` set on the enqueue change the path? `no batchId used (plain manual download)`
- Actual folder the files landed in (relative to `/data/downloads/soulseek/`):
  `BLACK MIDAS (2026)/<tracks>.flac`   ✅ observed 2026-05-31
- slskd uses ONLY the **last segment** of the remote folder as the local dir:
  remote `music\ZHU\BLACK MIDAS (2026)\…` → local `soulseek/BLACK MIDAS (2026)/`.
  No `<username>/` subdir, no `<batchId>/` subdir — tracks sit flat inside the album leaf.
- **DECISION (A2):** acquire.py uses the remote-folder-resolution route — resolve the import
  source as `staging_root / leaf(remote_dir)` post-completion (derive `leaf` from the transfer
  filename's directory portion), point ManualImport there, and purge/quarantine that folder.
  No batchId dependency. Satisfies IMPORT-01 (everything under /data → hardlink Move).

### A1 — Lidarr ManualImport POST envelope
The exact `POST /api/v1/command` request body Lidarr's own UI sends for a Manual Import.
Paste the raw JSON verbatim (key casing matters — `importMode` move/Move, whether
`quality` is the full QualityModel object, whether `albumReleaseId` is per-file or top-level).

Captured from the live Lidarr command queue entry (the `body` is the stored command body the UI POSTed).
Representative single file shown; all 14 tracks shared the identical key shape.

```json
{
  "name": "ManualImport",
  "importMode": "copy",
  "replaceExistingFiles": false,
  "sendUpdatesToClient": true,
  "files": [
    {
      "path": "/data/downloads/soulseek/BLACK MIDAS (2026)/ZHU - BLACK MIDAS - 01 - MIDAS INTRO.flac",
      "artistId": 15,
      "albumId": 14481,
      "albumReleaseId": 36749,
      "trackIds": [287087],
      "quality": {
        "quality": { "id": 6, "name": "FLAC" },
        "revision": { "version": 1, "real": 0, "isRepack": false }
      },
      "indexerFlags": 0,
      "disableReleaseSwitching": false
    }
  ]
}
```

Notes / anything surprising:
- **importMode is lowercase** (`"copy"` / `"move"`). Our [ASSUMED] `"Move"` (capital) was wrong casing.
  The UI default is `copy`; **Curator deliberately sends `"move"`** (D-09 atomic same-fs hardlink-rename,
  then purge staging). The observation pins the field name + casing; the value choice (move) is by design.
- `quality` is the **full QualityModel object** echoed from the manualimport candidate (not a bare id):
  `{ "quality": {id,name}, "revision": {version,real,isRepack} }`.
- `albumReleaseId` and `trackIds[]` are **per-file**. `indexerFlags` (int) + `disableReleaseSwitching` (bool)
  are present per-file.
- Per-file IDs (artistId/albumId/albumReleaseId/trackIds/quality) all come from the GET
  `/api/v1/manualimport?folder=…` candidate response → execute_import echoes them back in the POST.
- Command fields like `priority/status/queued/isExclusive/isLongRunning/id` are command-queue metadata
  added by Lidarr on accept — NOT part of the POST body Curator must send.
