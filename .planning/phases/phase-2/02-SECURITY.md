---
phase: 02-state-ledger-arr-adapter-gap-detection
asvs_level: 1
block_on: high
threats_total: 13
threats_closed: 13
threats_open: 0
register_authored_at_plan_time: true
status: secured
audited: 2026-05-30
---

# Phase 2 Security Audit — State Ledger + *arr Adapter + Gap Detection

**Audited:** 2026-05-30
**ASVS Level:** 1
**Block policy:** `block_on: high`
**Disposition source:** plan-time STRIDE register (`register_authored_at_plan_time: true`) — verification only, no net-new scan.
**Implementation files:** READ-ONLY at audit time; the single open finding (T-02-05) was resolved by an orchestrator-applied compose change post-audit (see Audit Trail).

---

## Verdict

**SECURED** — 13 of 13 CLOSED; `threats_open: 0`.

The audit initially found 1 partial gap (T-02-05, "container non-root" sub-clause), classified
WARNING (below the `high` block threshold — the load-bearing ledger-integrity controls were all
present). It was resolved immediately by dropping container privileges: `user: "${PUID}:${PGID}"`
added to the `curator` service in `docker-compose.yml`, matching the existing `slskd` pattern.
All threats now have a verified mitigation or a documented accepted risk.

---

## Per-Threat Verification

| Threat ID | Category | Disposition | Status | Evidence |
|-----------|----------|-------------|--------|----------|
| T-02-01 | Info Disclosure | mitigate | CLOSED | `app/config.py:36-42` keys read via `os.getenv` only; no log/print of key anywhere (`grep` of adapters/config/core for key+log = none). `.gitignore` ignores `.env`/`*.env`, allows `.env.example`. CR-01: `app/adapters/lidarr.py:32-33` raises on empty Lidarr key; `app/adapters/readarr.py:32-33` raises → `gap_detector.build_adapters` (`app/core/gap_detector.py:75-77`) skips Readarr gracefully. |
| T-02-02 | Tampering | mitigate | CLOSED | `docker-compose.yml:84` `/volume1/docker/curator/db:/db` — own LOCAL mount, NOT under `/data`. `docker-compose.yml:78` `DB_PATH=/db/curator.sqlite`. `.env.example:35-37` documents 1031:65536 ownership + local-volume / no-NFS requirement. |
| T-02-SC | Tampering (supply chain) | mitigate/resolved | CLOSED | `app/requirements.txt:3` `httpx==0.28.1` (human-approved pinned). `app/requirements-dev.txt:5` `respx==0.22.0` (human-approved pinned). Approval recorded inline + 02-01-SUMMARY. |
| T-02-03 | Tampering / SQLi | mitigate | CLOSED | `app/state/repo.py` — every query uses `?` placeholders (lines 36-66, 71-74, 84-87, 92-95); no f-string into SQL. Only f-string-into-SQL is `app/state/db.py:86` `PRAGMA user_version = {i}` where `i` is the loop-controlled integer index (line 79), never user input. Status CHECK constraint at `app/state/schema.sql:17-19`. |
| T-02-04 | Tampering (status clobber) | mitigate | CLOSED | `app/state/repo.py:42-51` `ON CONFLICT(arr_app, arr_id) DO UPDATE SET` omits `status` and `discovered_at`. Proven by `app/tests/test_state_repo.py:88-100` `test_upsert_preserves_status` (status stays `imported` after re-detect). WR-06 durability: `app/state/db.py:39` `PRAGMA synchronous=FULL`. |
| T-02-05 | Tampering / Elevation (DB perms/location) | mitigate | **CLOSED** | Own local `/db` mount: `docker-compose.yml` ✓. Dir 1031:65536: `.env.example:35-36` ✓. WAL local only: `app/state/db.py:33` ✓. **Container non-root: NOW satisfied** — `user: "${PUID}:${PGID}"` added to the `curator` service in `docker-compose.yml` (mirrors `slskd`), so the container drops to UID 1031:65536. No Dockerfile `USER` needed: `python:3.12-slim` code is world-readable, the app only writes to the 1031-owned `/db` mount, and port 8674 is non-privileged. (Verify on next NAS deploy that the container starts as 1031:65536.) |
| T-02-06 | DoS (db locked) | ACCEPT | CLOSED | `busy_timeout` verified set: `app/state/db.py:41` `PRAGMA busy_timeout=5000`. Single-writer design enforced (`app/main.py:16-35` one retained writer connection). Documented accepted risk — see Accepted Risks log below. |
| T-02-07 | Info Disclosure (X-Api-Key) | mitigate | CLOSED | `app/adapters/lidarr.py:36` / `app/adapters/readarr.py:36` `X-Api-Key` set only as request header from injected key. No log line emits the key (logs reference `path`/record `%r` only: lidarr.py:96, readarr.py:81,102,118). Firewall keeps `X-Api-Key` out of core/state (`app/tests/test_adapter_protocol.py:24,67-78`). |
| T-02-08 | DoS (malformed *arr response) | mitigate | CLOSED | Readarr `_map`→None on non-dict/missing-id/exception: `app/adapters/readarr.py:101-119`. `_paged` swallows `httpx.HTTPError/ValueError/TypeError`→[]: `readarr.py:74-82`. Breaker→[] when open: `app/adapters/breaker.py:81-84`. WR-03 Lidarr `_map` skips missing-id: `app/adapters/lidarr.py:95-97`. BL-01 pagination guard against pageSize<=0/empty page: `lidarr.py:69-73`, `readarr.py:70-72`, `_MAX_PAGES=1000` cap (lidarr.py:40, readarr.py:39). |
| T-02-09 | Tampering / SSRF | mitigate | CLOSED | Base URLs env-wired to synobridge hosts: `docker-compose.yml:74,76`, `app/config.py:37-39`. `follow_redirects` never overridden → relies on httpx documented default `False` (no cross-host redirect following); no `follow_redirects=True` anywhere in adapters. `timeout=30.0` bounds every request: `lidarr.py:63`, `readarr.py:65`. NOTE: mitigation depends on the httpx library default, not an explicit flag — see Defense-in-Depth note. |
| T-02-10 | DoS (hung Readarr) | mitigate | CLOSED | Per-request `timeout=30.0` (`readarr.py:65`) + breaker short-circuit after `fail_threshold` (`app/adapters/breaker.py:81-84`). WR-04 half-open cooldown recovery: `breaker.py:50-52,85-94`. Lidarr iterates independently (`app/core/gap_detector.py:34-39`). |
| T-02-11 | DoS (Readarr fault aborts detect) | mitigate | CLOSED | Adapters iterated independently and Readarr breaker-wrapped: `app/core/gap_detector.py:34-39`, `build_adapters` wraps Readarr in `CircuitBreaker` (gap_detector.py:74). Proven by `app/tests/test_gap_detector.py:179-192` `test_readarr_fault_does_not_gate_music` (counts `{lidarr:4, readarr:0}`, all 4 music rows persisted, no raise). |
| T-02-12 | Tampering (firewall) | mitigate | CLOSED | `app/core/gap_detector.py:17-18` imports only `ArrAdapter` Protocol + `state.repo`; zero *arr field names. Grep test `app/tests/test_adapter_protocol.py:67-78` `test_core_state_have_no_arr_field_names` asserts `foreignAlbumId|X-Api-Key|records\[|profileId` appear nowhere in `app/core` or `app/state` (comments stripped). |
| T-02-13 | Elevation / scope-creep | mitigate | CLOSED | `app/core/gap_detector.py:82-98` `__main__` is one-shot only (no `while True` / `time.sleep` / `apscheduler` / `slskd` in the module — full file inspected). Scheduling deferred to Phase 5 per inline comment (line 84). |

---

## Open / Partial Findings

**None.** The one finding raised during the audit (WARNING-02-05, below) was resolved before close.

### WARNING-02-05 — Curator container ran as root → RESOLVED

- **Threat:** T-02-05 (Tampering / Elevation).
- **Declared mitigation (plan):** "DB file perms/location: own local /db mount, dir 1031:65536, WAL local only, **container non-root**."
- **Original gap:** No privilege drop — `curator` service had no `user:` directive, so the container ran as in-namespace root (UID 0).
- **Resolution (2026-05-30):** Added `user: "${PUID}:${PGID}"` to the `curator` service in `docker-compose.yml`, matching `slskd` (compose line 41). The container now drops to 1031:65536. No Dockerfile `USER` was required — the `python:3.12-slim` code layer is world-readable, the app writes only to the 1031-owned `/db` mount, and uvicorn binds the non-privileged port 8674.
- **Deploy-time confirmation:** on the next NAS deploy, confirm the curator container actually starts and the ledger writes succeed as 1031:65536 (no permission error on `/db`). This rolls into UAT test 6 (the deferred live smoke).

---

## Accepted Risks Log

| ID | Threat | Risk | Justification | Scope/Expiry |
|----|--------|------|---------------|--------------|
| T-02-06 | DoS — `database is locked` under concurrent writers | A second concurrent writer could hit a busy timeout error after 5s. | Phase 2 is single-writer by design (one retained writer connection, `app/main.py`); WAL + `busy_timeout=5000` (`app/state/db.py:41`) absorb incidental contention. Multi-writer is explicitly out of Phase-2 scope. | Revisit when multi-writer paths (search/import, Phases 4-5) are added. |
| ~~T-02-05 (non-root sub-clause)~~ | ~~Curator container runs as root~~ | — | **RESOLVED (not accepted)** — privileges dropped via `user: "${PUID}:${PGID}"` on the curator service; no longer a residual risk. | Confirm at next deploy. |

---

## Defense-in-Depth Notes (informational, not blocking)

- **T-02-09 redirect handling relies on the httpx default.** `follow_redirects` is never set in the adapters, so SSRF-via-redirect protection rests on httpx's documented default of `False`. This is correct today but is an implicit dependency on library behavior; setting `follow_redirects=False` explicitly on the `httpx.Client` would make the SSRF mitigation self-evident and resilient to a future default change. Not a gap for this audit.

---

## Unregistered Flags (new attack surface during implementation)

None. No `## Threat Flags` section exists in any of `02-01..02-04-SUMMARY.md`; the executor
declared no new attack surface. The plan-time register is authoritative and complete for this phase.

---

## Tests Cited as Mitigation Proof (verified present)

- `app/tests/test_state_repo.py:88` `test_upsert_preserves_status` — proves T-02-04.
- `app/tests/test_gap_detector.py:179` `test_readarr_fault_does_not_gate_music` — proves T-02-11.
- `app/tests/test_adapter_protocol.py:67` `test_core_state_have_no_arr_field_names` — proves T-02-12 (firewall grep).

---

## Security Audit 2026-05-30

| Metric | Count |
|--------|-------|
| Threats in register | 13 |
| Closed | 13 |
| Open | 0 |
| Accepted risks (documented) | 1 (T-02-06, single-writer DoS) |
| Findings raised | 1 (WARNING-02-05) |
| Findings resolved this session | 1 (WARNING-02-05 — container privilege drop) |

**Outcome:** SECURED. Register was plan-time authored (verify-only, no net-new scan). 12 threats verified
CLOSED against the post-code-review implementation; the 1 WARNING (container ran as root) was fixed in
`docker-compose.yml` (`user: "${PUID}:${PGID}"`) and re-verified. `threats_open: 0`.

**Deferred to next NAS deploy (rolls into UAT test 6):** confirm the curator container starts as
1031:65536 and ledger writes to `/db` succeed; confirm the T-02-09 SSRF mitigation (httpx `follow_redirects`
default `False`) — optionally hardened by setting it explicitly on the client (informational, non-blocking).
