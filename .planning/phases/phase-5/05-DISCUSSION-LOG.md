# Phase 5: Autonomy, Sharing & Self-Recovery - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-31
**Phase:** 5-autonomy-sharing-self-recovery
**Areas discussed:** Grace window & Usenet-race avoidance, Daemon cadence & rollout caps, Retry backoff & give-up, Sharing automation depth, Issue visibility

---

## Grace window & Usenet-race avoidance (GAP-03)

| Option | Description | Selected |
|--------|-------------|----------|
| 3 days | Solid Usenet window before Soulseek fallback | ✓ |
| 24 hours | More aggressive fallback | |
| 7 days | Very patient/conservative | |

| Option | Description | Selected |
|--------|-------------|----------|
| Check Lidarr's queue | *arr queue is source of truth for active Usenet grab | ✓ |
| Time-based grace only | No live check, could race a late grab | |
| Query SABnzbd directly | New integration + creds | |

| Option | Description | Selected |
|--------|-------------|----------|
| From first detected as wanted (discovered_at) | Backlog already past grace | ✓ |
| From album release date | Needs release date, treats old=new | |

**User's choice:** 3-day grace, *arr queue check, clock from discovered_at.
**Notes:** User asked whether the full wanted list would wait a fresh 3 days at launch — clarified it would NOT (status-preserving upsert keeps original discovered_at, so the ~1493 backlog is already past grace; only new items wait). User correctly reasoned the race only exists for in-flight grabs ("if the file is in the *arr it leaves the wanted queue"), confirming the queue-check + wanted-list semantics fully cover it. Flagged that grace ≠ rollout safety; caps hold back the backlog flood.

---

## Daemon cadence & rollout caps (REL-01 + bounded rollout)

| Option | Description | Selected |
|--------|-------------|----------|
| Every 6 hours | Gentle, plenty responsive for fallback | ✓ |
| Hourly | More responsive, more searches | |
| Daily | Very gentle | |

| Option | Description | Selected |
|--------|-------------|----------|
| 1 concurrent | Safest start, easiest to observe | |
| 3 concurrent | Modest parallelism | ✓ |
| 5 concurrent | Faster drain, more load | |

| Option | Description | Selected |
|--------|-------------|----------|
| Env flags, manually promoted | dry-run → cap=1 → raise; kill-switch | ✓ |
| Auto-promote after N clean imports | Hands-off graduation | |
| Start live at cap=1 (skip dry-run) | Straight to live | |

| Option | Description | Selected |
|--------|-------------|----------|
| Manual single-item trigger | Dedicated endpoint/CLI for one album | |
| Observe first capped daemon pass | Watch the daemon's first real item | ✓ |

**User's choice:** 6h poll, 3 concurrent steady-state, env-flag rollout ("1 as long as it's easy to manage"), observe the first capped daemon pass.
**Notes:** Synthesized: first live pass at MAX_CONCURRENT=1 (clean one-album observation = the acceptance test), then raise to 3 — same env knob, no separate trigger code. Supersedes the RESEARCH-SEED single-item-trigger suggestion.

---

## Retry backoff & give-up (STATE-03)

| Option | Description | Selected |
|--------|-------------|----------|
| 3 attempts | Reasonable before giving up | ✓ |
| 5 attempts | More persistent | |
| 2 attempts | Give up quickly | |

| Option | Description | Selected |
|--------|-------------|----------|
| Exponential 1h→6h→24h | Grows wait, caps at 24h | ✓ |
| Fixed 24h | One retry/day | |
| Exponential capped 7d | Longer final waits | |

| Option | Description | Selected |
|--------|-------------|----------|
| Re-check every 30 days | Monthly graveyard sweep | ✓ |
| Every 7 days | Weekly | |
| Never (permanent) | Give up for good | |

**User's choice:** All recommended — 3 attempts, exponential 1h→6h→24h, 30-day dormant re-check.
**Notes:** None.

---

## Sharing automation depth (SHARE-01/02)

| Option | Description | Selected |
|--------|-------------|----------|
| Ensure + self-heal | Verify count>0, rescan, surface; no config rewrite | ✓ |
| Curator fully owns config | Self-configures shares from scratch | |
| Verify-only (alert if 0) | No auto-rescan | |

| Option | Description | Selected |
|--------|-------------|----------|
| Music + books, read-only | The clean library | ✓ |
| Music only, read-only | Music just | |

**User's choice:** Ensure + self-heal; music + books read-only.
**Notes:** Researcher to confirm slskd API supports reading share count + triggering a rescan.

---

## Issue visibility (REL-03 + owner addition)

| Option | Description | Selected |
|--------|-------------|----------|
| Status endpoint | Lists problem items + counts; browser/JSON | ✓ |
| Logs only | Grep docker logs | |
| Both endpoint + daily log summary | Endpoint + once-a-day summary | |

| Option | Description | Selected |
|--------|-------------|----------|
| Expose now, push later | Phase 5 exposes; Apprise push in Phase 6 | ✓ |
| Pull basic notifications into Phase 5 | Minimal alert now | |

**User's choice:** Browser-viewable status page (simple UI), expose-now-push-later.
**Notes:** User asked if the status can be accessed via a browser — yes; wants a simple UI, not raw JSON. Mentioned a Pushover setup but explicitly wanted to avoid the complexity of push now → deferred to Phase 6 (Apprise supports Pushover).

## Claude's Discretion

- Startup reconciliation policy (REL-02): default = restart in-flight cleanly, verify-guard against double-import, infra outage burns no attempt. Owner accepted; may revisit at plan time.
- Dry-run log format, status-page layout, scheduler library (APScheduler vs asyncio loop) — planner/researcher.

## Deferred Ideas

- Push notifications (Apprise → Pushover) → Phase 6.
- Polished Homepage customapi widget → Phase 6.
- Concurrency tuning beyond 3 → owner ops after trust.
- Direct SABnzbd integration → rejected (queue check suffices).
