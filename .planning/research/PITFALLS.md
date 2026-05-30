# Pitfalls Research

**Domain:** Autonomous, fallback-only Soulseek/slskd gap-filler for Lidarr/Readarr on Synology + gluetun/PIA
**Researched:** 2026-05-29
**Confidence:** MEDIUM-HIGH (grounded in established slskd/Soularr/gluetun/Synology/*arr community knowledge and the owner's first-hand prior experience; specific PIA region behavior and slskd API details flagged where confidence is lower and should be verified live during build)

> Scope note: This covers the five prior pain points first (they are the reason Curator exists), then Soulseek etiquette/risk, gluetun+PIA, Synology Docker networking, *arr import, autonomy/reliability, and security/legal. Each pitfall maps to a build phase. Phases are referenced by role, not number, since the roadmap is created downstream:
> - **P-Network** = VPN sidecar + Docker networking foundation
> - **P-Source** = slskd integration + search/match/download engine
> - **P-State** = persistent state, dedup, backoff, retry control
> - **P-Quality** = quality enforcement + file validation
> - **P-Import** = handoff to Lidarr/Readarr + Plex reflection
> - **P-Trigger** = "grace then fallback" Usenet-aware orchestration
> - **P-Observe** = Homepage widget, notifications, stuck-item surfacing
> - **P-Share** = give-back / share configuration

---

## Critical Pitfalls

### Pitfall 1: Redundant downloads — no persistent memory of what was already attempted (PRIOR PAIN POINT #1)

**What goes wrong:**
The same album/book is grabbed over and over. Soularr's core loop re-queries Lidarr for "missing/wanted" each run; anything not yet *imported* still looks missing, so it re-downloads — even items that just failed to import, are mid-transfer, are unavailable on the network, or were grabbed last cycle and are sitting in the blackhole. The owner saw exactly this.

**Why it happens:**
The acquisition decision is derived purely from the *arr "wanted" list, which only flips to "satisfied" after a successful *import*. There is no Curator-owned ledger of `attempted / in-flight / succeeded / unavailable / failed`. The gap between "downloaded" and "imported" (the prior pain point #5) is precisely where re-grabbing happens. Soularr's optional state is shallow and timing-dependent.

**How to avoid:**
- Make a **persistent state store the source of truth for "should I act on this gap?"**, not the *arr wanted list alone. SQLite (the app already may run SQL per PROJECT.md) keyed on a stable identity (MusicBrainz release/release-group ID for music; ISBN/Goodreads/edition ID for books — NOT free-text title which mutates).
- Record per-item lifecycle: `first_seen`, `last_attempt`, `attempt_count`, `state`, `next_eligible_at` (backoff), `resolved_release_id`.
- Before searching, **gate on state**: skip if `in-flight`, if `succeeded`, if `unavailable` and `next_eligible_at` is in the future, or if `attempt_count` exceeded a cap.
- Treat "downloaded but not yet imported" as a distinct `awaiting-import` state so a transfer in the blackhole is never re-queued.

**Warning signs:**
Same item appears in slskd transfer history multiple times; download dir accumulates duplicate folders; transfer count grows faster than library completion; Plex shows duplicate versions.

**Phase to address:** **P-State** (state model is the spine of dedup; design it before the source engine so the engine queries state, not just *arr).

---

### Pitfall 2: Incorrect matches — wrong release/track pulled from messy Soulseek results (PRIOR PAIN POINT #2)

**What goes wrong:**
Soulseek search returns free-text filenames from thousands of strangers' folders. "Artist - Album" matches a live bootleg, a deluxe edition with extra tracks, a single vs. the LP, a foreign pressing, a tribute/cover, a karaoke version, or a folder mislabeled entirely. Soularr's fuzzy filename matching grabbed wrong items routinely. Books are worse: same title, wrong author/edition/format (epub vs. the wanted azw3/pdf).

**Why it happens:**
There is no authoritative metadata on Soulseek — only filenames and folder names typed by uploaders. Matching by string similarity to a desired title is inherently lossy. Track-count and duration are the only structural signals and they are often absent or wrong. Soularr's default thresholds are permissive to maximize hit rate, which trades precision for recall.

**How to avoid:**
- **Match on structure, not just the title string.** For an album: require the candidate folder's **track count to equal the MusicBrainz release track count**, and (where filenames expose it) validate track ordering/numbering. Reject folders with extra/missing tracks unless explicitly allowed.
- Normalize aggressively before comparing (strip `[YEAR]`, `(Deluxe)`, scene tags, bitrate tags, uploader prefixes) and score against the *specific* monitored release, not the release-group.
- **Hard-reject denylist patterns**: `live`, `bootleg`, `karaoke`, `tribute`, `instrumental`, `cover`, `remix` (unless the wanted item is that) in folder/file names.
- For books: require **format match** to the Readarr-wanted format and prefer exact author-string match; reject if author token absent.
- Set a **confidence threshold below which Curator does NOT auto-grab** — surface as a "needs review" item (P-Observe) rather than guessing. Precision over recall: a miss is cheap (Usenet may still get it later), a wrong import is expensive (manual cleanup, the exact labor Curator must eliminate).
- Prefer uploaders/folders that already passed quality (Pitfall 3) to compound signal.

**Warning signs:**
Track counts in imported folders differ from MusicBrainz; Plex shows albums with bonus/missing tracks; user has to delete-and-reblacklist imports; books import under wrong author.

**Phase to address:** **P-Source** (matching engine) with the confidence-threshold/review path landing in **P-Observe**.

---

### Pitfall 3: Quality downgrades — wrong/lower-quality or fake file pulled (PRIOR PAIN POINT #3)

**What goes wrong:**
A 128kbps MP3 imports where a FLAC was wanted; or worse, a **transcoded fake FLAC** (MP3 upsampled to a .flac container) passes as lossless and permanently pollutes the library at "cutoff met," so Lidarr never tries to upgrade it. Soularr/slskd do not verify audio integrity — they trust the file extension.

**Why it happens:**
Two compounding causes: (1) The acquisition path applies its *own* quality logic separate from Lidarr's profile, so it can pick something the profile would reject — exactly what PROJECT.md forbids. (2) Soulseek is full of **mislabeled and transcoded files**; a `.flac` extension is not proof of lossless content, and bitrate in the filename is uploader-asserted, not measured.

**How to avoid:**
- **Defer quality entirely to Lidarr/Readarr profiles** (a hard project constraint). Do not implement parallel quality scoring. Use the wanted release's quality/cutoff to *filter Soulseek candidates before grabbing*, and let *arr's import quality parsing be the final gate — if *arr would reject it, don't import it.
- **Validate actual audio content, not the extension.** For FLAC, run a real decode/integrity check (`flac -t` / spectral-style heuristics, or read the stream's true bit depth/sample rate and detect a frequency cutoff suggesting upsampled MP3). Reject files whose real format contradicts the label. This is the single biggest defense against fake FLAC.
- Capture slskd-reported bitrate/size *and* post-download verify; mismatch ⇒ reject + denylist that uploader/file.
- Never let a failed-verification file reach the *arr import dir.

**Warning signs:**
Imported "FLAC" files with file sizes too small for their length; spectral analysis showing a hard cutoff ~16kHz; Lidarr cutoff marked "met" but listening reveals MP3 artifacts; bitrate in filename not matching decoded stream.

**Phase to address:** **P-Quality** (content validation gate sits between download-complete and import).

---

### Pitfall 4: Setup complexity making Curator primary instead of supplementary (PRIOR PAIN POINT #4)

**What goes wrong:**
The slskd+Soularr stack accreted manual touchpoints — manual approving of grabs, hand-editing match decisions, babysitting the VPN, manual import mapping — until "supplementary fallback" became a part-time job and effectively the primary workflow. The owner's #1 reason for the rewrite.

**Why it happens:**
Each unsolved edge case (wrong match, failed import, dropped VPN, duplicate) was patched with a human-in-the-loop step instead of an automated decision. Soularr's interactive mode and the *arr UIs invite manual fiddling. Complexity is additive: every manual step added becomes permanent labor.

**How to avoid:**
- **Define "hands-off" as an explicit success criterion and a test**: after deploy, zero human actions for N days while gaps still get filled. Anything requiring a human is a bug, not a feature.
- Push every ambiguous case to an **automated default** (skip + backoff, or low-confidence "review" queue that is *optional* to look at — never a blocker to progress).
- Keep configuration in **one declarative place** (env + a single config file in the compose), no per-item tuning. The app should self-configure from the *arr APIs (pull quality profiles, root folders, monitored items) rather than the user re-declaring them.
- Resist building a UI (project constraint) — surfacing through Homepage keeps it a monitor, not a control panel that begs for clicks.

**Warning signs:**
Any runbook step that says "then manually…"; a growing "review" backlog the owner feels obligated to clear; needing to restart containers by hand after VPN blips; config that must be edited per-artist.

**Phase to address:** Cross-cutting, but enforced in **P-Trigger** (autonomous decisioning) and verified in **P-Observe**. Bake "no required manual step" into every phase's acceptance.

---

### Pitfall 5: Import/sync friction — Lidarr/Readarr won't import, Plex won't reflect, manual file mapping (PRIOR PAIN POINT #5)

**What goes wrong:**
Download completes in slskd's dir but Lidarr never imports it; or imports to the wrong place; or Plex never shows it. Owner ends up hand-moving files and triggering rescans — the worst of the prior labor.

**Why it happens (the classic, near-universal cause):**
**Path mismatch between containers.** slskd sees the completed download at one path (e.g. `/downloads/complete/...` inside the slskd container) but Lidarr is configured with a *different* mount for the same physical folder (e.g. `/data/downloads/...`). Lidarr's "Remote Path Mapping" is unset or wrong, so it cannot find/hardlink the file. Secondary causes: the download isn't a "release" Lidarr is tracking (no matching grab in its history because Curator grabbed out-of-band), permissions block Lidarr from moving the file, atomic-move fails across filesystems forcing slow copy, and Plex isn't notified so the library looks stale.

**How to avoid:**
- **Single shared mount, identical path, for all containers** that touch downloads and library. The community-standard layout: one `/data` (or `/volume1/data`) root bind-mounted **at the same path** into slskd, Lidarr, Readarr, and Curator, with `downloads/` and `media/` subtrees inside it. This makes atomic hardlink/move possible and eliminates remote-path-mapping entirely. (Avoid the "separate /downloads and /music mounts" trap that breaks hardlinks and forces copies.)
- Two integration models — pick deliberately:
  1. **slskd-as-download-client**: register slskd to Lidarr/Readarr as a download client so *arr owns the import (cleanest; *arr does matching/rename/move). Confidence MEDIUM — verify current slskd↔*arr support level during build.
  2. **Curator-managed handoff**: Curator places the validated folder in the *arr-watched location and triggers `DownloadedAlbumsScan`/`RescanFolder` via API, then triggers Plex refresh. Use this if (1) is flaky for fallback semantics.
- **Trigger, don't wait.** After handoff, call the *arr command API to scan the specific folder, then poll import status; do not rely on the periodic rescan timer (slow, and races with Curator's next loop ⇒ double-import).
- After *arr import, **call Plex's partial-scan API for the affected library section** (or let *arr's Plex connection do it) so Plexamp reflects immediately.
- Ensure **PUID/PGID and umask** let *arr read/move what slskd/Curator wrote (see Pitfall 11).

**Warning signs:**
slskd shows complete but Lidarr "Activity" never logs an import; files pile up in the download dir; Lidarr import errors "file not found" or "no files found are eligible"; copies instead of instant hardlinks (slow, double disk use); Plex stale until manual scan.

**Phase to address:** **P-Network** (mount/path layout — must be right from day one) + **P-Import** (handoff, trigger, Plex refresh).

---

### Pitfall 6: gluetun/PIA region without port forwarding — US always fails (VPN)

**What goes wrong:**
Soulseek transfers stall, queues never advance, you can only download from peers who happen to have open ports, and uploads (your give-back) barely work — because slskd has no forwarded inbound port. Choosing a US PIA server guarantees this: **PIA does not offer port forwarding on US servers.**

**Why it happens:**
Soulseek needs an inbound listening port to accept peer connections; behind VPN+NAT without a forwarded port you are effectively firewalled, drastically reducing reachable peers and crippling sharing (which then gets you treated as a leecher — Pitfall 8). Operators default to a nearby/US server out of habit.

**How to avoid:**
- Set `VPN_PORT_FORWARDING=on` in gluetun and choose a **PIA region known to support PF** (e.g. CA Montreal/Toronto, and various EU regions — verify the current PF-supported list at build time; PIA's list changes). Confidence MEDIUM on specific cities — verify live.
- Confirm gluetun actually obtained a port: check gluetun logs / its control-server endpoint (`/v1/openvpn/portforwarded` or `/v1/publicip/ip`) and assert a non-zero port before declaring the stack healthy.

**Warning signs:**
gluetun log shows "port forwarding not supported for this provider/region" or no forwarded port; slskd "no connection" / all transfers queued; your slskd share download count near zero; chosen `SERVER_REGIONS` is a US city.

**Phase to address:** **P-Network** (region + PF must be validated before any source work).

---

### Pitfall 7: Forwarded port not syncing into slskd; lost on container restart (VPN)

**What goes wrong:**
gluetun gets a forwarded port, but slskd is still listening on a stale/hardcoded port, so inbound never works. Worse: **PIA's forwarded port changes** (on reconnect/restart), and slskd keeps the old one — connectivity silently dies after a reboot or VPN re-handshake.

**Why it happens:**
slskd's listen port is static config; gluetun's PF port is dynamic. There is no built-in glue. A Synology power blip, Container Manager update, or PIA session expiry re-rolls the port, and nothing tells slskd.

**How to avoid:**
- Use gluetun's **port-forwarding script hook** (`VPN_PORT_FORWARDING_UP_COMMAND` / the file gluetun writes the port to, e.g. `/tmp/gluetun/forwarded_port`) and **propagate the port into slskd** on every change — either via slskd's API/config-reload or a small sidecar/Curator routine that watches the port file and updates slskd's listen port + restarts/reloads it.
- Make port-sync **idempotent and event-driven** (react to port-file change), not a one-time startup read.
- Health check asserts `slskd.listen_port == gluetun.forwarded_port`; alert if they diverge.

**Warning signs:**
Connectivity works fresh but dies after reboot/update; slskd config port ≠ gluetun's current PF port; share works for a day then peers can't reach you.

**Phase to address:** **P-Network** (port-sync mechanism) with a recurring assertion in **P-Observe**.

---

### Pitfall 8: Getting blocked/treated as a leecher; poor share config; queue etiquette (Soulseek)

**What goes wrong:**
Peers set their clients to block users who share nothing or share very little; downloads sit queued forever or get refused. The owner is then unable to reliably fill gaps — the network punishes non-contributors. Also: hammering a single peer with many parallel requests, or ignoring their queue, gets you locked.

**Why it happens:**
Soulseek is reciprocity-based. slskd defaults can leave shares empty or misconfigured (path not inside the container's mount, or share scan never run). Aggressive concurrent requests violate unwritten etiquette and trip per-user limits.

**How to avoid:**
- **Share real content from the library** — point slskd's share at the read-only `/volume1` music/book tree (mounted into slskd). Verify the share actually scanned (slskd reports file/dir counts) — an empty share is the #1 leecher trigger.
- This requires the forwarded port (Pitfalls 6/7) so peers can actually pull from you; sharing without inbound reachability still reads as leeching.
- **Respect queues and limit concurrency**: modest simultaneous downloads, one (or few) requests per peer, back off when a peer queues you rather than retry-spamming. Honor slskd's upload slots so you're a genuine contributor.
- Don't set absurd upload speed caps to near-zero (reads as fake sharing).

**Warning signs:**
Downloads perpetually "Queued (remote)"; "User is offline/blocked you" messages; slskd shared-files count = 0; many failed connects to the same peer; your upload total stays at zero over days.

**Phase to address:** **P-Share** (share config + reciprocity), concurrency/etiquette in **P-Source**.

---

### Pitfall 9: Mislabeled uploads, incomplete albums, transcoded "FLAC" (Soulseek content quality)

**What goes wrong:**
Beyond wrong-match (Pitfall 2) and fake-FLAC (Pitfall 3): albums missing tracks (uploader only has 9 of 12), folders with mixed bitrates, bonus-disc chaos, scene-tagged junk, or NFO/sample files masquerading as the release. Auto-importing these creates incomplete/ugly library entries.

**Why it happens:**
No quality control on the network; uploaders share partial rips. slskd returns whatever filenames exist.

**How to avoid:**
- **Completeness gate**: require the candidate folder to contain the full expected track count *before* downloading; reject partials. Re-verify after download (count audio files actually received).
- Filter non-audio cruft; ensure only the intended tracks/formats land in the import dir.
- Prefer single-format folders matching the wanted quality; reject mixed-bitrate folders.
- Combine with the uploader denylist (built from prior bad grabs) to avoid known-bad sources.

**Warning signs:**
Downloaded folder track count < expected; presence of `.nfo`/`sample`/`.url`; mixed bitrates within one album; Readarr import of a "book" that is actually a sample chapter.

**Phase to address:** **P-Quality** (completeness + content checks) with denylist feeding back from **P-State**.

---

### Pitfall 10: Kill-switch / DNS leaks / *arr API reachability through gluetun (VPN + Networking)

**What goes wrong:**
Two opposite failure modes: (a) VPN drops and traffic leaks to the real home IP (kill-switch off) — defeats the whole privacy purpose; or (b) kill-switch is on but **over-blocks**, so Curator/slskd (which share gluetun's netns) can't reach Lidarr/Readarr/SABnzbd on `synobridge`, and the gap-filler goes dark. DNS leaks expose lookups even when traffic is tunneled.

**Why it happens:**
`network_mode: service:gluetun` puts slskd/Curator entirely inside gluetun's network namespace. gluetun's firewall blocks all non-VPN egress by default — including LAN/Docker-network traffic — unless you explicitly allow the local subnets. So the *arr containers on `synobridge` become unreachable. Conversely, forgetting the kill-switch or using the provider's DNS incorrectly leaks.

**How to avoid:**
- Set gluetun **`FIREWALL_OUTBOUND_SUBNETS`** to include the `synobridge` subnet and the LAN subnet, so Curator/slskd can still reach Lidarr/Readarr/SABnzbd/Plex while everything else is forced through the tunnel.
- Keep gluetun's **kill-switch/firewall ON** (default) and **`DOT=on`/gluetun DNS** to prevent DNS leaks — verify no leak via gluetun's `publicip` and a leak test from inside the netns.
- Decide the topology deliberately (see Pitfall 11): only the *source* path (slskd) strictly needs the tunnel; Curator's control-plane talk to the *arr APIs must remain reachable.
- Health check: from inside gluetun netns, assert (1) public IP ≠ home IP, (2) Lidarr API reachable, (3) DNS resolves via tunnel.

**Warning signs:**
Curator logs "connection refused/timeout" to lidarr:8686 after adding gluetun; public IP equals home IP; nslookup from container uses ISP resolver; transfers work but *arr calls fail (or vice-versa).

**Phase to address:** **P-Network** (firewall subnets + kill-switch + DNS).

---

### Pitfall 11: `network_mode: service:gluetun` removes synobridge membership & breaks name resolution (Synology Docker)

**What goes wrong:**
PROJECT.md *requires* running on `synobridge` to reach Lidarr/Readarr/Plex/SABnzbd by container name. But a container using `network_mode: service:gluetun` **cannot also join synobridge** — it inherits gluetun's network stack, so `lidarr` won't resolve and container-name addressing breaks. This is a direct architectural collision with a stated constraint.

**Why it happens:**
A container has exactly one network namespace. `network_mode: service:gluetun` replaces it; you can't add bridge networks to a container in that mode. Synology's Container Manager also has quirks with custom bridge networks, name resolution, and recreating networks on stack redeploy.

**How to avoid (architecture decision — resolve early):**
- **Put gluetun itself on `synobridge`.** Then slskd (and optionally Curator) use `network_mode: service:gluetun` and inherit gluetun's synobridge connectivity — so container-name resolution to lidarr/readarr/sabnzbd works *through gluetun*, provided `FIREWALL_OUTBOUND_SUBNETS` includes synobridge (Pitfall 10). Verify Synology's embedded DNS resolves names in this nested setup; if not, fall back to addressing *arr by NAS LAN IP:port.
- **Consider splitting roles**: only slskd needs the tunnel. Curator (control plane) can sit plainly on synobridge and talk to slskd's API via gluetun's published port and to the *arr APIs directly — simplest, most debuggable. Keep the VPN blast radius to just slskd.
- Expose slskd's web/API port via **gluetun's** `ports:` (ports must be published on the netns-owning container, i.e. gluetun, not slskd).
- On Synology, **create the network and deploy via compose, not the GUI network editor**; expect Container Manager to sometimes need a full stack down/up (not just restart) after network changes.

**Warning signs:**
`getaddrinfo`/"could not resolve host lidarr"; slskd web UI unreachable (port published on wrong container); Container Manager shows the container on `none`/gluetun net, not synobridge; redeploy silently drops network membership.

**Phase to address:** **P-Network** — this is *the* foundational topology decision; get it wrong and everything downstream breaks.

---

### Pitfall 12: PUID/PGID & bind-mount ownership on /volume1 (Synology permissions)

**What goes wrong:**
Curator/slskd write files as a UID that Lidarr/Readarr can't read or move ⇒ imports fail with permission errors, or files land owned by `root` and the owner can't manage them. On Synology, `/volume1` ACLs + Docker UID mapping make this especially fiddly.

**Why it happens:**
Each container's process UID/GID must match the owner of the bind-mounted `/volume1/docker/...` paths. Synology often uses a specific user (uid 1026+/users group gid 100) for media; mismatched PUID/PGID across the stack, or a too-restrictive umask, breaks cross-container file ops. Atomic moves also fail if download and library are different filesystems (forces copy + permission re-stamp).

**How to avoid:**
- Use a **consistent PUID/PGID across slskd, Curator, Lidarr, Readarr** matching the `/volume1` media owner; set a sane **umask (002)** so group-writable files are movable by the *arr apps.
- Pre-create bind-mount dirs with correct ownership on the NAS before first run.
- Keep download + library under **one filesystem/mount** (Pitfall 5) so moves are atomic hardlinks, not permission-resetting copies.

**Warning signs:**
*arr import error "permission denied"/"access to the path is denied"; files owned by root or an unexpected UID; owner can't delete via File Station; hardlink fails ⇒ slow copies.

**Phase to address:** **P-Network** (mount/permission layout) verified in **P-Import**.

---

### Pitfall 13: Retry storms & missing backoff (Autonomy/Reliability)

**What goes wrong:**
An unavailable item or a transiently-offline peer triggers immediate, repeated re-search/re-download every loop — hammering slskd, getting the account flagged, spiking load, and (with no memory) compounding Pitfall 1. A "stuck" transfer never times out and blocks a slot forever.

**Why it happens:**
Naive loop: "for each wanted item, search+grab" with no per-item cooldown, no attempt cap, no transfer timeout. Soularr's fixed-interval runs without exponential backoff replay the same failures.

**How to avoid:**
- **Exponential backoff with jitter** per item, persisted in `next_eligible_at` (P-State). Escalate cooldown on repeated failure; cap attempts then mark `unavailable` (retry only after a long window, since Soulseek availability is peer-dependent and may improve later).
- **Transfer timeouts**: if a download makes no progress in N minutes, cancel, free the slot, record failure, back off.
- Rate-limit outbound searches/requests globally and per-peer (ties to etiquette, Pitfall 8).
- Make the whole loop **interval-driven and idempotent** so a crash mid-loop resumes cleanly.

**Warning signs:**
slskd search/transfer logs show the same query every cycle; CPU/network sawtooth; transfers stuck at 0% holding slots; attempt_count climbing without resolution.

**Phase to address:** **P-State** (backoff/attempt model) + **P-Source** (timeouts, rate limits).

---

### Pitfall 14: State corruption & crash-unsafe lifecycle (Autonomy/Reliability)

**What goes wrong:**
A crash/restart (Synology reboot, Container Manager update, OOM) mid-transfer leaves the ledger inconsistent: items stuck `in-flight` forever (never retried), or marked `succeeded` before import actually completed (lost forever), or duplicate rows. SQLite corruption from an unclean shutdown on a bind-mount.

**Why it happens:**
State transitions aren't transactional or are written optimistically; no reconciliation on startup; `in-flight` has no owning-process liveness check.

**How to avoid:**
- **Reconcile on startup**: cross-check ledger `in-flight`/`awaiting-import` against slskd's actual transfer state and the *arr import history; demote orphans back to a retryable state.
- Write state transitions **transactionally**; only mark `succeeded` after *arr confirms import (poll the command result), not at download-complete.
- Enable SQLite **WAL mode**; ensure the DB lives on a reliable `/volume1` bind-mount; back it up periodically.
- Idempotent operations keyed on stable IDs so a replay can't double-insert.

**Warning signs:**
Items frozen `in-flight` after a reboot; gaps that downloaded but never re-attempt import; "database is locked"/"database disk image is malformed"; duplicate ledger entries.

**Phase to address:** **P-State** (transactional model + startup reconciliation).

---

### Pitfall 15: Racing the Usenet pipeline — violating fallback-only (Trigger)

**What goes wrong:**
Curator grabs an item from Soulseek while SABnzbd/Usenet was about to (or did) get a better copy ⇒ duplicate imports, quality fights (Soulseek MP3 vs incoming Usenet FLAC), and Curator overriding the primary pipeline — explicitly forbidden by PROJECT.md.

**Why it happens:**
Triggering on "wanted" without checking whether Usenet has the item *in progress* or recently queued. No grace window, or grace window too short. Curator and Usenet both racing the same release.

**How to avoid:**
- **"Grace then fallback"**: only act on a gap that has been wanted for ≥ grace period AND has no active/queued Usenet attempt. Query SABnzbd queue/history and the *arr "queue"/"history" before grabbing — if Usenet has it in-flight or recently grabbed, defer.
- Recheck just before grab (race window): re-confirm still-missing and no Usenet activity.
- On import, if the item is now present (Usenet won during Curator's transfer), **discard Curator's copy** rather than double-import.
- Keep quality deference (Pitfall 3) so even if both land, the better one wins per *arr profile.

**Warning signs:**
Duplicate versions in Plex right after a grab; Curator import immediately followed by an upgrade/replace; SABnzbd and slskd both processing the same release; grabs of items Usenet routinely gets.

**Phase to address:** **P-Trigger** (Usenet-aware grace logic + pre-grab recheck).

---

### Pitfall 16: Partial-download handling & surfacing "stuck" items (Autonomy/Observability)

**What goes wrong:**
A transfer dies at 60%, leaving a partial folder that either gets imported as incomplete (Pitfall 9) or sits forever consuming a slot/disk; or an item silently never resolves and the owner never knows — the library just stays incomplete, eroding trust in "hands-off."

**Why it happens:**
No distinction between complete and partial; no surfacing of items that exhausted retries; the only feedback channel (manual *arr browsing) is exactly what Curator is meant to eliminate.

**How to avoid:**
- Treat a folder as importable **only when complete** (expected file/track count present and integrity-verified); quarantine partials, clean them up, record failure + backoff.
- **Surface the queue states** to Homepage: counts of `wanted-gaps`, `in-flight`, `awaiting-import`, `stuck/unavailable`, `needs-review`. A stuck item must be *visible*, not silent.
- **Push notifications** on grabs/failures/blocks (per PROJECT.md) so the owner learns of a persistent stuck item without polling.
- Auto-clean orphaned partials on startup reconciliation (Pitfall 14).

**Warning signs:**
Partial folders accumulating in download dir; disk creep; items that never complete and never alert; owner discovering gaps only by manually browsing Lidarr.

**Phase to address:** **P-Quality** (completeness) + **P-Observe** (stuck surfacing + notifications).

---

### Pitfall 17: Readarr being unmaintained / API drift (Integration)

**What goes wrong:**
Readarr's upstream development halted in 2024 (per PROJECT.md). Metadata server outages, edition/format quirks, and unpatched bugs can break book matching or import; future *arr ecosystem changes won't be reflected.

**Why it happens:**
No maintenance ⇒ accumulating bugs, dependence on a metadata service that may degrade; book metadata (editions/formats) is inherently messier than music.

**How to avoid:**
- **Isolate Readarr-specific logic** behind an adapter so a future swap (e.g. to a successor/fork) is low-cost. Don't hard-couple Curator's core to Readarr internals.
- Be **defensive about Readarr API responses** (handle missing metadata, empty editions). Prefer ISBN/edition IDs for state keys; degrade gracefully when metadata is absent rather than crashing the loop.
- Treat books as a **secondary, best-effort path**; music (Lidarr, healthy) is the reliability backbone.

**Warning signs:**
Readarr metadata lookups failing/empty; book imports erroring on edition mismatch; Readarr API shape changing vs. expectations.

**Phase to address:** **P-Source/P-Import** (adapter boundary + defensive parsing), flagged for **deeper research** before book support is built.

---

### Pitfall 18: Security & legal exposure for a homelab (Security/Privacy)

**What goes wrong:**
Real home IP exposed to Soulseek peers (P2P shows your IP to every peer — no SSL tunnel, unlike Usenet); secrets (PIA creds, *arr API keys) leaked in the repo/image; the Docker Hub image or compose accidentally publishing slskd to the internet; and the inherent legal exposure of acquiring copyrighted music/books over P2P from a residential connection.

**Why it happens:**
Forgetting that Soulseek transfers are direct peer connections; baking secrets into the image built by GitHub Actions; opening ports on the NAS/firewall; not appreciating that P2P (peers see your IP) carries more direct exposure than Usenet-over-SSL.

**How to avoid:**
- **All source traffic through gluetun, kill-switch on, verified non-home public IP** (Pitfalls 6/10). Home firewall stays closed (project constraint) — no inbound port-forward on the NAS; inbound reachability comes only via the VPN's forwarded port.
- **Never bake secrets into the image.** PIA creds and *arr API keys come from env/Docker secrets at runtime, not the GitHub repo or the published Docker Hub image. Scan the image/CI for leaked secrets.
- **Do not publish slskd/Curator ports to the WAN**; access stays LAN-only via Tailscale (existing model). Don't undo that.
- Run containers **non-root** (PUID/PGID), read-only share mount for the give-back tree so a compromise can't write to the library.
- Legal: this is the owner's accepted risk for a private homelab; the mitigation Curator provides is IP anonymization via VPN and keeping everything private (LAN/Tailscale, no public exposure). Note it explicitly so the privacy posture is a designed feature, not an afterthought.

**Warning signs:**
Public IP test inside container = home IP; API keys/PIA creds found in repo or image layers; slskd port reachable from WAN; containers running as root; share mount writable.

**Phase to address:** **P-Network** (VPN/leak/firewall) + cross-cutting secrets hygiene in the **CI/deploy** setup; verified in **P-Observe** (ongoing leak/IP assertion).

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Trust `.flac` extension instead of decoding | Faster, less code | Fake-FLAC permanently pollutes library at "cutoff met" | Never (this is prior pain point #3) |
| Match on title-string similarity only | Quick to build, high hit rate | Wrong-release imports = manual cleanup labor | Never for auto-grab; OK only to populate a review queue |
| Derive "should act" from *arr wanted list alone | No state code | Redundant downloads (pain point #1) | Never — state ledger is mandatory |
| Static slskd listen port (ignore gluetun PF) | Simple compose | Dies on every VPN reconnect/reboot | Never (PF is dynamic) |
| US PIA server "because it's close" | Lower latency | No port forwarding ⇒ crippled transfers + leecher status | Never |
| Mark item `succeeded` at download-complete | Simpler flow | Lost items if import later fails | Never — confirm via *arr import |
| Separate /downloads and /music mounts | Mirrors mental model | Breaks hardlinks ⇒ slow copies + perms churn | Never — use one /data root |
| Fixed-interval retry, no backoff | Trivial loop | Retry storms, account flagging | MVP smoke-test only, replace before hands-off |
| Skip startup reconciliation | Less code | Orphaned in-flight items after reboot | Never on Synology (reboots/updates happen) |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| slskd ↔ Lidarr/Readarr | Different container paths for the same folder ⇒ import "file not found" | Identical single `/data` mount in all containers; or correct Remote Path Mapping |
| slskd via gluetun | Publishing ports on slskd (netns owned by gluetun) | Publish slskd's ports on the **gluetun** service |
| Curator ↔ *arr through gluetun | Kill-switch blocks LAN/Docker traffic | `FIREWALL_OUTBOUND_SUBNETS` includes synobridge + LAN |
| gluetun + synobridge | `network_mode: service:gluetun` can't also join synobridge | Put gluetun on synobridge; dependents inherit it |
| PIA port forwarding | Choosing US (unsupported) | PF-supported non-US region + `VPN_PORT_FORWARDING=on`; assert port obtained |
| slskd port vs gluetun PF | Static slskd port, dynamic PF port | Watch gluetun's forwarded_port file; push into slskd on change |
| *arr import trigger | Waiting on periodic rescan | Call command API to scan the specific folder, then poll result |
| Plex (native Synology app) | Assuming *arr import updates Plex | Trigger Plex partial-scan of the section after import |
| SABnzbd/Usenet | Grabbing without checking Usenet queue | Query SABnzbd + *arr queue/history before fallback grab |
| Secrets in CI/image | Baking PIA creds / API keys into Docker Hub image | Runtime env/secrets only; scan image layers |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Re-searching all wanted items every loop | slskd search spam, network sawtooth | Gate on state + backoff; only search eligible items | Grows with library/gap count (~hundreds of items) |
| Copies instead of hardlinks | Slow imports, 2x disk use | Single filesystem mount; atomic move | Any large library, immediately |
| Unbounded concurrent transfers | Slots exhausted, peers block you | Cap concurrency per-peer and globally | As soon as multiple gaps queue |
| No transfer timeout | Stuck slots, growing partials | Progress-based timeout + cleanup | First dead peer |
| Polling *arr/slskd too aggressively | API load, rate issues | Sensible intervals; event/file-watch where possible | Minor at this scale, but avoid |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Source traffic outside the VPN | Home IP exposed to every Soulseek peer | All slskd traffic via gluetun; kill-switch on; assert non-home IP |
| DNS leak despite tunnel | Lookups reveal activity to ISP | gluetun DNS/DoT; verify no leak from inside netns |
| Secrets in repo/Docker Hub image | Credential theft, account compromise | Runtime secrets only; never in image/CI logs |
| Publishing slskd to WAN | Open service, IP/abuse exposure | LAN/Tailscale only; never WAN-publish; firewall stays closed |
| Containers as root + writable share | Library tampering on compromise | Non-root PUID/PGID; read-only share mount |
| Forwarded port on NAS firewall | Defeats "closed firewall" constraint | Inbound only via VPN PF, never NAS port-forward |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Any required manual approval step | Recreates the prior labor (pain point #4) | Automated default decision; review queue is optional, non-blocking |
| Silent stuck items | Library quietly incomplete, trust erodes | Surface stuck/unavailable to Homepage + push notify |
| Per-artist/per-item config | Setup never ends | Self-configure from *arr APIs; one declarative config |
| Building a control-panel UI | Invites fiddling, scope creep | Read-only Homepage widget (project constraint) |
| Notification spam on every event | Owner mutes it, misses real issues | Notify on grabs/failures/blocks; batch routine events |

## "Looks Done But Isn't" Checklist

- [ ] **VPN privacy:** Public IP from *inside* the slskd netns ≠ home IP, AND no DNS leak — verify, don't assume kill-switch works.
- [ ] **Port forwarding:** gluetun actually obtained a PF port (non-US region) AND slskd is listening on *that* port — verify after a container restart, not just first boot.
- [ ] **Port re-sync:** Reboot the NAS; confirm slskd's port re-syncs to gluetun's new PF port automatically.
- [ ] **Name resolution through gluetun:** Curator can reach `lidarr`/`readarr`/`sabnzbd` by name (or IP) while inside/through gluetun.
- [ ] **Path parity:** slskd's completed path == the path Lidarr sees == hardlink-capable (same filesystem) — confirm an actual hardlink, not a copy.
- [ ] **Permissions:** Lidarr/Readarr can move/delete files slskd wrote (PUID/PGID/umask) — confirm a real import end-to-end.
- [ ] **Dedup:** Trigger the same gap twice; confirm it is NOT downloaded twice (state ledger working).
- [ ] **Quality gate:** Feed a known fake-FLAC; confirm it is rejected, not imported.
- [ ] **Match precision:** Feed a wrong-track-count folder; confirm rejection or review-queue, not auto-import.
- [ ] **Fallback-only:** Item active in SABnzbd queue is NOT grabbed by Curator.
- [ ] **Crash safety:** Kill the container mid-transfer; on restart, no orphaned `in-flight`, no double-import, DB intact.
- [ ] **Share/give-back:** slskd shared-files count > 0 and an external peer can actually download from you (proves inbound PF + share scan).
- [ ] **Plex reflection:** A fresh import appears in Plex/Plexamp without a manual scan.
- [ ] **Hands-off:** N days with zero manual actions while gaps still fill.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Redundant downloads already happened | LOW | Build ledger; backfill from slskd history; dedup library in Plex/*arr |
| Wrong-match imported | MEDIUM | *arr "remove + blocklist release"; add uploader/pattern to denylist; re-trigger |
| Fake-FLAC in library | MEDIUM | Detect via spectral scan; delete + blocklist; let *arr re-seek; denylist source |
| VPN region had no PF | LOW | Switch `SERVER_REGIONS` to PF-supported non-US; restart gluetun; verify port |
| Port not syncing | LOW-MED | Add port-file watcher → slskd update; assert in health check |
| synobridge unreachable through gluetun | MEDIUM | Set `FIREWALL_OUTBOUND_SUBNETS`; or split Curator off the tunnel |
| Import path mismatch | MEDIUM | Re-architect to single /data mount (best) or add Remote Path Mapping |
| State corruption after reboot | MEDIUM | Startup reconciliation against slskd + *arr history; WAL; restore DB backup |
| Stuck/partial items | LOW | Quarantine-and-clean job; surface to Homepage; backoff |
| Got blocked as leecher | LOW-MED | Verify share scanned + PF working; share more; respect queues |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| #1 Redundant downloads | P-State | Re-trigger same gap ⇒ no second download |
| #2 Incorrect matches | P-Source (+P-Observe review) | Wrong-track-count folder rejected |
| #3 Quality downgrades / fake FLAC | P-Quality | Fake-FLAC rejected; quality deferred to *arr |
| #4 Setup complexity / not supplementary | P-Trigger + cross-cutting | N-day hands-off test passes |
| #5 Import/sync friction | P-Network (paths) + P-Import | End-to-end import + Plex reflect, hardlink confirmed |
| #6 PIA region no PF | P-Network | gluetun reports forwarded port (non-US) |
| #7 Port not syncing / lost on restart | P-Network | After reboot, slskd port == gluetun PF port |
| #8 Leecher block / share/etiquette | P-Share + P-Source | External peer can download; shared count > 0 |
| #9 Mislabeled/incomplete/transcoded | P-Quality | Partial/mixed folders rejected |
| #10 Kill-switch/DNS/API reachability | P-Network | Non-home IP, no DNS leak, *arr reachable |
| #11 network_mode vs synobridge | P-Network | Name/IP resolution to *arr works through gluetun |
| #12 PUID/PGID/ownership | P-Network + P-Import | *arr moves slskd-written files cleanly |
| #13 Retry storms / backoff | P-State + P-Source | No repeated search of failed item; timeouts fire |
| #14 State corruption | P-State | Crash-mid-transfer ⇒ clean recovery |
| #15 Racing Usenet | P-Trigger | SABnzbd-active item not grabbed |
| #16 Partials / stuck surfacing | P-Quality + P-Observe | Partials quarantined; stuck visible + notified |
| #17 Readarr unmaintained | P-Source/P-Import (adapter) | Book path degrades gracefully on missing metadata |
| #18 Security/privacy/legal | P-Network + CI/deploy | No leaked secrets; no WAL exposure; non-home IP |

## Sources

- Owner first-hand prior experience with slskd + Soularr (PROJECT.md Context) — the five pain points are validated, not hypothetical. **HIGH** confidence on the problem statements.
- Established community knowledge of the *arr stack (TRaSH Guides path/hardlink conventions, Remote Path Mapping, "single /data mount" pattern), gluetun port-forwarding behavior, and PIA's lack of US port forwarding. **MEDIUM-HIGH**; specific PIA PF-supported regions and current slskd↔*arr download-client support should be **verified live during P-Network/P-Source** as they change over time.
- slskd / Soulseek reciprocity and leecher-blocking norms; Soulseek P2P exposes peer IPs (no SSL) — **HIGH** (network design fact).
- Readarr development halt (2024) — stated in PROJECT.md; treat as known constraint. **HIGH** for the fact, **MEDIUM** for downstream API-stability implications.

> Items marked verify-live: exact PIA port-forwarding region list; whether to register slskd directly as an *arr download client vs. Curator-managed handoff; Synology Container Manager DNS resolution behavior for `network_mode: service:gluetun` nested on synobridge. Resolve these empirically in P-Network/P-Source before committing the topology.

---
*Pitfalls research for: autonomous Soulseek/slskd gap-filler for Lidarr/Readarr on Synology + gluetun/PIA*
*Researched: 2026-05-29*
