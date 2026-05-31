-- Curator state ledger — migration 0003 (Phase 5): add the backoff/attempt/dormant columns +
-- the 'permanently-unavailable' status to the acquisition state machine.
-- Phase 5 makes the loop autonomous: an item that fails acquisition backs off (1h/6h/24h),
-- and after the give-up threshold (D-07) becomes 'permanently-unavailable' with a 30-day dormant
-- re-check (D-09). That needs three new per-item columns (attempt_count / next_attempt_at /
-- last_checked_at) and the new terminal status. SQLite has no ALTER COLUMN for a CHECK, so the
-- widen is the standard table-rebuild, run inside the runner's single BEGIN/COMMIT (db.py) so a
-- mid-migration crash rolls back wholly — the live NAS ledger (~1,493 rows) must survive intact.
--
-- IMPORTANT for the runner: it strips `--` comments then splits on top-level `;`, so NO `;` may
-- appear inside a string literal below. The 13 pre-existing columns + the UNIQUE(arr_app, arr_id)
-- dedup primitive are copied VERBATIM from migration_0002 — only the status CHECK widens and the
-- three columns append. Because the new/old shapes now DIVERGE, the copy uses an EXPLICIT
-- column-list INSERT ... SELECT (NOT positional SELECT *) so the carry-over is unambiguous.

-- (1) Rebuild `items` with the three new columns + the widened status CHECK (adds 'permanently-unavailable').
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
                                       'permanently-unavailable')),
  discovered_at      TEXT NOT NULL,                 -- ISO8601 UTC, first seen (NEVER refreshed by the upsert)
  last_seen_at       TEXT NOT NULL,                 -- ISO8601 UTC, refreshed each detection run
  raw_json           TEXT,                          -- original *arr record (provenance for later phases)
  attempt_count      INTEGER NOT NULL DEFAULT 0,    -- D-07 per-item acquisition attempt counter
  next_attempt_at    TEXT,                          -- D-08 ISO8601 UTC backoff gate (NULL = no backoff pending)
  last_checked_at    TEXT,                          -- D-09 ISO8601 UTC last attempt time (dormant re-check anchor)
  UNIQUE (arr_app, arr_id)                          -- THE dedup primitive (STATE-02)
);

-- Explicit column-list carry-over of the 13 pre-existing columns (shapes diverge now, so NOT SELECT *).
-- The three new columns are intentionally OMITTED here so they take their table defaults
-- (attempt_count -> 0, next_attempt_at -> NULL, last_checked_at -> NULL) for every preserved row.
INSERT INTO items (id, arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id,
                   quality_profile_id, status, discovered_at, last_seen_at, raw_json)
  SELECT id, arr_app, arr_id, kind, gap_type, title, artist_or_author, foreign_id,
         quality_profile_id, status, discovered_at, last_seen_at, raw_json
  FROM items_old;
DROP TABLE items_old;

-- Recreate the existing indexes (the rebuild dropped them with items_old) + add the backoff index.
CREATE INDEX IF NOT EXISTS idx_items_status       ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind     ON items(arr_app, kind);
CREATE INDEX IF NOT EXISTS idx_items_next_attempt ON items(next_attempt_at);
