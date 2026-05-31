# 05-05 Live Probe — slskd application/shares shape (A3) + *arr queue album-id field (A2)

Recorded against the live Synology NAS to pin the two LOW-risk Phase-5 assumptions before the offline
fixtures are reconciled. Mirrors the Phase-4 `04-05-LIVE-PROBE.md` format.

**STATUS: PENDING owner action (this session has no NAS access).** The offline code already uses the
research-confirmed shapes (`shares.files` int, queue `albumId`), so NOTHING in Phase 5 is blocked on
this probe — it is a truth-pin, not a build dependency. The two probes below are run by the owner on
the next NAS teardown/rebuild and pasted into the "Observed" section; the executor (or the owner) then
reconciles the fixtures if anything differs.

- NAS IP: `192.168.86.37`
- slskd web/API: `http://192.168.86.37:5030`
- Lidarr web/API: `http://192.168.86.37:8686`
- Readarr web/API (if reachable): `http://192.168.86.37:8787`

> Security (T-05-23): the probe commands reference `$SLSKD_API_KEY` / `$LIDARR_API_KEY` as env vars.
> Paste only the JSON BODY (`.shares` object / `.records[0] | keys`) — never the `X-API-Key` header.

---

## Probe 1 — A3: slskd application/shares shape + rescan status code

```bash
# (a) shares shape — expect an object with an integer `files` (and `directories`)
curl -s http://192.168.86.37:5030/api/v0/application -H "X-API-Key: $SLSKD_API_KEY" | jq '.shares'

# (b) rescan — expect 204 (started) or 409 (a scan is already running)
curl -s -o /dev/null -w "%{http_code}\n" -X PUT http://192.168.86.37:5030/api/v0/shares \
  -H "X-API-Key: $SLSKD_API_KEY"
```

### Observed (A3)
- `.shares` object: `________________`  **[ASSUMED — pin live on NAS]**
  - Offline-assumed shape (from RESEARCH §slskd Shares API, `[VERIFIED]` against the maintained
    gethomepage/homepage widget which reads `appData.shares?.files`):
    ```json
    { "directories": 7, "files": 1234, "scanning": false, "scanPending": false }
    ```
  - Confirm `files` is an **integer** (the shared-file count Curator reads via
    `slskd.get_shared_file_count()` → `body["shares"]["files"]`).
- `PUT /api/v0/shares` status code: `____`  **[ASSUMED — pin live on NAS]**
  - Offline-assumed: **204** on start, **409** if a scan is already running
    (`rescan_shares()` returns `True` on 204, `False` on 409).

---

## Probe 2 — A2: *arr queue record album-id field name

```bash
# Lidarr (primary) — expect the per-record album identity field to be `albumId`
curl -s 'http://192.168.86.37:8686/api/v1/queue?page=1&pageSize=20' \
  -H "X-Api-Key: $LIDARR_API_KEY" | jq '.records[0] | keys'

# Readarr (best-effort, if a queue is reachable) — expect `bookId`
curl -s 'http://192.168.86.37:8787/api/v1/queue?page=1&pageSize=20' \
  -H "X-Api-Key: $READARR_API_KEY" | jq '.records[0] | keys'
```

### Observed (A2)
- Lidarr `.records[0] | keys`: `________________`  **[ASSUMED — pin live on NAS]**
  - Offline-assumed: the per-record album identity field is **`albumId`** (an integer); Curator's
    `LidarrAdapter.get_queue_status` matches `str(rec.get("albumId")) == item.arr_id`. The offline
    fixture `tests/fixtures/lidarr_queue.json` carries a single `downloading` record with `albumId: 42`.
  - (If the live queue is empty, run the probe while a Usenet grab is active, or confirm the field
    name from the slskd/Lidarr Swagger `Queue` schema.)
- Readarr `.records[0] | keys`: `________________`  **[ASSUMED — pin live on NAS]**
  - Offline-assumed: the per-record book identity field is **`bookId`** (Readarr is best-effort —
    a fault degrades `get_queue_status` to `False`, never gates music; ARR-02).

---

## Reconciliation (executor, AFTER the owner pastes the observations)

Once the two probe outputs are pasted above:

1. If `.shares.files` is an integer and `PUT /api/v0/shares` returns 204/409 → **A3 confirmed**, no
   code change; just drop the `[ASSUMED]` markers here and in
   `tests/fixtures/slskd/application.json`.
2. If the Lidarr queue record's album field is `albumId` → **A2 confirmed**, no code change; drop the
   `[ASSUMED]` markers here and in `tests/fixtures/lidarr_queue.json`.
3. If EITHER shape differs from the assumption: fix the adapter read + the fixture to the live truth
   (truth → fixture → test, **never weaken an assertion**), then re-run:
   ```bash
   cd app && python3 -m pytest tests/test_shares.py tests/test_lidarr_adapter.py -q
   ```
   and record the mismatch + the fix in this file (mirroring Phase-4 04-05's reconciliation section).

**Offline status at scaffold time (2026-05-31):** `tests/test_shares.py` + `tests/test_lidarr_adapter.py`
both pass (27 tests) against the `[ASSUMED]` fixtures — the offline code is ready and uses the
research-confirmed shapes, so the live probe only confirms (it does not unblock).
