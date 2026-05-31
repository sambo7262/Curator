# Phase 4 Discussion Log

**Date:** 2026-05-31
**Mode:** discuss (default)
**For human reference only — not consumed by downstream agents.**

## Areas selected
Owner selected all four offered gray areas plus added a fifth (Manual Import mechanics).

## Area 1 — Download stall & fallback (ACQ-03)
- **Options:** stall-based + next candidate / fixed wall-clock + next candidate / stall-based single-attempt no fallback
- **Chosen:** Stall-based (no-progress for N min, configurable) + fall to next-best gate-accepted candidate; exhausted → surface stuck + back off.
- **Notes:** Tolerant of slow Soulseek peers; never holds a slot forever. → D-01, D-02.

## Area 2 — Import success bar (IMPORT-03/04)
- **Options:** *arr-confirmed + Plex best-effort / both must confirm / *arr-only drop Plex
- **Chosen:** *arr-confirmed import into /volume1 is the bar; Plex scan fire-and-forget.
- **Owner note (verbatim intent):** "plex doesn't even need to warn loudly — with 10k tracks I don't have any metadata mismatch issues at the moment." → Plex failure = quiet debug log only, never blocks completion/purge. → D-03, D-04.

## Area 3 — Staging purge on failure (IMPORT-05)
- **Options:** quarantine-on-failure + purge-on-success / purge immediately always / keep failed indefinitely
- **Chosen:** Quarantine on failure (move + record + surface + TTL auto-purge), purge immediately on success. → D-05, D-06.

## Area 4 — Search → selection timing (ACQ-01/02)
- **Options:** fixed window + one relaxed retry / wait-until-quiet no retry / first-strong-match short-circuit
- **Chosen:** Fixed collection window → score once with gate.evaluate; one relaxed-query retry if nothing passes; still nothing → surface stuck.
- **Notes:** Fixed window preserves Phase 3 rec-gap (needs the runner-up); short-circuit would defeat precision. → D-07, D-08.

## Area 5 — Manual Import mechanics (IMPORT-02) — added by owner
- **Owner intent (verbatim):** "I have less of a technical requirement here and more wanted to understand the UI mechanics — once the file is downloaded, what actions will I need to take to get the file into lidarr/readarr? It's already a file in my wanted list theoretically."
- **Resolution:** Comprehension question, not a new constraint. Explained the Lidarr/Readarr "Manual Import" UI flow and that Curator automates exactly that sequence via the command API → **owner takes zero manual actions** (that automation is the project's reason for existing). Locked the standard correct approach: GET mapping → filter wanted files → POST ManualImport (importMode=Move, atomic hardlink) through the *arr-agnostic adapter. → D-09, D-10.

## Live-test precondition
- Captured from RESEARCH-SEED.md: manual slskd.yml share config + `shared file count > 0` gates the first live download test; Phase 4 code does not configure shares. → D-11.

## Deferred (redirected to keep scope)
- Daemon/scheduling/grace-window/backoff → Phase 5
- Programmatic share self-healing (SHARE-01/02) → Phase 5
- Status endpoint / Apprise → Phase 6
- Detection batch-fsync perf → Phase 5
