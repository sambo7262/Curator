-- Curator state ledger — migration 0002: widen the acquisition lifecycle enum + add staged_files.
-- Phase 4 turns the single `items` spine into a real acquisition state machine: a gap goes
-- pending -> searching -> downloading -> importing -> imported, or sidesteps to quarantined / stuck.
-- The status-preserving upsert (repo.upsert_gap) already protects in-flight states from a re-detect
-- clobber, so widening the CHECK FIRST means set_status('downloading') etc. never raise once this lands
-- (RESEARCH Pitfall 6). SQLite has no ALTER COLUMN, so the widen is the standard table-rebuild,
-- run inside the runner's single BEGIN/COMMIT (db.py) so a mid-migration crash rolls back wholly.
--
-- IMPORTANT for the runner: it strips `--` comments then splits on top-level `;`, so NO `;` may
-- appear inside a string literal below. Column list + constraints are copied VERBATIM from
-- schema.sql (migration 0001) — ONLY the status CHECK enum changes — so `INSERT ... SELECT *`
-- aligns by position and the UNIQUE(arr_app, arr_id) dedup primitive survives the rebuild.

-- (1) Rebuild `items` with the widened status CHECK (adds downloading/importing/quarantined/stuck).
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
                                       'downloading','importing','quarantined','stuck')),
  discovered_at      TEXT NOT NULL,                 -- ISO8601 UTC, first seen (NEVER refreshed by the upsert)
  last_seen_at       TEXT NOT NULL,                 -- ISO8601 UTC, refreshed each detection run
  raw_json           TEXT,                          -- original *arr record (provenance for later phases)
  UNIQUE (arr_app, arr_id)                          -- THE dedup primitive (STATE-02)
);

INSERT INTO items SELECT * FROM items_old;
DROP TABLE items_old;

-- Recreate 0001's indexes (IF NOT EXISTS — idempotent; the rebuild dropped them with items_old).
CREATE INDEX IF NOT EXISTS idx_items_status   ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind ON items(arr_app, kind);

-- (2) The staged_files ledger surface: one row per per-item staging dir a download writes into.
-- created_at is stamped when the download begins (D-05/D-06 lifecycle anchor); on a terminal/
-- ambiguous failure the row is updated with quarantine_path + failure_reason + quarantined_at (D-06).
CREATE TABLE IF NOT EXISTS staged_files (
  id              INTEGER PRIMARY KEY,
  item_id         INTEGER NOT NULL REFERENCES items(id),  -- the gap this staging dir belongs to
  staging_path    TEXT NOT NULL,                          -- absolute /data staging dir slskd wrote into
  quarantine_path TEXT,                                   -- set on failure when moved to quarantine (D-06)
  failure_reason  TEXT,                                   -- human-readable cause, set with quarantine_path
  quarantined_at  TEXT,                                   -- ISO8601 UTC, set when quarantined
  created_at      TEXT NOT NULL                           -- ISO8601 UTC, when the download began
);
CREATE INDEX IF NOT EXISTS idx_staged_files_item ON staged_files(item_id);
