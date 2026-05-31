# Phase 4: Acquisition, Staging & Clean Import - Context

**Gathered:** 2026-05-31
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 4 closes the acquisition loop for a single real gap, end-to-end:

**trigger slskd search (ACQ-01) → download the gate-selected candidate into an isolated per-item staging/quarantine dir (ACQ-02, IMPORT-01) → watch to completion, handling stalls/partials (ACQ-03) → Manual-Import ONLY the wanted files via the *arr command API (IMPORT-02) → verify the item actually imported into `/volume1` (IMPORT-03) → trigger a Plex scan (IMPORT-04) → auto-purge the staging dir / quarantine on failure (IMPORT-05).**

This phase performs Curator's **first real slskd downloads**. It consumes the Phase 3 decision contract (`gate.evaluate` + `selector.select`) — it does NOT re-implement matching/quality. It does NOT build the autonomous daemon, scheduling, grace-window/Usenet-race logic, retry-backoff memory, or programmatic share self-healing — all of that is Phase 5. Phase 4 is the single-item mechanism; Phase 5 makes it run itself.

</domain>

<decisions>
## Implementation Decisions

### Download stall & fallback (ACQ-03)
- **D-01:** Declare a download dead via **no-progress stall detection** (no bytes for N minutes, configurable, default ~10 min) — NOT a fixed wall-clock deadline. Tolerant of slow but legitimate Soulseek peers.
- **D-02:** On death (stall, partial, or hard fail): **fall to the next-best gate-accepted candidate** from the same search. If candidates are exhausted, **surface the gap as stuck and back off** (do not loop). Never hold a slot forever.

### Import success bar (IMPORT-03/04)
- **D-03:** The bar for DONE is **`*arr`-confirmed import into `/volume1`** — re-query the adapter and confirm the item left the wanted/missing list. "Downloaded" never counts as "imported."
- **D-04:** The **Plex scan is fire-and-forget**: trigger it, never block completion or the staging purge on it, and do **not warn loudly** on a Plex hiccup — a quiet debug-level log only. Rationale (owner): the ~10k-track library has no metadata-mismatch problems today, so Plex is a trusted downstream view, not a gate. (`*arr` remains the source of truth.)

### Staging purge / failure handling (IMPORT-05)
- **D-05:** **Verified import → purge the per-item staging dir immediately.**
- **D-06:** **Terminal or ambiguous failure** (e.g. downloaded but Manual Import rejected the files) **→ move the staging dir into a quarantine area, record the failure reason, surface it, and auto-purge the quarantine after a TTL (or on the next run).** Debuggable without unbounded junk; still zero manual deletion ever. Do NOT keep failed staging indefinitely (violates hands-off goal); do NOT blind-purge on failure (destroys diagnostic evidence).

### Search → selection orchestration (ACQ-01/02)
- **D-07:** Issue the slskd search, **wait a fixed collection window** (configurable, default ~8–15 s) for results to trickle in, build `Candidate` objects from the accumulated results, then run `gate.evaluate` **once** over the full set. A fixed window (not first-match short-circuit) is required so Phase 3's rec-gap can compare the runner-up — short-circuiting would defeat the precision design.
- **D-08:** If nothing passes the gate, **retry once with a relaxed query** (drop year/edition noise from the search terms), re-score; if still nothing, surface the gap as stuck.

### Manual Import mechanics (IMPORT-02)
- **D-09:** Drive the import the same way the Lidarr/Readarr **"Manual Import" UI button** does, via the command API: `GET` the *arr's proposed file→track mapping for the staging folder + downloadId, **filter to the wanted files**, then `POST` the `ManualImport` command listing exactly those files with their resolved release/track IDs and **`importMode=Move`** (atomic rename/hardlink within the shared `/data` tree — no cross-FS copy). Explicit, never a blind drop-folder rescan.
- **D-10:** The import action flows through the **`*arr`-agnostic adapter** (Phase 2/3 firewall), so music (Lidarr, primary) and books (Readarr, best-effort) share one import path; Readarr faults degrade and never block music.
- **Note:** D-09/D-10 were raised by the owner as a *comprehension* question ("what do I have to do once the file is downloaded?"), not a new constraint. The answer that defines this phase: **the owner takes zero manual actions** — Curator automating exactly this UI sequence is the entire reason the project exists. The decision above is the standard correct approach, locked.

### Live-test precondition (from RESEARCH-SEED.md — BLOCKING for live testing only)
- **D-11:** Before the **first live download test**, basic slskd sharing must be configured **manually in slskd.yml** (share `/data/media/music` + `/data/media/books`, the clean library — NOT the download/staging tree) and verified `shared file count > 0`. This is slskd *config* (Phase 1 infra concern), distinct from the Curator-automated share self-healing of SHARE-01/02 (Phase 5). Phase 4 **code does not configure shares** — the plan includes a verification step/precondition note that gates the live test; unit/integration tests against fakes do not need it.

### Claude's Discretion
- Exact default values for the tunables (stall threshold, search window, quarantine TTL) — pick sensible defaults, all config-overridable via `Settings.from_env()`, matching the SP-4 pattern from Phase 3.
- The slskd REST client shape (new in this phase) and how acquisition state is tracked in the SQLite ledger (downloading / importing / imported / quarantined / stuck) — planner/researcher to design, reusing the existing state-repo + circuit-breaker patterns.
- Whether Phase 4 processes strictly one item at a time or a small bounded set — single-item end-to-end is sufficient for this phase; true concurrency/scheduling is Phase 5.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase 4 directives & scope
- `.planning/phases/phase-4/RESEARCH-SEED.md` — **MANDATORY.** The slskd manual-sharing precondition (leech-block avoidance), the read-only/writability clarifications, the exact slskd.yml share procedure, verification steps, and common failure modes. Gates live download testing.
- `.planning/ROADMAP.md` §"Phase 4: Acquisition, Staging & Clean Import" — goal + the 4 success criteria.
- `.planning/REQUIREMENTS.md` — ACQ-01, ACQ-02, ACQ-03, IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-04, IMPORT-05 (full text).

### Project-wide constraints
- `CLAUDE.md` — identical `/data` paths across containers + atomic hardlinks (#1 import-failure cause), staging→Manual-Import→auto-purge cleanup contract, `*arr`-agnostic adapter firewall, gluetun networking (slskd reachable only via gluetun's published port; Lidarr/Plex by container name on synobridge), defer-to-Lidarr quality, fallback-only behavior.
- `DEPLOY.md` — slskd `network_mode: service:gluetun`, `/volume1/data → /data` mount, PUID/PGID `1031:65536`, PIA port-forward sync, slskd reached at `http://<NAS-IP>:5030` (per INFRA-03), library roots `/data/media/music` + `/data/media/books`.

### Phase 3 decision contract (consumed, not rebuilt)
- `app/core/gate.py` — `evaluate(candidates, manifest, profile, cfg) -> GateResult(decision, chosen, distance, reasons)`. Phase 4 feeds live slskd results in and acts on the decision.
- `app/core/selector.py` — `select(accepted) -> Candidate|None`, the only reader of uploader speed/slots.
- `app/core/candidate.py` — `build_candidate` / `Candidate.from_slskd` factory (turn live slskd search-result JSON into the neutral `Candidate`).

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `app/adapters/lidarr.py`, `app/adapters/readarr.py` — authenticated Servarr v1 REST clients (httpx, `.get()`-defensive, `raise_for_status`). Phase 4 extends them with search-trigger (if *arr-side) and the `ManualImport` command + import-verification re-query. Plex scan trigger likely lands here or a sibling client.
- `app/adapters/base.py` — the `*arr`-agnostic Protocol; new import/verify methods get added here so music/books stay symmetric and the firewall holds.
- `app/adapters/breaker.py` — circuit breaker for flaky upstreams; wrap slskd + *arr calls so transient outages don't burn an attempt.
- `app/core/gate.py` + `app/core/selector.py` + `app/core/candidate.py` — Phase 3 contract; the live loop is "search → build_candidate(s) → gate.evaluate → selector.select → download winner."
- SQLite state ledger (Phase 2, `app/.../state` repo + `app/tests/test_state_repo.py`) — extend with per-item acquisition state transitions and the quarantine/stuck records.
- `app/config.py` `Settings.from_env()` (SP-4) — add Phase 4 tunables (stall threshold, search window, quarantine TTL, staging/quarantine paths) with defaults, env-overridable.

### Established Patterns
- *arr-agnostic firewall (locked, grep-tested over all core modules): no `*arr` field names cross into core; adapters normalize. Import-command payloads stay adapter-local.
- Defensive `.get()` / degrade-don't-raise on optional/absent upstream fields; Readarr faults never gate music (ARR-02).
- Config-tunable-with-defaults via `from_env()`; pinned, human-verified dependencies (package-legitimacy checkpoint precedent — applies if a new slskd client lib is added).
- Identical `/data` path + hardlink atomicity is a hard correctness constraint, not a nicety.

### Integration Points
- **NEW: slskd REST client** — no Curator→slskd client exists yet (Phase 1 set up slskd infra only). This is the major new surface: search submit/poll, download enqueue, transfer status/progress, cancel. Reached via gluetun's published port (`http://<NAS-IP>:5030`), `X-API-Key`.
- **`*arr` Manual Import command API** — `GET /manualimport` mapping + `POST` `ManualImport` command (Move mode).
- **Plex** — library scan trigger (fire-and-forget per D-04), reached by container name on synobridge.
- **Filesystem** — per-item staging dir + quarantine area inside the shared `/data` tree (no new bind-mount); purge/quarantine lifecycle.

</code_context>

<specifics>
## Specific Ideas

- Owner's mental model to honor: "it's already in my wanted list theoretically" — yes, the *arr has the item on its missing list (Phase 2 gap detection reads exactly that); Phase 4 is what explicitly hands the downloaded file back to the *arr via Manual Import so the item leaves that list. The owner does nothing manually.
- Plex is trusted and quiet: no loud warnings on Plex-scan failure (clean ~10k-track library, no current metadata-mismatch pain).
- Quarantine-with-TTL is the chosen middle path between "blind purge" (loses evidence) and "keep forever" (junk + manual chore).

</specifics>

<deferred>
## Deferred Ideas

- Autonomous scheduling / daemon loop, grace-window + Usenet-race (fallback-only timing), exponential backoff + permanent-unavailable memory — **Phase 5** (GAP-03, STATE-03, REL-01/02/03).
- Programmatic share self-healing (verify count > 0, re-scan, survive restarts, surface if broken) — **Phase 5** (SHARE-01/02). Phase 4 only *assumes/verifies* manually-configured shares for the live test.
- Detection batch-fsync perf optimization — **Phase 5** (carried from Phase 2, see `phase-5/RESEARCH-SEED.md`).
- Status endpoint / Apprise notifications — **Phase 6** (OBS-01/02). Phase 4 records stuck/quarantined state in the ledger; surfacing it richly is Phase 6.

</deferred>

---

*Phase: 4-Acquisition, Staging & Clean Import*
*Context gathered: 2026-05-31*
