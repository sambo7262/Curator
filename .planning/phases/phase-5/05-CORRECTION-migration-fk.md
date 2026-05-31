# Phase 5 Correction — migration_0003 FK regression (supersedes earlier "out-of-scope" notes)

**Date:** 2026-05-31
**Fix commit:** `fix(05-01): repair FK corruption from migration_0003 table-rebuild` (see `git log`)

## What was wrong

Migration_0003 (from plan 05-01) widens the `items.status` CHECK with the standard
RENAME / CREATE / INSERT / DROP table-rebuild. Under SQLite's **default** schema handling,
`ALTER TABLE items RENAME TO items_old` silently repoints the `staged_files.item_id`
foreign key at `items_old`; the trailing `DROP TABLE items_old` then leaves that FK
**dangling**. Result: after the migration runs, **every** `INSERT INTO staged_files`
fails with `sqlite3.OperationalError: no such table: main.items_old`.

This broke the **entire Phase-4 acquisition/staging path** post-migration — a real,
deploy-blocking regression introduced by Phase 5, not a test artifact.

## What the earlier summaries claimed (INCORRECT — now superseded)

- **05-01-SUMMARY.md** "Tests" / "Deferred Issues": reported "247 passed, 2 failed —
  both failures pre-existing and out of scope." The full-suite count was inaccurate and
  the migration FK breakage was not identified.
- **05-03-SUMMARY.md**: reported "259 passed, 6 failed" and labeled the failures
  "pre-existing out-of-scope migration-test contamination (pass in isolation)."

Both labels were wrong. The failures were **not** pre-existing and **not** isolation
contamination — `test_state_repo.py` and `test_acquire.py` failed even when each file was
run completely alone. The true count at that point was **16 failures** (≈13 `items_old`
FK errors + 3 stale `user_version == 2` assertions), all traceable to 05-01's migration.

## What the fix does

`app/state/db.py` `run_migrations()` now follows SQLite's documented table-rebuild
procedure: toggle `PRAGMA foreign_keys=OFF` + `PRAGMA legacy_alter_table=ON` **around**
the migration loop (both pragmas are no-ops inside a transaction, so they sit outside the
per-migration BEGIN/COMMIT), and run `PRAGMA foreign_key_check` **inside** each migration's
transaction so a genuine FK violation fails it closed.

Verified end-to-end: after migration the `staged_files` FK resolves to `items`, the insert
succeeds, and the FK is still enforced (orphan insert rejected). Three stale
`test_state_repo.py` assertions were updated from `user_version == 2` to `== 3` (head is 3
after migration_0003).

**Full suite after fix: 251 passed, 0 failed.**

## Process note

This is recorded because two executor agents mislabeled an in-scope regression as
out-of-scope. Future Phase-5 executors: a `no such table: items_old` or any migration/FK
error is a **real regression to investigate**, never an out-of-scope deferral.
