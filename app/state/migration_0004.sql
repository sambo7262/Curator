-- Curator state ledger — migration 0004 (Phase 5): add the 'partial' acquisition status.
-- Partial album completion (owner policy 2026-05-31): when Soulseek yields only SOME of an album's
-- tracks (a single/EP/partial) and the *arr imports those REAL tracks but the album stays on the
-- wanted list, that is PROGRESS, not a failure — so it must NOT quarantine. The item parks in the new
-- 'partial' status on a long cooldown (acq_partial_cooldown_seconds) and is revisited later for the
-- still-missing tracks (a fuller source may appear), never permanently ignored. 'partial' is a
-- RETRYABLE status (repo.select_eligible) gated by next_attempt_at, exactly like stuck/quarantined.
--
-- SQLite has no ALTER COLUMN for a CHECK, so this is the standard table-rebuild — identical shape to
-- migration_0003 (all 16 columns + UNIQUE), the ONLY change being 'partial' appended to the status
-- CHECK. The runner (db.py) toggles foreign_keys OFF + legacy_alter_table ON around this so the
-- staged_files FK survives the RENAME/DROP, and runs it inside one BEGIN/COMMIT (the live ~1,493-row
-- NAS ledger must survive intact). No `;` may appear inside any string literal (the runner splits on `;`).

-- (1) Rebuild `items` with the widened status CHECK (adds 'partial').
ALTER TABLE items RENAME TO items_old;

CREATE TABLE items (
  id                 INTEGER PRIMARY KEY,
  arr_app            TEXT NOT NULL,                 -- 'lidarr' | 'readarr'
  arr_id             TEXT NOT NULL,                 -- the *arr's own record id (albumId / bookId)
  kind               TEXT NOT NULL,                 -- 'album' | 'book'
  gap_type           TEXT NOT NULL,                 -- 'missing' | 'cutoff'
  title              TEXT,
  artist_or_author   TEXT,
  foreign_id         TEXT,                          -- MBID release-group (Lidarr) / foreign book id (Readarr)
  quality_profile_id INTEGER,                       -- AlbumResource.profileId; stored, consumed by the gate
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','searching','grabbed','downloaded',
                                       'imported','unavailable','blacklisted',
                                       'downloading','importing','quarantined','stuck',
                                       'permanently-unavailable','partial')),
  discovered_at      TEXT NOT NULL,                 -- ISO8601 UTC, first seen (NEVER refreshed by the upsert)
  last_seen_at       TEXT NOT NULL,                 -- ISO8601 UTC, refreshed each detection run
  raw_json           TEXT,                          -- original *arr record (provenance for later phases)
  attempt_count      INTEGER NOT NULL DEFAULT 0,    -- D-07 per-item acquisition attempt counter
  next_attempt_at    TEXT,                          -- D-08 ISO8601 UTC backoff/cooldown gate (NULL = none pending)
  last_checked_at    TEXT,                          -- D-09 ISO8601 UTC last attempt time (dormant re-check anchor)
  UNIQUE (arr_app, arr_id)                          -- THE dedup primitive (STATE-02)
);

-- Explicit column-list carry-over of all 16 columns (shapes are now identical, but stay explicit for
-- an unambiguous, future-proof carry-over — NOT SELECT *).
INSERT INTO items (id, arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id,
                   quality_profile_id, status, discovered_at, last_seen_at, raw_json,
                   attempt_count, next_attempt_at, last_checked_at)
  SELECT id, arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id,
         quality_profile_id, status, discovered_at, last_seen_at, raw_json,
         attempt_count, next_attempt_at, last_checked_at
  FROM items_old;
DROP TABLE items_old;

-- Recreate the indexes (the rebuild dropped them with items_old).
CREATE INDEX IF NOT EXISTS idx_items_status       ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind     ON items(arr_app, kind);
CREATE INDEX IF NOT EXISTS idx_items_next_attempt ON items(next_attempt_at);
