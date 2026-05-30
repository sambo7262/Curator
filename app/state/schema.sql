-- Curator state ledger — migration 0001: the `items` table (the persistent spine).
-- Phase 2 scope is EXACTLY one table. The richer multi-table schema (attempts,
-- staged_files, peers, events) belongs to Phases 4-6 — do NOT add them here; the
-- versioned migration runner (state/db.py, gated on PRAGMA user_version) makes adding
-- them later trivial. All DDL is idempotent (IF NOT EXISTS) so a recreated container
-- self-heals on boot (STATE-01, criterion 1).
CREATE TABLE IF NOT EXISTS items (
  id                 INTEGER PRIMARY KEY,
  arr_app            TEXT NOT NULL,                 -- 'lidarr' | 'readarr'
  arr_id             TEXT NOT NULL,                 -- the *arr's own record id (albumId / bookId)
  kind               TEXT NOT NULL,                 -- 'album' | 'book'
  gap_type           TEXT NOT NULL,                 -- 'missing' | 'cutoff'
  title              TEXT,
  artist_or_author   TEXT,
  foreign_id         TEXT,                          -- MBID release-group (Lidarr) / foreign book id (Readarr); Phase-3 anchor, stored not acted on
  quality_profile_id INTEGER,                       -- AlbumResource.profileId; stored, NOT acted on in Phase 2
  status             TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','searching','grabbed','downloaded',
                                       'imported','unavailable','blacklisted')),
  discovered_at      TEXT NOT NULL,                 -- ISO8601 UTC, first seen (NEVER refreshed by the upsert)
  last_seen_at       TEXT NOT NULL,                 -- ISO8601 UTC, refreshed each detection run
  raw_json           TEXT,                          -- original *arr record (provenance for later phases)
  UNIQUE (arr_app, arr_id)                          -- THE dedup primitive (STATE-02)
);
CREATE INDEX IF NOT EXISTS idx_items_status   ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_app_kind ON items(arr_app, kind);
